# 国内 Coremail 系邮箱 IMAP 的五个静默坑（126/163/企业邮实测）

> 适用：用 Python `imaplib` 或任意 IMAP 客户端对接 **Coremail** 架构的国内邮箱（126、163、企业邮、部分高校邮）。
> 症状都是同一个：**命令返回 OK，但什么都没发生**。本文是踩坑实录＋可直接抄的规避写法。

## 坑 1：`SEARCH` 返回的是「序号」，不是 UID

大多数 IMAP 服务端两套编号：**序号（sequence number，随邮件增删重排）** 和 **UID（永不重排）**。
跨会话、跨文件夹时只有 UID 能用。但 Coremail 上 `mail.search(None, 'ALL')` 返回的数字，
**看起来像 UID、实际是序号**——真 UID 是 10 位数（实测 ≈1.77e9）。

```python
# ❌ 错：把 SEARCH 结果当 UID 用 → 跨箱投递时全乱、投到别的文件夹
typ, data = M.search(None, 'ALL'); uids = data[0].split()

# ✅ 对：显式 UID SEARCH，再 FETCH 回读真实 UID
typ, data = M.uid('SEARCH', None, 'ALL')
real = []
for num in data[0].split():
    t, d = M.fetch(num, '(UID)')          # 用 UID 搜索拿到的仍是序号，必须回读
    real.append(int(re.findall(rb'UID (\d+)', d[0])[0]))
```

**判据**：如果你的 UID 是 4~5 位数，八成是序号被当成 UID 用了。

## 坑 2：`UID COPY` 成功但**不回 `COPYUID`**

RFC 4315 规定服务端应回 `COPYUID`（告知新箱里的 UID），据此可以校对投递结果。
Coremail **返回 OK 但不回 `COPYUID`** → 你无法确认"这封信到底进没进去"。

**规避**：放弃依赖服务端回执，改成 **「指纹核验 + 删源」**：
1. 目标箱 `FETCH` 全量，按 `Message-ID`（缺则 `Subject+Date+大小`）建指纹集合；
2. 投递后重扫目标箱，**指纹命中才算成功**；
3. 确认命中后才在原箱删除 —— 顺序绝不能颠倒。

## 坑 3：`UID STORE` / `UID EXPUNGE` **静默失效**

标记 `\Deleted` 和清理，Coremail 上**只认序号版**，UID 版返回 OK 却什么都不做：

```python
M.store(num, '+FLAGS', '\\Deleted')   # ✅ 序号版，有效
M.expunge()
# M.uid('STORE', uid, '+FLAGS', '\\Deleted')  ❌ 返回 OK，但标记没打上
```

**代价**：一旦生效顺序错了（先删源、后投递），邮件就永久丢了。所以坑 2 的核验顺序是硬要求。

## 坑 4：不支持 `MOVE`（RFC 6851）

```python
M.uid('MOVE', uid, 'Archive')   # ❌ BAD/不支持
```
只能退化成 **COPY → 核验 → STORE \Deleted → EXPUNGE** 的老办法（即上面三条的组合）。

## 坑 5：`select(readonly=True)` 会**静默吞掉后续所有 STORE**

这个最阴——不是服务端问题，是 `imaplib` 的行为：readonly 打开的信箱不许改状态，
后续 `STORE` 不报错、不生效，你以为删了其实没删，跑第二遍发现"归档数与回读数不符"。

```python
M.select('INBOX')            # ✅
# M.select('INBOX', readonly=True)  ❌ 之后的 STORE 全部无效
```

## 附：连不上 / `Unsafe Login`

网易系邮箱在 `LOGIN` 前会拒绝陌生客户端，需先发 RFC 2971 的 `ID` 命令上报客户端信息：

```python
M = imaplib.IMAP4_SSL('imap.126.com', 993)
M.login(user, authcode)        # 授权码，不是登录密码
M._simple_command('ID', '("name" "my-client" "version" "1.0")')
M._untagged_response('OK', [None], 'ID')
```

## 一句话总结

**在 Coremail 上，「返回 OK」不等于「做了」。** 一切有副作用的操作，都必须用**回读**来验证，
而不是相信返回值：投递靠指纹核验，删除靠重扫确认。

---
*实测环境：Python 3.12 + imaplib，2026-09。文中数字为实测值，不同部署版本可能有差异，
建议先用 `--dry-run` 模式验证。*
