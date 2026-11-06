#!/usr/bin/env python3
"""Verify that an export actually contains every message the databases hold.

Why this exists: the failure mode this catches is **silent**. A short export
raises nothing, logs nothing, and looks exactly like a complete one - the .md
files are well formed and the totals printed by export_markdown.py are the
totals of whatever it happened to read. The only way to know the archive is
whole is to recount the source and compare.

It re-derives the expected count by importing export_markdown and applying the
same row filters, so the two cannot drift apart. A conversation is reported
when

    exported != exportable   (rows the exporter should have written)

and the reasons rows were dropped are broken out, so an explained delta and an
unexplained one look different.

Usage: verify_export.py <decrypted-root> <export-dir> <self-wxid> [--include-unmapped]

Exit code 1 if any conversation is short, long, missing, or if a whole talker
was skipped for want of a name mapping (the last one only applies without
--include-unmapped).
"""
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import export_markdown as em  # noqa: E402

COUNT_RE = re.compile(r"^- 消息数：(\d+)\s*$", re.M)


def source_counts(root, by_md5, known, include_unmapped):
    """Per-talker: rows, exportable rows, and why the rest were dropped.

    Row filters mirror export_markdown.main() exactly - both call em.fmt_ts
    and em.render - so `exportable` is the count the exporter should have
    written.
    """
    stats = defaultdict(lambda: Counter())
    unknown = Counter()          # md5 -> rows, for talkers no name mapping knows

    for db_path in em.iter_message_dbs(root):
        db = em.open_ro(db_path)
        tables = [r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'"
        )]
        for table in tables:
            talker = em.resolve_talker(table[4:], by_md5, include_unmapped)
            try:
                rows = db.execute(
                    f'SELECT create_time, local_type, message_content FROM "{table}"'
                )
            except Exception:
                continue

            for ts, ltype, content in rows:
                if talker is None:
                    unknown[table[4:]] += 1
                    continue
                st = stats[talker]
                st["rows"] += 1
                if em.fmt_ts(ts) is None:
                    st["no_timestamp"] += 1
                    continue
                if em.render(ltype, content, known) is None:
                    st["unrenderable"] += 1
                    continue
                st["exportable"] += 1
        db.close()

    return stats, unknown


def exported_counts(outdir):
    """talker -> 消息数, read back from the .md headers the exporter wrote."""
    got = {}
    for name in os.listdir(outdir):
        if not name.endswith(".md") or name == "_index.md":
            continue
        if "__" not in name:
            continue
        talker = name[:-3].rsplit("__", 1)[1]
        try:
            with open(os.path.join(outdir, name), encoding="utf-8") as fh:
                head = fh.read(4096)
        except OSError:
            continue
        m = COUNT_RE.search(head)
        got[talker] = int(m.group(1)) if m else -1
    return got


def main():
    argv = sys.argv[1:]
    flags = {a for a in argv if a.startswith("--")}
    positional = [a for a in argv if not a.startswith("--")]
    if len(positional) != 3:
        print(__doc__.strip())
        sys.exit(2)
    root, outdir, _self_wxid = positional   # wxid: CLI symmetry with the exporter
    include_unmapped = "--include-unmapped" in flags

    by_md5, display, known = em.build_name_maps(root)
    stats, unknown = source_counts(root, by_md5, known, include_unmapped)
    written = exported_counts(outdir)

    total_src = sum(s["rows"] for s in stats.values())
    total_ok = sum(s["exportable"] for s in stats.values())

    mismatch, missing = [], []
    for talker, st in stats.items():
        exp = st["exportable"]
        n = written.get(talker)
        if n is None:
            if exp:
                missing.append((talker, exp))
            continue
        if n != exp:
            mismatch.append((talker, exp, n, st["rows"]))

    extra = [t for t in written if t not in stats]

    print(f"源库会话            : {len(stats):,}")
    print(f"源库消息行          : {total_src:,}")
    print(f"应导出（同过滤规则）: {total_ok:,}")
    print(f"被过滤掉的          : {total_src - total_ok:,}"
          f"（无时间戳 {sum(s['no_timestamp'] for s in stats.values()):,} /"
          f" 渲染失败 {sum(s['unrenderable'] for s in stats.values()):,}）")
    print(f"归档文件            : {len(written):,}")
    print()

    bad = False

    if mismatch:
        bad = True
        print(f"✗ {len(mismatch)} 个会话条数对不上：")
        for talker, exp, n, rows in sorted(mismatch, key=lambda x: -abs(x[1] - x[2])):
            print(f"    {em.display_name(talker, display)}"
                  f"  应 {exp:>7,}  实 {n:>7,}  差 {exp - n:>+6,}  (源 {rows:,})")
        print("  → 退出微信让它 checkpoint，再重跑这几个会话；见 SKILL.md「核验」。")
    else:
        print("✓ 每个会话的条数都对得上")

    if missing:
        bad = True
        print(f"\n✗ {len(missing)} 个会话在源库里有消息、但没有归档文件：")
        for talker, exp in sorted(missing, key=lambda x: -x[1])[:10]:
            print(f"    {em.display_name(talker, display)}  应 {exp:,}")
    if extra:
        print(f"\n· {len(extra)} 个归档文件在源库里找不到对应会话（旧归档残留？）")

    if unknown:
        bad = True
        print(f"\n✗ {len(unknown)} 个消息表没有名字映射，整段会话被跳过："
              f"{sum(unknown.values()):,} 行")
        print("  → 这些会话不在 contact.db / session.db 里。"
              "加 --include-unmapped 按表名导出，或补上映射再重跑。")
    else:
        print("\n✓ 每个消息表都映射到了会话")

    print()
    print("结论:", "有问题，见上" if bad else "归档完整")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
