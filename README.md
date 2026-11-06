# wechat-extract-skill

把 **macOS 微信 4.x** 的本地加密数据库解密，并导出成可读的 Markdown 归档。

同时是一个 [Claude Code](https://claude.com/claude-code) skill，也可以脱离 Claude Code 直接当命令行脚本用。

> **仅用于导出你自己设备上、你自己账号的聊天记录。** 密钥来自本机，数据库来自本机。

---

## 为什么需要这个

微信 4.x 用腾讯自家的 WCDB（SQLCipher 的 fork）加密本地数据库。网上绝大多数教程会让你用标准 `sqlcipher` 去试：

```bash
sqlcipher message_0.db "PRAGMA key = \"x'<64位hex>'\"; SELECT count(*) FROM sqlite_master;"
```

**这条路走不通**，会得到 `file is not a database`。然后你会开始穷举 `cipher_page_size`、`cipher_hmac_algorithm`、`cipher_compatibility`……全部无效。

原因：**那 32 字节密钥不是 SQLCipher 的原生密钥，而是 PBKDF2 的口令。**

```
encKey  = PBKDF2-HMAC-SHA512(key, salt, 256000, 32)
macSalt = salt XOR 0x3a
macKey  = PBKDF2-HMAC-SHA512(encKey, macSalt, 2, 32)
```

页面布局 4096 字节：`[密文数据][IV 16][HMAC-SHA512 64]`，第 0 页前 16 字节是 salt 明文。

本项目的解密输出与 `chatlog` 的 darwin v4 解密器**字节一致**。

## 安装

```bash
git clone https://github.com/Roger-Lv/wechat-extract-skill.git ~/.claude/skills/wechat-export
cd ~/.claude/skills/wechat-export
bash scripts/setup.sh          # 建私有 venv（cryptography + zstandard）
```

## 快速开始

```bash
SKILL=~/.claude/skills/wechat-export

# 1. 侦察：微信版本、账号、库布局、工具链
bash $SKILL/scripts/recon.sh

# 2. 验证密钥（拿到密钥后第一件事，别跳过）
$SKILL/.venv/bin/python $SKILL/scripts/verify_key.py <64位hex密钥> \
    "<账号目录>/db_storage/session/session.db"

# 3. 解密整棵树
$SKILL/.venv/bin/python $SKILL/scripts/decrypt_wechat.py <密钥> --tree \
    "<账号目录>/db_storage" /tmp/wxout

# 4. 导出 Markdown
$SKILL/.venv/bin/python $SKILL/scripts/export_markdown.py \
    /tmp/wxout <输出目录> <你自己的wxid>

# 5. 核验归档是不是真的完整（别跳过，理由见下）
$SKILL/.venv/bin/python $SKILL/scripts/verify_export.py \
    /tmp/wxout <输出目录> <你自己的wxid>
```

账号目录形如：

```
~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<wxid>_<suffix>
```

## 密钥从哪来

64 位十六进制（32 字节），从**运行中的微信进程内存**提取。可用工具：

- [`chatlog`](https://github.com/sjzar/chatlog) — `chatlog key`（需微信在运行）
- `PyWxDump` / `wechat-dump-rs` 等

这串 64 位 hex 本身就是 PBKDF2 的**口令**，所以如果你已经拿到它（本机已有的提取结果，或别处保存过），直接喂给 `decrypt_wechat.py` 即可——**不需要 attach 进程，也不需要关 SIP**。实测 macOS + 微信 4.x 可用。

同一条路也有现成封装 [`weflow-cli`](https://www.npmjs.com/package/weflow-cli)：macOS 流程是先 `weflow-cli fav set-key --passphrase <口令>`，再 `weflow-cli init`。注意 `init` 本身只接受 `--path` / `--refresh` / `--test-missing-keys`，**没有 `--passphrase`**。⚠️ 该项目官方标注支持 Windows/Linux、**macOS 未测试**，本仓库也没有实测过它——用前自行验证。

本机 `xwechat_files/all_users/login/<wxid>/key_info.db` 是明文 SQLite，里面存了包装过的密钥（`LoginKeyInfoTable.key_info_data`，180 字节 blob），但还套了一层加密，不能直接读。

## 微信 4.x 存储的四个坑

导出脚本已处理，但你若自己写代码必须知道：

| # | 坑 | 后果 |
|---|---|---|
| 1 | `local_type` **高位塞了子类型**（`244813135921 & 0xFFFFFFFF = 49`） | 不掩码 → 大量 `[消息类型 244813135921]` |
| 2 | `message_content` **可能是 zstd 压缩的**（魔数 `28 B5 2F FD`） | 不解压 → 大量 `[二进制内容]` |
| 3 | 群聊文本存成 **`<发送者wxid>:\n<正文>`** | 不剥前缀 → 正文里混着原始 wxid |
| 4 | app 消息（type 49）是 **XML** | 不解析 → 丢失链接标题、小程序名、引用内容 |

## 表结构

```sql
-- 每个会话一张表，表名 = "Msg_" + md5(会话id)
CREATE TABLE Msg_<md5>(
  local_id, server_id, local_type, sort_seq, real_sender_id,
  create_time, status, message_content, packed_info_data, ...
);
-- real_sender_id → Name2Id.rowid → user_name
```

联系人昵称在 `contact.db` 的 `contact(username, remark, nick_name, alias)`，优先 `remark`。

## 核验：归档真的完整吗

```bash
$SKILL/.venv/bin/python $SKILL/scripts/verify_export.py /tmp/wxout <输出目录> <你自己的wxid>
```

逐会话把源库重新数一遍，与归档里每个 `.md` 头部写的条数对比，对不上的点名，退出码非 0。核验脚本直接 `import export_markdown`，用的是同一套过滤规则，所以两边不会各算各的。

**为什么值得单独跑一步**：这个失败模式是**静默**的。少几条不会报错、不会写日志，产出的 `.md` 结构完好，导出脚本自己打印的合计也只是"它读到的那部分"的合计——没有核验，你分不出一份完整的归档和一份少了 0.007% 的归档。

实测过一次：一轮全量导出里 9 个会话各少 1–30 条（约占总量 0.007%），全程零报错；**重跑同一批会话即恢复**。根因是读到的库快照不含 `-wal` 里尚未 checkpoint 的数据。

对不上时：完全退出微信 → 重跑那几个会话 → 再核验。

它还会报出另外两类容易漏掉的情况：**整个消息表因为没有名字映射被跳过**（会话在 `contact.db`/`session.db` 里找不到），以及**归档里有源库中不存在的文件**（旧归档残留）。

**如果卡在「N 个消息表没有名字映射」上**：那是删掉联系人之后留下的孤儿会话。默认跳过它们，核验就会一直退出 1。给导出和核验都加上 `--include-unmapped`，它们会按表名导出，核验就干净了：

```bash
$SKILL/.venv/bin/python $SKILL/scripts/export_markdown.py /tmp/wxout <输出目录> <wxid> --include-unmapped
$SKILL/.venv/bin/python $SKILL/scripts/verify_export.py /tmp/wxout <输出目录> <wxid> --include-unmapped
```

## 局限

- **只有本机现存的数据。** 微信 4.0 迁移之前的历史在旧版 3.x 目录里，**用的是另一个密钥**，开不了。
- **媒体文件（图片/语音/视频）不导出**，只留占位符。媒体是独立的加密 `.dat`，另有解密流程。
- **WAL 未处理。** 解密读的是主库文件，不含 `-wal` 里尚未 checkpoint 的数据。导出前先退出微信让它正常 checkpoint，否则会**静默**少掉最近的消息。第 5 步的核验能查出是哪些会话少了。
- 表情、语音等非文本消息只能给类型标签。

## 排错

| 现象 | 原因 |
|---|---|
| `file is not a database` | 用了 `x'...'` 原生密钥语法。密钥要走 PBKDF2 |
| HMAC 不匹配 | 密钥不对，或拿的是另一个微信版本的密钥 |
| 解密"成功"但输出 0 字节 | chatlog 在密钥错时也会留下空文件。用 `decrypt_wechat.py` |
| `unsupported platform: macos v4` | chatlog 的 CLI 接受 `macos` 但内部工厂只认 `darwin` |
| 归档比源库少几条，但零报错 | 读到的是不含 `-wal` 的快照。退出微信 → 重跑该会话 → 再核验 |
| 整个会话没导出，也没有报错 | 它的 `Msg_<md5>` 表在 `contact.db`/`session.db` 里找不到名字映射，被静默跳过。核验脚本会点名；加 `--include-unmapped` 可按表名强行导出 |
| 容器路径访问极慢/卡住 | macOS TCC。给终端开「完全磁盘访问权限」 |

## 安全

- 解密产物是**你全部聊天记录的明文**。别放共享目录，用完及时删。
- 密钥等同于你的聊天记录，别贴进聊天窗口、issue、日志。
- **绝对不要把导出归档提交进 Git。**

## License

MIT
