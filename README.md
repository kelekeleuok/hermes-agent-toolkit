# hermes-agent-toolkit

Agent 工具集（**中转稿，未发布**）。定位：把长期跑 LLM agent 时踩到的坑，沉淀成别人能直接抄的写法和脚本。

## docs/

| 文档 | 内容 |
|:--|:--|
| [coremail-imap-pitfalls.md](docs/coremail-imap-pitfalls.md) | 国内 Coremail 系邮箱 IMAP 的五个**静默坑**：SEARCH 返回的是序号不是 UID、UID COPY 不回 COPYUID、UID STORE/EXPUNGE 不生效、不支持 MOVE、readonly 静默吞 STORE —— 症状统一是「命令返回 OK 但什么都没发生」 |

## scripts/

| 脚本 | 说明 |
|:--|:--|
| [mail_archive.py](scripts/mail_archive.py) | 邮箱分类归档器（`--dry-run` 出计划 / `--apply` 执行）。设计要点：**指纹核验 + 删源**保证守恒、幂等可重跑、中文夹名走 modified UTF-7 |

### 用法
```bash
export IMAP_USER='you@126.com' IMAP_AUTHCODE='授权码'
python3 scripts/mail_archive.py --dry-run
python3 scripts/mail_archive.py --apply
```

### 设计原则（跨邮箱通用）
1. **不信返回值，只信回读**：有副作用的操作一律用回读验证；
2. **先投递、核验通过后再删源**，顺序绝不颠倒；
3. **幂等**：重跑不重复搬、不误删（靠 Message-ID 指纹）。

## 许可
MIT。AS IS，无担保。
