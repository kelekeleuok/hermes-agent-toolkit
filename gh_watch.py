#!/usr/bin/env python3
"""gh_watch.py — GitHub 观察器（纯脚本、零 token，可挂 cron）。

盯两类信号，命中才推飞书（无命中静默退出）：
  ① 关注仓库的新 release / 新 issue（如 ollama / litellm / dify / vllm）
  ② 关键词搜索的新条目（找别人的解决方案：提示缓存计费、上下文压缩、token 成本）

用法：
  gh_watch.py --seed          首次运行，只记录基线不推送
  gh_watch.py                常规运行，命中即推飞书
  gh_watch.py --list          只打印当前关注清单与状态
状态：/root/.hermes/state/gh_watch.json
"""
import json, os, subprocess, sys, time, urllib.parse, urllib.request

STATE = "/root/.hermes/state/gh_watch.json"
API = "https://api.github.com"
UA = {"Accept": "application/vnd.github+json", "User-Agent": "hermes-gh-watch"}
ENV_FILE = "/root/.hermes/.env"


def token():
    try:
        for l in open(ENV_FILE):
            if l.startswith("GITHUB_TOKEN="):
                return l.strip().split("=", 1)[1]
    except Exception:
        pass
    return None


TOKEN = token()
if TOKEN:
    UA["Authorization"] = "Bearer " + TOKEN

DEFAULT_REPOS = ["ollama/ollama", "BerriAI/litellm", "langgenius/dify", "vllm-project/vllm",
                 "deepseek-ai/deepseek-harness"]
DEFAULT_QUERIES = [
    "prompt+caching+cost+in:title",
    "context+compression+token+cost+in:title",
    "deepseek+cache+hit+in:title",
]
MAX_PUSH = 6


def gh(path):
    req = urllib.request.Request(API + path, headers=UA)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


GQL = """query($q:String!){search(query:$q,type:DISCUSSION,first:5){nodes{... on Discussion{title url repository{nameWithOwner}}}}}"""


def gql_search(q):
    req = urllib.request.Request(API + "/graphql",
                                 headers={"Authorization": "Bearer " + TOKEN,
                                          "User-Agent": "hermes-gh-watch",
                                          "Content-Type": "application/json"},
                                 data=json.dumps({"query": GQL, "variables": {"q": q}}).encode(),
                                 method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.load(r)
    return (((d.get("data") or {}).get("search") or {}).get("nodes") or [])


def load():
    if os.path.exists(STATE):
        return json.load(open(STATE))
    return {"repos": DEFAULT_REPOS, "queries": DEFAULT_QUERIES,
            "seen": {}, "seeded": False, "fails": 0}


def save(st):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(st, open(STATE, "w"), ensure_ascii=False, indent=2)


HERMES_BIN = "/root/.hermes/hermes-agent/venv/bin/hermes"


def push(title, lines):
    body = "**%s**\n\n%s" % (title, "\n".join("- " + l for l in lines))
    try:
        subprocess.run([HERMES_BIN, "send", "-t", "feishu", body],
                       capture_output=True, text=True, timeout=60)
    except Exception as e:
        print("[gh_watch] 飞书通知失败：%s" % e, file=sys.stderr)


def collect(st):
    hits = []
    for repo in st["repos"]:
        try:
            rel = gh("/repos/%s/releases/latest" % repo)
            key = "rel:%s:%s" % (repo, rel.get("tag_name"))
            if key not in st["seen"] and rel.get("tag_name"):
                st["seen"][key] = time.strftime("%F %T")
                hits.append("%s 新版本 `%s`：%s" % (repo, rel["tag_name"], (rel.get("name") or "")[:60]))
        except Exception as e:
            pass
        try:
            iss = gh("/repos/%s/issues?state=open&sort=created&direction=desc&per_page=10" % repo)
            for it in iss:
                key = "iss:%s:%s" % (repo, it["number"])
                if key in st["seen"] or "pull_request" in it:
                    continue
                st["seen"][key] = time.strftime("%F %T")
                hits.append("%s#%d [新issue] %s" % (repo, it["number"], it["title"][:70]))
        except Exception as e:
            pass
        time.sleep(1)
    for q in st["queries"]:
        try:
            d = gh("/search/issues?q=%s&sort=updated&order=desc&per_page=5" % q)
            for it in d.get("items", []):
                key = "q:%s" % it["id"]
                if key in st["seen"]:
                    continue
                st["seen"][key] = time.strftime("%F %T")
                hits.append("%s（%s）" % (it["title"][:70], it["html_url"]))
        except Exception:
            pass
        time.sleep(6)
    if TOKEN:
        for q in st["queries"]:
            try:
                for n in gql_search(q):
                    key = "disc:%s" % n["url"]
                    if key in st["seen"]:
                        continue
                    st["seen"][key] = time.strftime("%F %T")
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
    first = not st.get("seeded")
    st["seeded"] = True
    if first or "--seed" in sys.argv:
        save(st)
        print("[seed] 建立基线，记录 %d 条，不推送" % len(hits))
        return 0
    if hits:
        push("GitHub 观察 %s（%d 条）" % (time.strftime("%F %H:%M"), len(hits)), hits[:MAX_PUSH])
    save(st)
    print(hits and ("命中 %d 条，已推飞书" % len(hits)) or "无新命中，静默退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
