#!/usr/bin/env python3
"""Export decrypted WeChat 4.x databases to a Markdown archive.

Handles the quirks of WeChat 4.x storage:
  * local_type packs a sub-type into the high 32 bits -> mask to base type
  * message_content may be zstd-compressed (magic 28 B5 2F FD)
  * group text is stored as "<sender-wxid>:\\n<text>"
  * app messages (type 49) carry an XML payload with title/type/refermsg

Usage: export_markdown.py <decrypted-root> <out-dir> <self-wxid>
"""
import hashlib
import io
import os
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime

import zstandard as zstd

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
_dctx = zstd.ZstdDecompressor()

# base local_type -> label (types that carry no useful text)
TYPE_LABEL = {
    3: "[图片]", 34: "[语音]", 43: "[视频]", 47: "[表情]", 48: "[位置]",
    42: "[名片]", 50: "[语音通话]", 66: "[红包]", 67: "[转账]",
    10000: "[系统消息]", 10002: "[撤回了一条消息]",
}

# appmsg <type> -> label
APPMSG_LABEL = {
    5: "链接", 6: "文件", 33: "小程序", 36: "小程序", 44: "视频号",
    51: "视频号直播", 62: "视频号", 87: "群公告", 2000: "转账", 2001: "红包",
}

SENDER_PREFIX = re.compile(r"^([A-Za-z0-9_\-]+):\n")


def md5(s):
    return hashlib.md5(s.encode()).hexdigest()


def open_ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def maybe_decompress(raw):
    if not isinstance(raw, (bytes, bytearray)):
        return raw
    if raw[:4] == ZSTD_MAGIC:
        try:
            return _dctx.decompress(bytes(raw))
        except zstd.ZstdError:
            try:
                return _dctx.stream_reader(io.BytesIO(bytes(raw))).read()
            except zstd.ZstdError:
                return None
    return raw


def coerce_text(raw):
    raw = maybe_decompress(raw)
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        try:
            return bytes(raw).decode("utf-8")
        except UnicodeDecodeError:
            return None
    return str(raw)


