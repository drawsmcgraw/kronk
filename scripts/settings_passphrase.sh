#!/usr/bin/env bash
# settings_passphrase.sh — set the passphrase that turns editing on for the
# /settings page (sprint M21 step 2; docs/features/config-directory.md).
#
# Usage:
#   scripts/settings_passphrase.sh [ENV_FILE]      default: the repo's .env
#
# Asks for the passphrase twice without echo, hashes it (scrypt from
# Python's standard library: N=2^15, r=8, p=1, 16-byte random salt, 32-byte
# key; the same format orchestrator/settings_auth.py checks) and writes one
# line, SETTINGS_PASSPHRASE_HASH=scrypt:..., to ENV_FILE, replacing any
# earlier one. The passphrase goes to Python on stdin: never on a command
# line, in the environment, a file or a log. Neither it nor the hash is
# printed. The file keeps its mode (600 when it is new).
#
# Takes effect on the next deploy: scripts/deploy_tag.sh deploy <tag> for
# production; for the dev pair, the dev up line in docker-compose.yml
# (both instances read the same line, so "apply to dev first" can sign
# its request). To turn editing off again: delete the line and redeploy.
#
# The page is served over plain HTTP on the LAN: the passphrase crosses the
# network unencrypted when you unlock. Use one you use nowhere else.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${1:-$REPO/.env}"
KEY=SETTINGS_PASSPHRASE_HASH
MIN_LEN=12

die() { echo "settings_passphrase: $*" >&2; exit 1; }

target=$(realpath -m -- "$ENV_FILE")
dir=$(dirname "$target")
[ -d "$dir" ] || die "$dir does not exist"

read -r -s -p "New /settings passphrase: " p1 || die "no passphrase read"
echo >&2
read -r -s -p "The same again: " p2 || die "no passphrase read"
echo >&2
[ "$p1" = "$p2" ] || { unset p1 p2; die "the two entries differ; nothing written"; }
unset p2
[ "${#p1}" -ge "$MIN_LEN" ] || { unset p1; die "use at least $MIN_LEN characters; nothing written"; }

hash=$(printf '%s' "$p1" | python3 -I -c '
import base64, hashlib, secrets, sys
pw = sys.stdin.buffer.read()
salt = secrets.token_bytes(16)
key = hashlib.scrypt(pw, salt=salt, n=2 ** 15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)
b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")
print(f"scrypt:15:8:1:{b64(salt)}:{b64(key)}")
')
unset p1
[[ "$hash" == scrypt:15:8:1:* ]] || die "hashing failed; nothing written"

tmp=$(mktemp "$dir/.env.settings.XXXXXX")
trap 'rm -f -- "$tmp"' EXIT
if [ -e "$target" ]; then
    chmod --reference="$target" "$tmp"
    grep -v "^${KEY}=" -- "$target" > "$tmp" || true
    # Keep the last line terminated before appending.
    if [ -s "$tmp" ] && [ "$(tail -c1 -- "$tmp" | od -An -c | tr -d ' ')" != '\n' ]; then echo >> "$tmp"; fi
else
    chmod 600 "$tmp"
fi
printf '%s=%s\n' "$KEY" "$hash" >> "$tmp"
mv -- "$tmp" "$target"
trap - EXIT
unset hash
echo "settings_passphrase: wrote $KEY to $target (the passphrase and the hash are not shown)." >&2
echo "settings_passphrase: it takes effect at the next deploy (scripts/deploy_tag.sh deploy <tag>; dev: the dev up line)." >&2
