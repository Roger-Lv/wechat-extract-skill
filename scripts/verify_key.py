#!/usr/bin/env python3
"""Replicate chatlog's macOS WeChat v4 key derivation and validate against page 1.

Algorithm (chatlog internal/wechat/decrypt/darwin/v4.go + common/common.go):
    encKey  = PBKDF2(key, salt, 256000, 32, SHA512)
    macSalt = salt XOR 0x3a
    macKey  = PBKDF2(encKey, macSalt, 2, 32, SHA512)
    HMAC-SHA512 over page1[16 : pageSize-reserve+IVSize] || LE32(1)
    compared with page1[pageSize-reserve+IVSize : +64]
"""
import hashlib
import hmac
import struct
import sys

PAGE_SIZE = 4096
RESERVE = 80          # IV(16) + HMAC(64), already a multiple of AES block size
HMAC_SIZE = 64
IV_SIZE = 16
ITER = 256000
SALT_SIZE = 16
DATA_END = PAGE_SIZE - RESERVE + IV_SIZE      # 4032


def derive(key: bytes, salt: bytes):
    enc_key = hashlib.pbkdf2_hmac("sha512", key, salt, ITER, 32)
    mac_salt = bytes(b ^ 0x3A for b in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, 32)
    return enc_key, mac_key


def check(path: str, key_hex: str) -> bool:
    with open(path, "rb") as fh:
        page1 = fh.read(PAGE_SIZE)
    if len(page1) < PAGE_SIZE:
        print(f"  {path}: file shorter than one page")
        return False

    salt = page1[:SALT_SIZE]
    key = bytes.fromhex(key_hex)
    enc_key, mac_key = derive(key, salt)

    mac = hmac.new(mac_key, digestmod=hashlib.sha512)
    mac.update(page1[SALT_SIZE:DATA_END])
    mac.update(struct.pack("<I", 1))
    calc = mac.digest()
    stored = page1[DATA_END:DATA_END + HMAC_SIZE]

    ok = hmac.compare_digest(calc, stored)
    print(f"  {path}")
    print(f"    salt        = {salt.hex()}")
    print(f"    encKey      = {enc_key.hex()}")
    print(f"    calc HMAC   = {calc.hex()[:32]}...")
    print(f"    stored HMAC = {stored.hex()[:32]}...")
    print(f"    => {'MATCH  *** key is CORRECT ***' if ok else 'MISMATCH'}")
    return ok


if __name__ == "__main__":
    key_hex = sys.argv[1]
    files = sys.argv[2:]
    print(f"key = {key_hex}  ({len(key_hex)//2} bytes)\n")
    results = [check(p, key_hex) for p in files]
    print(f"\n{sum(results)}/{len(results)} files matched")
