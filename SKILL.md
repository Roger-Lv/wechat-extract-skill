---
name: wechat-export
description: 导出 macOS 微信本地聊天记录为 Markdown 归档,并核验归档是否完整(逐会话比对源库行数)。解密微信 4.x 的 WCDB/SQLCipher 数据库(db_storage 下的 message_*.db、contact.db、session.db),处理 zstd 压缩、local_type 高位子类型、群聊前缀、app 消息 XML 等存储细节,按会话导出。当用户提到导出微信聊天记录、解密微信数据库、微信聊天备份、微信归档、db_storage、msg_*.db、提取聊天记录、chatlog、WCDB、核验导出完整性、归档少了几条时使用。
---

# WeChat Export (macOS)

把 macOS 微信本地加密数据库解密并导出成可读的 Markdown 归档。

**仅用于导出你自己设备上、你自己账号的聊天记录。** 密钥来自本机，数据库来自本机，导出的是你自己的数据。

## 核心认知（先读这段，能省几小时）

**微信 4.x 的 32 字节密钥不是 SQLCipher 原生密钥，而是 PBKDF2 的口令。**

这是最容易踩的坑。用标准 sqlcipher CLI 试 `PRAGMA key = "x'<hex>'"` 会得到 `file is not a database`，然后你会以为密钥错了、去穷举 `cipher_page_size` / `cipher_hmac_algorithm` / `cipher_compatibility`——**全是无用功**，因为密钥压根没进 PBKDF2。

正确的派生（微信 4.x / WCDB）：

```
encKey  = PBKDF2-HMAC-SHA512(key, salt, 256000, 32)
macSalt = salt XOR 0x3a
macKey  = PBKDF2-HMAC-SHA512(encKey, macSalt, 2, 32)
```

页面布局（4096 字节）：`[密文数据][IV 16][HMAC-SHA512 64]`，第 0 页前 16 字节是 salt 明文。

验证密钥只要一步——比对第 1 页的 HMAC：

```bash
.venv/bin/python scripts/verify_key.py <64位hex密钥> <某个.db>
# 匹配 => 密钥正确；不匹配 => 密钥不对（或属于另一个微信版本）
```

**先用 `verify_key.py` 确认密钥，再动手解密。** 不要跳过。

## 前置

```bash
bash scripts/setup.sh        # 建私有 venv(cryptography + zstandard)
bash scripts/recon.sh        # 侦察:版本、账号、库布局、工具链
```

## 流程

### 1. 侦察

```bash
bash scripts/recon.sh
```

确认三件事：微信版本、账号目录、以及**微信是否在运行**（解密前先退出，避免读到半写状态）。

数据位置：

| 版本 | 路径 |
|---|---|
| 4.x | `~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<wxid>_<suffix>/db_storage/` |
| 3.x（旧版） | `~/Library/Containers/com.tencent.xinWeChat/Data/Library/Application Support/com.tencent.xinWeChat/2.0b4.0.9/<hash>/` |

注意：**3.x 和 4.x 用不同的密钥**。4.x 的密钥开不了 3.x 的库，反之亦然。想导出旧版历史得另外提取一次密钥。

### 2. 拿到密钥

密钥是 64 位十六进制（32 字节），来自运行中的微信进程内存，或本机 `key_info.db`。常见来源：

- `chatlog key`（需微信在运行；macOS 上可能需临时关闭 SIP）
- PyWxDump / wechat-dump-rs 等同类工具
- 用户自己已有的提取结果

