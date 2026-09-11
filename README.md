# hermes-agent-toolkit

Agent 工具集（**中转稿，未发布**）。

## gh_watch.py
零 token 的 GitHub 观察器：盯指定仓库的 release / issue ＋ 关键词搜索（含 Discussions），
命中才推飞书，无命中静默退出。纯脚本，可挂 cron。

```bash
python3 gh_watch.py --seed   # 建基线，不推送
python3 gh_watch.py          # 常规运行，命中即告警
python3 gh_watch.py --list   # 看关注清单
```

配置：令牌读 `~/.hermes/.env` 的 `GITHUB_TOKEN`（可选，未配置走匿名 60 次/时）；
状态落 `~/.hermes/state/gh_watch.json`。
