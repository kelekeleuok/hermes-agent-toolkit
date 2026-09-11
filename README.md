# agent-toolkit

**Practical notes and small tools from running LLM agents in production.**

Real-world pitfalls we hit, written up so you don't have to hit them too.
每条都是从实际运行中踩出来的坑，写成可以直接抄的形式。

> Status: early / 早期。内容会随实践继续补。

---

## Contents / 目录

### `docs/` — write-ups / 实录

| Doc | 内容 |
|:--|:--|
| [coremail-imap-pitfalls.md](docs/coremail-imap-pitfalls.md) | **Five silent IMAP pitfalls of Coremail-based mailboxes** (126 / 163 / enterprise mail). The symptom is always the same: *the command returns OK but nothing happened*. Covers `SEARCH` returning sequence numbers instead of UIDs, `UID COPY` not returning `COPYUID`, `UID STORE/EXPUNGE` being silently ignored, no `MOVE` support, and `select(readonly=True)` swallowing subsequent `STORE`. 国内 Coremail 系邮箱的五个静默坑。 |

### `scripts/` — tools / 工具

| Script | 说明 |
|:--|:--|
| [gh_watch.py](gh_watch.py) | **Zero-cost GitHub watcher.** Pure script, no LLM calls: watches releases/issues of chosen repos, keyword searches (issues + Discussions), and your own notifications. Silent when nothing new. 零成本 GitHub 观察器，无命中静默退出。 |
| [mail_archive.py](scripts/mail_archive.py) | **Mailbox classifier/archiver.** Moves mail into folders by rule, with an idempotent design and a conservation check (`--dry-run` / `--apply`). 邮箱分类归档器。 |

---

## Design principles / 贯穿的设计原则

These come up in every tool here:

1. **Trust read-back, not return codes.** On some servers "OK" means nothing. Verify side effects by re-reading state — never by trusting the response.
   **不信返回值，只信回读。**
2. **Copy first, verify, delete last.** Never remove the source before the destination is confirmed. Order is not negotiable.
   **先投递、核验通过再删源，顺序不可颠倒。**
3. **Idempotent by default.** Re-running must not duplicate, double-move, or lose anything. Fingerprints (`Message-ID`, tag names, issue numbers) — not positional indexes.
   **幂等：重跑不重复、不误删。**
4. **Low frequency, silent by default.** Watchers that shout every run get muted by week two. No news = no message.
   **低频、静默；没有新东西就不出声。**
5. **No secrets in code.** Tokens and credentials come from the environment.
   **凭据只走环境变量。**

---

## `gh_watch.py` usage

```bash
export GITHUB_TOKEN='ghp_...'      # optional; without it you get 60 req/h anonymously
export GH_WATCH_HOOK='/path/to/notifier'   # optional; gets the message body as argv[1]

python3 gh_watch.py --seed    # first run: build baseline, don't notify
python3 gh_watch.py           # regular run: notify only on new hits
python3 gh_watch.py --list    # show watched repos + counter
```

Cron example (every 3 hours, off-peak friendliness):
```cron
15 */3 * * * GITHUB_TOKEN=... /usr/bin/python3 /path/to/gh_watch.py >> ~/gh_watch.log 2>&1
```

Environment variables: `GITHUB_TOKEN`, `GH_WATCH_STATE` (state file path),
`GH_WATCH_HOOK` (notification command), `GH_WATCH_REPOS` (comma-separated repos).

## `mail_archive.py` usage

```bash
export IMAP_USER='you@126.com'
export IMAP_AUTHCODE='...'        # authorization code, not your login password
python3 scripts/mail_archive.py --dry-run   # print the plan
python3 scripts/mail_archive.py --apply     # create folders, move, verify
```

---

## License

MIT — see [LICENSE](LICENSE). Provided AS IS, without warranty of any kind.