> **不想动 SIP 的话**：这串 64 位 hex 本身就是 PBKDF2 的**口令**，所以如果你已经拿到它（本机已有的提取结果、或别处保存过），直接喂给 `decrypt_wechat.py` 就行——不需要 attach 进程，也不需要关 SIP。实测 macOS + 微信 4.x 可用。
>
> 同一条路也有人封装好了：[`weflow-cli`](https://www.npmjs.com/package/weflow-cli)。macOS 流程是先 `weflow-cli fav set-key --passphrase <口令>`，再 `weflow-cli init`——`init` 本身只接受 `--path` / `--refresh` / `--test-missing-keys`，**没有 `--passphrase`**。⚠️ 该项目官方标注 Windows/Linux、**macOS 未测试**，本 skill 未实测过它，用前自行验证。

拿到后**先验证**：

```bash
.venv/bin/python scripts/verify_key.py <密钥> <db_storage/session/session.db>
```

### 3. 解密

```bash
# 单个文件
.venv/bin/python scripts/decrypt_wechat.py <密钥> <src.db> <dst.db>

# 整棵树(db_storage 下所有 .db)
.venv/bin/python scripts/decrypt_wechat.py <密钥> --tree \
    "<账号目录>/db_storage" /tmp/wxout
```

输出是标准 SQLite，可以直接 `sqlite3` 打开。与 chatlog 的 darwin v4 解密器**字节一致**。

> 备选：`chatlog decrypt -p darwin -v 4 -d <账号目录> -k <密钥> -w <工作目录>`
> 注意必须传 `darwin` 而不是 `macos`——CLI 接受 `macos` 但内部工厂只认 `darwin`，会报
> `unsupported platform: macos v4`。而且它对失败的文件会留下 0 字节的假输出文件。

### 4. 导出 Markdown

```bash
.venv/bin/python scripts/export_markdown.py <解密后的db_storage目录> <输出目录> <你自己的wxid>
```

输出：每个会话一个 `.md`，外加 `_index.md` 索引（按消息数排序）。

### 5. 核验（别跳过）

```bash
.venv/bin/python scripts/verify_export.py <解密后的db_storage目录> <输出目录> <你自己的wxid>
```

它把源库逐会话重新数一遍，与归档里每个 `.md` 头部写的条数对比，对不上的会点名，退出码非 0。核验脚本直接 `import export_markdown`，走同一套过滤规则，所以两边不会各算各的。它同时会报出三类问题：条数对不上、源库有消息但没有归档文件、以及**整个消息表因为没有名字映射被跳过**。

**为什么这一步不能省**：这个失败模式是**静默**的。少几条不会报错、不会写日志，产出的 `.md` 结构完好，导出脚本自己打印的合计也只是"它读到的那部分"的合计。没有核验，你分不出一份完整的归档和一份少了 0.007% 的归档。

实测过一次：一轮全量导出里 9 个会话各少 1–30 条（约占总量 0.007%），全程零报错；**重跑同一批会话即恢复**。根因是读到的库快照不含 `-wal` 里尚未 checkpoint 的数据——不是永久丢数据，但那一轮归档确实不完整。

对不上时的处理顺序：

1. 完全退出微信（让它 checkpoint）
2. 重跑那几个会话
3. 再核验一次

> 核验脚本本身也要有反向对照才可信：改掉一个 `.md` 的条数、或删掉一个 `.md`，它必须报错并点名。一个只会说 ✓ 的检查等于没有检查。

**如果卡在「N 个消息表没有名字映射」上**：那是删掉联系人后留下的孤儿会话，默认被跳过，于是核验永远退出 1、「归档完整」永远打不出来。给导出和核验**都**加上 `--include-unmapped`，它们会按表名（`未映射会话 <md5前8位>`）导出，核验随之干净。两边要同时加，否则核验会把多出来的归档文件报成「旧归档残留」。

## 微信 4.x 存储的四个坑

导出脚本已处理，但你若自己写代码必须知道：

1. **`local_type` 高位塞了子类型**。`244813135921 & 0xFFFFFFFF = 49`。不掩码会把 3.7 万条 app 消息显示成 `[消息类型 244813135921]`。
2. **`message_content` 可能是 zstd 压缩的**（魔数 `28 B5 2F FD`）。不解压会有大量 `[二进制内容]`。
3. **群聊文本存成 `<发送者wxid>:\n<正文>`**。要剥掉前缀再解析成昵称。
4. **app 消息（type 49）是 XML**。`<appmsg>` 里有 `<title>`、`<type>`、`<refermsg>`（引用）。`<type>57</type>` 是引用。

消息表结构：

```sql
-- 每个会话一张表，表名 = "Msg_" + md5(会话id)
CREATE TABLE Msg_<md5>(
  local_id, server_id, local_type, sort_seq, real_sender_id,
  create_time, status, message_content, ...
);
-- real_sender_id -> Name2Id.rowid -> user_name
```

联系人昵称：`contact.db` 的 `contact(username, remark, nick_name, alias)`，优先 `remark`。

## 常见故障

| 现象 | 原因 |
|---|---|
| `file is not a database` | 用 `x'...'` 原生密钥语法了。密钥要走 PBKDF2 |
| HMAC 不匹配 | 密钥不对，或拿的是另一个微信版本的密钥 |
| 解密成功但输出 0 字节 | 用了 chatlog 且密钥错——它会留下空文件。换 `decrypt_wechat.py` |
| `unsupported platform: macos v4` | chatlog 的 CLI/工厂命名不一致，传 `darwin` |
| 大量 `[二进制内容]` | 没做 zstd 解压 |
| 大量 `[消息类型 <大数>]` | 没对 `local_type` 做 `& 0xFFFFFFFF` |
| 归档比源库少几条，但零报错 | 读到的是不含 `-wal` 的快照。退出微信 → 重跑该会话 → 再核验（第 5 步） |
| 整个会话没导出，也没有报错 | 它的 `Msg_<md5>` 表在 `contact.db`/`session.db` 里找不到名字映射，被静默跳过。核验脚本会点名；加 `--include-unmapped` 可按表名强行导出 |
| 容器路径访问极慢/卡住 | macOS TCC。给终端开「完全磁盘访问权限」 |

## 局限

- **只有本机现存的数据**。微信 4.0 迁移之前的历史在 3.x 目录里，需要另一个密钥。
- **媒体文件（图片/语音/视频）不导出**，只留占位符。媒体是独立的加密 `.dat`，另有解密流程。
- **微信 4.0 的 WAL 未处理**。解密读的是主库文件，不含 `-wal` 里尚未 checkpoint 的数据。导出前先退出微信让它正常 checkpoint，否则会**静默**少掉最近的消息——第 5 步的核验能查出来是哪些会话。
- 表情、语音等非文本消息只能给类型标签。

## 安全

- 解密产物是**你全部聊天记录的明文**。别放共享目录，用完及时删。
- 密钥等同于你的聊天记录，别贴到聊天窗口、issue、日志里。
- 输出目录建议加进 `.gitignore`，**绝对不要把归档提交进 Git**。
