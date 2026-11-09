#!/usr/bin/env bash
# Daily backup of the Horilla/Demiller database and uploaded media.
#
#   PostgreSQL:  DATABASE_URL=postgres://user:pass@host:5432/dbname \
#                BACKUP_DIR=$HOME/backups MEDIA_DIR=$HOME/horilla/media KEEP_DAYS=30 bash scripts/backup.sh
#   SQLite:      SQLITE_PATH=$HOME/hrm_data/horilla.sqlite3 BACKUP_DIR=$HOME/backups bash scripts/backup.sh
#                (uses SQLite's online-backup API, safe while the site is running; needs python3 on PATH,
#                 e.g. run it after activating the app's virtualenv)
#
# Writes <BACKUP_DIR>/db-YYYYmmdd-HHMMSS.sql.gz and media-YYYYmmdd-HHMMSS.tar.gz,
# verifies the dump is readable, deletes files older than KEEP_DAYS, exits non-zero on any failure.
# Restore: see docs/demiller/PRODUCTION_RUNBOOK.md. Copy BACKUP_DIR off the server too.
set -euo pipefail
umask 077

if [ -z "${DATABASE_URL:-}" ] && [ -z "${SQLITE_PATH:-}" ]; then
  echo "Set DATABASE_URL (PostgreSQL) or SQLITE_PATH (SQLite)" >&2; exit 2
fi
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups}"
MEDIA_DIR="${MEDIA_DIR:-}"
KEEP_DAYS="${KEEP_DAYS:-30}"
stamp="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$BACKUP_DIR"
ext=sql.gz; [ -n "${SQLITE_PATH:-}" ] && ext=sqlite3.gz
db_file="$BACKUP_DIR/db-$stamp.$ext"
tmp="$db_file.partial"
trap 'rm -f "$tmp"' EXIT   # never leave a half-written dump behind

if [ -n "${SQLITE_PATH:-}" ]; then
  [ -f "$SQLITE_PATH" ] || { echo "SQLITE_PATH not found: $SQLITE_PATH" >&2; exit 1; }
  snap="$BACKUP_DIR/.snapshot-$stamp.sqlite3"
  trap 'rm -f "$tmp" "$snap"' EXIT
  python3 - "$SQLITE_PATH" "$snap" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2])
src.backup(dst)                                  # consistent copy even while the site is writing
dst.execute("PRAGMA integrity_check").fetchone(); dst.close(); src.close()
PY
  gzip -9 -c "$snap" > "$tmp"
  rm -f "$snap"
else
  pg_dump --no-owner --no-privileges "$DATABASE_URL" | gzip -9 > "$tmp"
fi
gzip -t "$tmp"                                   # the archive must be readable
[ "$(stat -c %s "$tmp")" -gt 200 ] || { echo "backup suspiciously small" >&2; rm -f "$tmp"; exit 1; }
mv "$tmp" "$db_file"
echo "database -> $db_file"

if [ -n "$MEDIA_DIR" ] && [ -d "$MEDIA_DIR" ]; then
  tar -czf "$BACKUP_DIR/media-$stamp.tar.gz" -C "$(dirname "$MEDIA_DIR")" "$(basename "$MEDIA_DIR")"
  echo "media    -> $BACKUP_DIR/media-$stamp.tar.gz"
fi

find "$BACKUP_DIR" -maxdepth 1 \( -name 'db-*.sql.gz' -o -name 'db-*.sqlite3.gz' -o -name 'media-*.tar.gz' \) -mtime +"$KEEP_DAYS" -delete
echo "done $(date -Is)"
