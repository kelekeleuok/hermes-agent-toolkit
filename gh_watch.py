#!/usr/bin/env python3
"""gh_watch.py — 零成本 GitHub 观察器（纯脚本、无 LLM 调用，可挂 cron）。

盯四类信号，命中才通知（无命中静默退出）：
  ① 关注仓库的新 release
  ② 关注仓库的新 issue
  ③ 关键词搜索命中的 issue/PR（找别人的解决方案）
  ④ 你自己的未读通知（被 @ / 参与过的仓库有动静）

用法：
  ./gh_watch.py --seed     首次运行，只建基线不通知
  ./gh_watch.py            常规运行，命中即通知
  ./gh_watch.py --list     打印关注清单与已记录条目数

配置（全部可选，走环境变量或 state 文件）：
  GITHUB_TOKEN     GitHub 令牌（不设则匿名，60 次/时；设了 5000 次/时，且解锁通知/Discussions 搜索）
  GH_WATCH_STATE   state 文件路径，默认 ~/.local/state/gh-watch/state.json
  GH_WATCH_HOOK    命中时调用的命令（收到一个位置参数：通知正文）。不设则打印到 stdout
  GH_WATCH_REPOS   逗号分隔的仓库清单，覆盖 state 里的默认值

设计取向：**低频、静默、零成本**。没有新东西就不出声，避免"通知疲劳"。
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

HOME = os.path.expanduser("~")
STATE = os.environ.get("GH_WATCH_STATE", os.path.join(HOME, ".local/state/gh-watch/state.json"))
HOOK = os.environ.get("GH_WATCH_HOOK", "")
API = "https://api.github.com"
UA = {"Accept": "application/vnd.github+json", "User-Agent": "gh-watch"}

DEFAULT_REPOS = ["ollama/ollama", "BerriAI/litellm", "langgenius/dify", "vllm-project/vllm",
                 "deepseek-ai/deepseek-harness"]
DEFAULT_QUERIES = [
    "prompt+caching+cost+in:title",
    "context+compression+token+cost+in:title",
    "deepseek+cache+hit+in:title",
]
MAX_PUSH = 6

TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
if TOKEN:
    UA["Authorization"] = "Bearer " + TOKEN


def gh(path):
    req = urllib.request.Request(API + path, headers=UA)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


GQL = ("query($q:String!){search(query:$q,type:DISCUSSION,first:5)"
       "{nodes{... on Discussion{title url repository{nameWithOwner}}}}}")

# Discussions 只能通过 GraphQL 搜索，且必须带令牌
def gql_search(q):
    if not TOKEN:
        return []
    req = urllib.request.Request(
        API + "/graphql",
        headers={"Authorization": "Bearer " + TOKEN, "User-Agent": "gh-watch",
                 "Content-Type": "application/json"},
        data=json.dumps({"query": GQL, "variables": {"q": q}}).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.load(r)
    return (((d.get("data") or {}).get("search") or {}).get("nodes") or [])


def load():
    if os.path.exists(STATE):
        st = json.load(open(STATE))
    else:
        st = {"seen": {}, "seeded": False}
    st.setdefault("repos", [r.strip() for r in os.environ.get("GH_WATCH_REPOS", "").split(",") if r.strip()]
                  or DEFAULT_REPOS)
    st.setdefault("queries", DEFAULT_QUERIES)
    return st


def save(st):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(st, open(STATE, "w"), ensure_ascii=False, indent=2)


def mark(st, key):
    """记下已见条目；返回 True 表示这是新条目。"""
    if key in st["seen"]:
        return False
    st["seen"][key] = time.strftime("%F %T")
    return True


def notify(title, lines):
    body = "%s\n\n%s" % (title, "\n".join("- " + l for l in lines))
    if HOOK:
        try:
            subprocess.run([HOOK, body], capture_output=True, text=True, timeout=60)
            return
        except Exception as e:
            print("[gh_watch] hook 失败：%s" % e, file=sys.stderr)
    print(body)


def collect(st):
    hits = []
    # ④ 自己的未读通知
    if TOKEN:
        try:
            for n in gh("/notifications?per_page=20"):
                if mark(st, "ntf:%s" % n["id"]):
                    hits.append("[通知] %s %s：%s" % (n["repository"]["full_name"],
                                                     n["subject"]["type"],
                                                     n["subject"]["title"][:70]))
        except Exception:
            pass
    # ①② 关注仓库的 release / issue
    for repo in st["repos"]:
        try:
            rel = gh("/repos/%s/releases/latest" % repo)
            if rel.get("tag_name") and mark(st, "rel:%s:%s" % (repo, rel["tag_name"])):
                hits.append("%s 新版本 `%s`：%s" % (repo, rel["tag_name"], (rel.get("name") or "")[:60]))
        except Exception:
            pass
        try:
            for it in gh("/repos/%s/issues?state=open&sort=created&direction=desc&per_page=10" % repo):
                if "pull_request" in it:
                    continue
                if mark(st, "iss:%s:%s" % (repo, it["number"])):
                    hits.append("%s#%d [新issue] %s" % (repo, it["number"], it["title"][:70]))
        except Exception:
            pass
        time.sleep(1)          # 匿名限速下留出余量
    # ③ 关键词搜索（issue/PR）
    for q in st["queries"]:
        try:
            d = gh("/search/issues?q=%s&sort=updated&order=desc&per_page=5" % q)
            for it in d.get("items", []):
                if mark(st, "q:%s" % it["id"]):
                    hits.append("%s（%s）" % (it["title"][:70], it["html_url"]))
        except Exception:
            pass
        time.sleep(6)          # search 接口限速更严（认证 30 次/分）
    # ③b Discussions（需令牌）
    for q in st["queries"]:
        try:
            for n in gql_search(q):
                if mark(st, "disc:%s" % n["url"]):
                    hits.append("[讨论] %s：%s" % (n["repository"]["nameWithOwner"], n["title"][:70]))
        except Exception:
            pass
        time.sleep(3)
    return hits


def main():
    st = load()
    if "--list" in sys.argv:
        print("仓库:", ", ".join(st["repos"]))
        print("搜索:", ", ".join(st["queries"]))
        print("已记录条目:", len(st["seen"]))
        return 0
    hits = collect(st)
    if not st.get("seeded") or "--seed" in sys.argv:
        st["seeded"] = True
        save(st)
        print("[seed] 建立基线，记录 %d 条，不通知" % len(hits))
        return 0
    if hits:
        notify("GitHub 观察 %s（%d 条）" % (time.strftime("%F %H:%M"), len(hits)), hits[:MAX_PUSH])
    save(st)
    print("命中 %d 条，已通知" % len(hits) if hits else "无新命中，静默退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
