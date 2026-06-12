#!/usr/bin/env bash
# Set a Langfuse user's password directly in its Postgres database.
#
# Why this exists: self-hosted Langfuse only supports self-service password
# change/reset via transactional email (SMTP), which this deployment
# deliberately does not configure. The supported no-SMTP alternative in the
# docs is a rename-and-resignup dance. This script does the honest version:
# bcrypt-hash a new password and update the `users.password` column, which is
# exactly what Langfuse's NextAuth credentials provider checks at login.
#
# Hash format matches what Langfuse writes at signup: bcrypt, cost 12,
# "$2a$" prefix (bcryptjs). Verified compatible 2026-06-12 on Langfuse
# v3.181.0.
#
# Usage:
#   ./scripts/langfuse_set_password.sh [email]      # prompts silently
#   LANGFUSE_NEW_PASSWORD=... ./scripts/langfuse_set_password.sh [email]
#
# Default email: drew.malone@gmail.com (the only user on this box).
#
# After a successful change, LANGFUSE_INIT_USER_PASSWORD in .env.langfuse is
# STALE — it only seeds the very first boot on an empty database and is never
# read again. Update it or ignore it; this script prints a reminder.
set -euo pipefail

EMAIL="${1:-drew.malone@gmail.com}"
PG_CONTAINER="${PG_CONTAINER:-langfuse-postgres}"

command -v docker >/dev/null || { echo "ERROR: docker not found"; exit 1; }
python3 -c "import bcrypt" 2>/dev/null || {
    echo "ERROR: python3 bcrypt module not available (pip install bcrypt)"; exit 1; }

# ── confirm the user exists before asking for a password ───────────────────
user_id=$(docker exec "$PG_CONTAINER" psql -U postgres -t -A \
    -c "SELECT id FROM users WHERE email = '$EMAIL';")
if [[ -z "$user_id" ]]; then
    echo "ERROR: no user with email '$EMAIL' in Langfuse database. Users:"
    docker exec "$PG_CONTAINER" psql -U postgres -t -A -c "SELECT email FROM users;"
    exit 1
fi

# ── get the new password ────────────────────────────────────────────────────
if [[ -n "${LANGFUSE_NEW_PASSWORD:-}" ]]; then
    pw="$LANGFUSE_NEW_PASSWORD"
else
    read -r -s -p "New password for $EMAIL (min 8 chars): " pw;  echo
    read -r -s -p "Repeat: " pw2; echo
    [[ "$pw" == "$pw2" ]] || { echo "ERROR: passwords do not match"; exit 1; }
fi
(( ${#pw} >= 8 )) || { echo "ERROR: password must be at least 8 characters"; exit 1; }

# ── hash (bcrypt cost 12, 2a prefix — matches Langfuse signup hashes) ──────
hash=$(PW="$pw" python3 - <<'EOF'
import bcrypt, os
pw = os.environ["PW"].encode()
print(bcrypt.hashpw(pw, bcrypt.gensalt(rounds=12, prefix=b"2a")).decode())
EOF
)

# ── update ──────────────────────────────────────────────────────────────────
docker exec "$PG_CONTAINER" psql -U postgres -v ON_ERROR_STOP=1 -q \
    -c "UPDATE users SET password = '$hash', updated_at = now() WHERE email = '$EMAIL';"

echo "OK: password updated for $EMAIL (user $user_id)."
echo "Reminder: LANGFUSE_INIT_USER_PASSWORD in .env.langfuse is now stale —"
echo "it is only used to seed a fresh empty database, never read again."
