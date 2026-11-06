---
name: wechat-export
description: 导出 macOS 微信本地聊天记录为 Markdown 归档。解密微信 4.x 的 WCDB/SQLCipher 数据库(db_storage 下的 message_*.db、contact.db、session.db),处理 zstd 压缩、local_type 高位子类型、群聊前缀、app 消息 XML 等存储细节,按会话导出。当用户提到导出微信聊天记录、解密微信数据库、微信聊天备份、微信归档、db_storage、msg_*.db、提取聊天记录、chatlog、WCDB 时使用。
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
| 容器路径访问极慢/卡住 | macOS TCC。给终端开「完全磁盘访问权限」 |

## 局限

- **只有本机现存的数据**。微信 4.0 迁移之前的历史在 3.x 目录里，需要另一个密钥。
- **媒体文件（图片/语音/视频）不导出**，只留占位符。媒体是独立的加密 `.dat`，另有解密流程。
- **微信 4.0 的 WAL 未处理**。导出前退出微信让 SQLite 正常 checkpoint，否则可能丢最近的消息。
- 表情、语音等非文本消息只能给类型标签。

## 安全

- 解密产物是**你全部聊天记录的明文**。别放共享目录，用完及时删。
- 密钥等同于你的聊天记录，别贴到聊天窗口、issue、日志里。
- 输出目录建议加进 `.gitignore`，**绝对不要把归档提交进 Git**。
