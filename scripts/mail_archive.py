#!/usr/bin/env python3
"""mail_archive.py — Coremail 系邮箱分类归档器（收件箱瘦身）。

规则（按 from+subject 判定，先匹配先赢）：
  INBOX 保留：DeepSeek 计费信、账户安全事件（改密/passkey/SSH/OAuth/登录提醒）、
             微软条款、超大附件到期、自投存档
  归档 50-营销订阅 ：Ollama 营销、Docker 营销、网易会员营销、Copilot 营销
  归档 60-验证码   ：各类验证码/验证邮箱（飞书/Google/微软/麦蕊智数/DeepSeek 验证码）
  归档 70-登录验证 ：GitHub "verify your device"、iKuuu VPN、Docker 账号验证/重置

Coremail 三个坑（已踩实）：
  1. 中文夹名必须 IMAP modified UTF-7 编码，否则 SELECT 失败；
  2. `SEARCH` 返回的是**序号**，真实 UID 必须走 `UID SEARCH`；
  3. `UID STORE / UID EXPUNGE` 被静默忽略，删除只能用**序号版** `STORE + EXPUNGE`；
     且必须先 `select(readonly=False)`，否则 STORE 也被静默忽略。
  投递用 `UID COPY`（会回 COPYUID，可校验）；删除在**投递全部完成后一次性**做，
  避免中途 EXPUNGE 使序号漂移。

用法：
  python3 mail_archive.py --dry-run     # 只出计划
  python3 mail_archive.py --apply       # 建夹 + 移动 + 回读校验
"""
from __future__ import annotations

import argparse
import base64
import email
import email.header
import re
import sys
import time

import os

def connect():
    """连接 IMAP（凭据走环境变量，勿硬编码）。"""
    import imaplib
    host = os.environ.get("IMAP_HOST", "imap.126.com")
    user = os.environ["IMAP_USER"]           # 如 kelele***@126.com
    code = os.environ["IMAP_AUTHCODE"]       # 授权码，非登录密码
    M = imaplib.IMAP4_SSL(host, 993)
    M.login(user, code)
    M._simple_command("ID", '("name" "mail-archive" "version" "1.0")')
    M._untagged_response("OK", [None], "ID")
    return M


FOLDERS = ["50-营销订阅", "60-验证码", "70-登录验证"]


def mutf7(s: str) -> str:
    """IMAP modified UTF-7（Coremail 中文夹名必须编码，否则 SELECT 失败）。"""
    out, buf = [], []

    def flush():
        if buf:
            b = "".join(buf).encode("utf-16-be")
            out.append("&" + base64.b64encode(b).decode().rstrip("=").replace("/", ",") + "-")
            buf.clear()

    for ch in s:
        if ch == "&":
            flush()
            out.append("&-")
        elif 0x20 <= ord(ch) <= 0x7E:
            flush()
            out.append(ch)
        else:
            buf.append(ch)
    flush()
    return "".join(out)


def classify(frm: str, subj: str) -> str:
    f, s = frm.lower(), subj.lower()
    # 保留：DeepSeek 计费
    if "sc.mail.deepseek.com" in f:
        return "INBOX"
    # 保留：账户安全事件
    if any(k in s for k in ["password has changed", "password was reset", "passkey was added",
                            "ssh authentication public key", "oauth application has been added",
                            "github application has been added", "使用条款更新"]):
        return "INBOX"
    if "safe@service.netease.com" in f or "mail@service.netease.com" in f:
        return "INBOX"
    if "超大附件" in subj:
        return "INBOX"
    if f.startswith("hill") and "数据研究院" in subj:
        return "INBOX"
    # 验证码类
    if any(k in s for k in ["验证码", "验证邮件", "验证你的电子邮件地址", "验证您的电子邮件地址",
                            "验证您的邮箱地址", "launch code", "one-time verification",
                            "reset password request"]):
        return "60-验证码"
    # 登录验证类
    if "verify your device" in s or "ikuuu" in f:
        return "70-登录验证"
    # 营销
    if any(k in f for k in ["ollama.com", "docker.com", "member@service.netease.com",
                            "club@service.netease.com"]):
        return "50-营销订阅"
    if "copilot" in s:
        return "50-营销订阅"
    # 兜底：安全优先，留 INBOX
    return "INBOX"


def _dec(v: str | None) -> str:
    if not v:
        return ""
    parts = email.header.decode_header(v)
    return "".join(p.decode(c or "utf-8", "replace") if isinstance(p, bytes) else p for p, c in parts)


def key_of(m: dict) -> tuple:
    return (m["from"], m["subject"], m["date"])


