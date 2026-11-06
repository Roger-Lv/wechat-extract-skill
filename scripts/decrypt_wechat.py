#!/usr/bin/env python3
"""Decrypt WeChat 4.x (WCDB / SQLCipher-4) databases on macOS.

Page layout, 4096 bytes: [data][IV 16][HMAC 64]
  page 0 : first 16 bytes are the salt (plaintext), data region is bytes 16..4016
  page n : data region is bytes 0..4016

Key derivation — this is the part everyone gets wrong:
  encKey  = PBKDF2-HMAC-SHA512(key, salt, 256000, 32)
  macSalt = salt XOR 0x3a
  macKey  = PBKDF2-HMAC-SHA512(encKey, macSalt, 2, 32)

The 32-byte key is fed to PBKDF2 as a *password*. It is NOT used as a raw
SQLCipher key, which is why `sqlcipher db "PRAGMA key=\"x'<hex>'\""` fails.

Output is byte-identical to chatlog's darwin v4 decryptor.

Usage:
  decrypt_wechat.py <key-hex> <src.db> <dst.db>
  decrypt_wechat.py <key-hex> --tree <src-dir> <dst-dir>
"""
import hashlib
import hmac
import os
import struct
import sys

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

PAGE_SIZE = 4096
IV_SIZE = 16
HMAC_SIZE = 64
RESERVE = IV_SIZE + HMAC_SIZE              # 80
SALT_SIZE = 16
ITER = 256000
KEY_SIZE = 32
SQLITE_HEADER = b"SQLite format 3\x00"
DATA_END = PAGE_SIZE - RESERVE + IV_SIZE   # 4032


def derive(key, salt):
    enc_key = hashlib.pbkdf2_hmac("sha512", key, salt, ITER, KEY_SIZE)
    mac_salt = bytes(b ^ 0x3A for b in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, KEY_SIZE)
    return enc_key, mac_key


def decrypt_page(page, page_no, enc_key, mac_key):
    offset = SALT_SIZE if page_no == 0 else 0

    mac = hmac.new(mac_key, digestmod=hashlib.sha512)
    mac.update(page[offset:DATA_END])
    mac.update(struct.pack("<I", page_no + 1))
    if not hmac.compare_digest(mac.digest(), page[DATA_END:DATA_END + HMAC_SIZE]):
        raise ValueError(f"HMAC verification failed on page {page_no}")

    iv = page[PAGE_SIZE - RESERVE:PAGE_SIZE - RESERVE + IV_SIZE]
    dec = Cipher(algorithms.AES(enc_key), modes.CBC(iv)).decryptor()
    body = dec.update(page[offset:PAGE_SIZE - RESERVE]) + dec.finalize()
    return body + page[PAGE_SIZE - RESERVE:PAGE_SIZE]


def decrypt_file(key, src, dst):
    size = os.path.getsize(src)
    if size == 0:
        return "empty"
    if size % PAGE_SIZE:
        return f"skipped (size {size} not a multiple of {PAGE_SIZE})"

    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    with open(src, "rb") as fh:
        page0 = fh.read(PAGE_SIZE)
        if page0[:15] == SQLITE_HEADER[:15]:
            with open(dst, "wb") as out:          # already plaintext
                out.write(page0)
                while chunk := fh.read(1 << 20):
                    out.write(chunk)
            return "plaintext"

        salt = page0[:SALT_SIZE]
        enc_key, mac_key = derive(key, salt)
        total = size // PAGE_SIZE

        tmp = dst + ".tmp"
        try:
            with open(tmp, "wb") as out:
                out.write(SQLITE_HEADER)
                out.write(decrypt_page(page0, 0, enc_key, mac_key))
                for i in range(1, total):
                    page = fh.read(PAGE_SIZE)
                    if len(page) < PAGE_SIZE:
                        break
                    if not any(page):
                        out.write(page)
                        continue
                    out.write(decrypt_page(page, i, enc_key, mac_key))
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)          # never leave a partial file behind
            raise
        os.replace(tmp, dst)
    return f"ok ({total} pages)"


def main():
    args = sys.argv[1:]
    if len(args) < 3:
        sys.exit(__doc__)

    try:
        key = bytes.fromhex(args[0])
    except ValueError:
        sys.exit(f"key must be hex, got {args[0]!r}")
    if len(key) != KEY_SIZE:
        sys.exit(f"key must be {KEY_SIZE} bytes (64 hex chars), got {len(key)}")

    # `--tree` takes <src-dir> <dst-dir>; the single-file form takes <src> <dst>.
    # Both are three arguments after the key, so they are parsed together rather
    # than by fixed offsets - reading argv[3]/argv[4] unconditionally made the
    # documented single-file form die with an IndexError.
    tree = args[1] == "--tree"
    if tree:
        if len(args) < 4:
            sys.exit(__doc__)
        src, dst = args[2], args[3]
    else:
        src, dst = args[1], args[2]

    if not tree:
        print(f"{src}: {decrypt_file(key, src, dst)}")
        return

    ok = bad = 0
    for root, _, files in os.walk(src):
        for name in sorted(files):
            if not name.endswith(".db"):
                continue
            sp = os.path.join(root, name)
            dp = os.path.join(dst, os.path.relpath(sp, src))
            try:
                result = decrypt_file(key, sp, dp)
            except ValueError as exc:
                print(f"  FAIL {os.path.relpath(sp, src)}: {exc}")
                if os.path.exists(dp):
                    os.remove(dp)          # don't leave a 0-byte decoy
                bad += 1
                continue
            print(f"  {result:>16}  {os.path.relpath(sp, src)}")
            ok += 1
    print(f"\n{ok} decrypted, {bad} failed")
    if bad:
        print("A wrong-key failure usually means this key belongs to a different "
              "WeChat version (3.x and 4.x use different keys).")


if __name__ == "__main__":
    main()