def parse_appmsg(xml_text):
    """Return a readable one-liner for a type-49 app message."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return "[链接/文件]"
    appmsg = root.find(".//appmsg")
    if appmsg is None:
        return "[链接/文件]"

    def txt(tag):
        node = appmsg.find(tag)
        return (node.text or "").strip() if node is not None and node.text else ""

    title = txt("title")
    atype = txt("type")
    try:
        atype_i = int(atype)
    except ValueError:
        atype_i = -1

    if atype_i == 57:  # 引用
        refer = appmsg.find("refermsg")
        quoted, who = "", ""
        if refer is not None:
            q = refer.find("content")
            n = refer.find("displayname")
            quoted = (q.text or "").strip() if q is not None and q.text else ""
            who = (n.text or "").strip() if n is not None and n.text else ""
        quoted = quoted.replace("\n", " ")[:200]
        head = f"> 引用 {who}：{quoted}" if who or quoted else "> [引用]"
        return f"{head}\n\n{title}" if title else head

    label = APPMSG_LABEL.get(atype_i, "链接/文件")
    return f"[{label}] {title}" if title else f"[{label}]"


def build_name_maps(root):
    by_md5, display, known = {}, {}, set()

    con_path = os.path.join(root, "contact", "contact.db")
    if os.path.exists(con_path):
        con = open_ro(con_path)
        for username, remark, nick, alias in con.execute(
            "SELECT username, remark, nick_name, alias FROM contact"
        ):
            if not username:
                continue
            known.add(username)
            by_md5[md5(username)] = username
            display[username] = remark or nick or alias or username
        con.close()

    ses_path = os.path.join(root, "session", "session.db")
    if os.path.exists(ses_path):
        ses = open_ro(ses_path)
        for (username,) in ses.execute("SELECT username FROM SessionTable"):
            if not username:
                continue
            known.add(username)
            by_md5.setdefault(md5(username), username)
            display.setdefault(username, username)
        ses.close()

    return by_md5, display, known


def iter_message_dbs(root):
    msg_dir = os.path.join(root, "message")
    for name in sorted(os.listdir(msg_dir)):
        if name.startswith("message_") and name.endswith(".db"):
            yield os.path.join(msg_dir, name)


def sender_map(db):
    out = {}
    try:
        for rowid, user_name in db.execute("SELECT rowid, user_name FROM Name2Id"):
            out[rowid] = user_name
    except sqlite3.Error:
        pass
    return out


def fmt_ts(ts):
    try:
        return datetime.fromtimestamp(int(ts))
    except (ValueError, OSError, TypeError):
        return None


def strip_sender_prefix(text, known):
    """Group text is '<wxid>:\\n<message>'; drop the prefix when it is a real id."""
    m = SENDER_PREFIX.match(text)
    if m and (m.group(1).startswith("wxid_") or m.group(1) in known):
        return text[m.end():]
    return text


def render(ltype, content, known):
    """Turn one raw row into display text. Returns (text, kind) or None."""
    base = ltype & 0xFFFFFFFF if isinstance(ltype, int) else ltype

    if base in TYPE_LABEL and base != 10000:
        return TYPE_LABEL[base], base

    text = coerce_text(content)

    if base == 49:
        if text and "<" in text:
            return parse_appmsg(text), base
        return "[链接/文件]", base

    if text is None:
        return f"[消息类型 {ltype}]", base

    text = strip_sender_prefix(text, known)

    if base == 10000:
        # system notices are XML <sysmsg>; show a short label
        if "<" in text:
            return "[系统消息]", base
        return text.strip() or "[系统消息]", base

    if not text.strip():
        return f"[消息类型 {ltype}]", base
    return text, base


def main():
    root, outdir, self_wxid = sys.argv[1], sys.argv[2], sys.argv[3]
    os.makedirs(outdir, exist_ok=True)

    by_md5, display, known = build_name_maps(root)
    print(f"name mappings: {len(by_md5)}")

    convos = defaultdict(list)
    kinds = Counter()

    for db_path in iter_message_dbs(root):
        db = open_ro(db_path)
        senders = sender_map(db)
        tables = [r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'"
        )]
        for table in tables:
            talker = by_md5.get(table[4:])
            if talker is None:
                continue
            try:
                rows = db.execute(
                    f"SELECT create_time, real_sender_id, local_type, "
                    f"message_content FROM {table} "
                    f"ORDER BY create_time ASC, sort_seq ASC"
                )
            except sqlite3.Error:
                continue
            for ts, sender_id, ltype, content in rows:
                dt = fmt_ts(ts)
                if dt is None:
                    continue
                who = senders.get(sender_id)
                who = "我" if who == self_wxid else display.get(who, who or "?")
                got = render(ltype, content, known)
                if got is None:
                    continue
                text, kind = got
                kinds[kind] += 1
                convos[talker].append((dt, who, text))
        db.close()

    index = ["# 微信聊天记录归档", "",
             f"导出时间：{datetime.now():%Y-%m-%d %H:%M:%S}", ""]
    total = 0

    for talker, msgs in sorted(convos.items(), key=lambda kv: -len(kv[1])):
        msgs.sort(key=lambda m: m[0])
        name = display.get(talker, talker)
        safe = "".join(c for c in name if c not in '/\\:*?"<>|').strip() or talker
        path = os.path.join(outdir, f"{safe}__{talker}.md")

        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"# {name}\n\n")
            fh.write(f"- 微信 ID：`{talker}`\n")
            fh.write(f"- 消息数：{len(msgs)}\n")
            fh.write(f"- 时间范围：{msgs[0][0]:%Y-%m-%d %H:%M} ~ "
                     f"{msgs[-1][0]:%Y-%m-%d %H:%M}\n\n---\n\n")
            cur_day = None
            for dt, who, text in msgs:
                day = dt.strftime("%Y-%m-%d")
                if day != cur_day:
                    cur_day = day
                    fh.write(f"\n## {day}\n\n")
                body = text.replace("\n", "\n> ")
                fh.write(f"**{who}** · {dt:%H:%M:%S}\n\n> {body}\n\n")

        total += len(msgs)
        index.append(f"- [{name}]({safe}__{talker}.md) — {len(msgs)} 条")

    with open(os.path.join(outdir, "_index.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(index) + "\n")

    print(f"conversations: {len(convos)}  messages: {total}")
    print("top types:", kinds.most_common(10))
    print(f"output: {outdir}")


if __name__ == "__main__":
    main()