def enumerate_box(M, box: str = "INBOX") -> list[dict]:
    """一次 FETCH 同时拿到 (序号, 真实 UID, from, subject, date)。"""
    M.select(box)
    typ, data = M.fetch("1:*", "(UID BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
    out = []
    for item in data:
        if not isinstance(item, tuple):
            continue
        head, body = item[0].decode(errors="replace"), item[1]
        m = re.match(r"\s*(\d+)\s+\(UID\s+(\d+)", head)
        if not m:
            continue
        msg = email.message_from_bytes(body)
        out.append({"seq": m.group(1), "uid": m.group(2),
                    "from": _dec(msg.get("From")), "subject": _dec(msg.get("Subject")),
                    "date": _dec(msg.get("Date"))})
    return out


def inbox_count(M) -> int:
    M.select("INBOX")
    typ, d = M.uid("SEARCH", None, "ALL")
    return len(d[0].split()) if d and d[0] else 0


def box_count(M, box: str) -> int:
    M.select(box)
    typ, d = M.uid("SEARCH", None, "ALL")
    return len(d[0].split()) if d and d[0] else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    if not (a.dry_run or a.apply):
        ap.error("需 --dry-run 或 --apply")

    M = connect()
    msgs = enumerate_box(M, "INBOX")
    total0 = len(msgs)
    plan: dict[str, list[dict]] = {}
    for m in msgs:
        plan.setdefault(classify(m["from"], m["subject"]), []).append(m)

    print(f"=== 归档计划（INBOX 共 {total0} 封）===")
    for k in sorted(plan):
        print(f"[{k}] {len(plan[k])} 封")
        for m in plan[k][:5]:
            print(f"    {m['uid']:>10} | {m['from'][:32]:<32} | {m['subject'][:52]}")
        if len(plan[k]) > 5:
            print(f"    … 另 {len(plan[k]) - 5} 封")
    if a.dry_run:
        M.logout()
        return 0

    # 建夹（幂等）
    typ, boxes = M.list()
    existing = b" ".join(boxes).decode(errors="replace")
    for name in FOLDERS:
        if mutf7(name) in existing:
            print(f"夹已存在：{name}")
        else:
            print(f"建夹 {name}: {M.create(mutf7(name))[0]}")

    from collections import Counter

    # 幂等：目标夹已有同一封信（指纹一致）就不重复投递
    have = Counter()
    for name in FOLDERS:
        for m in enumerate_box(M, mutf7(name)):
            have[(name, key_of(m))] += 1

    todo = [(name, m) for name in FOLDERS for m in plan.get(name, [])]
    sent = skipped = 0
    for name, m in todo:
        k = (name, key_of(m))
        if have[k] > 0:
            have[k] -= 1
            skipped += 1
            continue
        typ, _ = M.uid("COPY", m["uid"], mutf7(name))  # Coremail 不回 COPYUID，只认 OK
        sent += typ == "OK"
        time.sleep(0.03)
    print(f"\n投递 {sent} 封、跳过（已在目标夹）{skipped} 封，共 {len(todo)} 封")

    # —— 核验 + 删源：按信件指纹多重集比对，确认已落地才删源（防丢信） ——
    inbox_msgs = enumerate_box(M, "INBOX")
    avail = Counter()
    for name in FOLDERS:
        for m in enumerate_box(M, mutf7(name)):
            avail[(name, key_of(m))] += 1
    del_seqs, unmatched = [], []
    for m in inbox_msgs:
        tgt = classify(m["from"], m["subject"])
        if tgt == "INBOX":
            continue
        if avail[(tgt, key_of(m))] > 0:
            avail[(tgt, key_of(m))] -= 1
            del_seqs.append(m["seq"])
        else:
            unmatched.append((tgt, m["uid"], m["subject"][:40]))
    if del_seqs:
        M.select("INBOX")
        M.store(",".join(del_seqs), "+FLAGS", "\\Deleted")
        M.expunge()
        time.sleep(0.8)
    print(f"核验通过并删源 {len(del_seqs)} 封" + (f"；未匹配 {len(unmatched)} 封" if unmatched else ""))
    for u in unmatched[:10]:
        print("   未匹配:", u)

    # 回读校验
    print("\n=== 回读校验 ===")
    tot = 0
    for name in FOLDERS:
        c = box_count(M, mutf7(name))
        tot += c
        print(f"{name}: {c} 封")
    inb = inbox_count(M)
    print(f"INBOX: {inb} 封；夹内合计 {tot}；总计 {inb + tot}（原 {total0}）")
    print("✅ 守恒" if inb + tot == total0 else "❌ 邮件数不守恒，需人工核查")
    M.logout()
    return 0


if __name__ == "__main__":
    sys.exit(main())
