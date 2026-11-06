#!/bin/bash
# Survey WeChat for Mac on this machine: version, accounts, database layout.
# Read-only. Run this first to fill in the paths the other scripts need.
set -uo pipefail

APP="/Applications/WeChat.app"
CONTAINER="$HOME/Library/Containers/com.tencent.xinWeChat/Data"

echo "=== app ==="
if [ -d "$APP" ]; then
  ver=$(defaults read "$APP/Contents/Info.plist" CFBundleShortVersionString 2>/dev/null)
  echo "WeChat.app version: ${ver:-unknown}"
else
  echo "WeChat.app not found in /Applications"
fi

echo
echo "=== WeChat running? ==="
pgrep -fl WeChat | head -5 || echo "not running"
echo "(quit WeChat before decrypting for a consistent snapshot)"

echo
echo "=== 4.x accounts (xwechat_files) ==="
FOURX="$CONTAINER/Documents/xwechat_files"
if [ -d "$FOURX" ]; then
  for acct in "$FOURX"/*/; do
    [ -d "$acct/db_storage" ] || continue
    echo "-- $(basename "$acct")"
    ls "$acct/db_storage" 2>/dev/null | tr '\n' ' '
    echo
    echo "   message dbs: $(find "$acct/db_storage/message" -maxdepth 1 -name 'message_*.db' 2>/dev/null | wc -l | tr -d ' ')"
  done
else
  echo "none"
fi

echo
echo "=== key_info.db (4.x; plaintext sqlite holding the wrapped key) ==="
find "$FOURX" -name 'key_info.db' 2>/dev/null | while read -r f; do
  echo "$f"
  sqlite3 "$f" "SELECT count(*) || ' rows' FROM LoginKeyInfoTable;" 2>/dev/null
done

echo
echo "=== 3.x accounts (legacy; DIFFERENT key from 4.x) ==="
THREEX="$CONTAINER/Library/Application Support/com.tencent.xinWeChat/2.0b4.0.9"
if [ -d "$THREEX" ]; then
  for acct in "$THREEX"/*/; do
    [ -d "$acct/Message" ] || continue
    echo "-- $(basename "$acct")"
    echo "   msg dbs: $(find "$acct/Message" -maxdepth 1 -name 'msg_*.db' 2>/dev/null | wc -l | tr -d ' ')"
  done
else
  echo "none"
fi

echo
echo "=== tooling ==="
for t in python3 sqlcipher zstd chatlog; do
  printf '%-10s ' "$t"
  command -v "$t" >/dev/null 2>&1 && echo "$(command -v "$t")" || echo "MISSING"
done
