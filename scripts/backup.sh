#!/usr/bin/env bash
# Daily backup of the Horilla/Demiller database and uploaded media.
#
#   DATABASE_URL=postgres://user:pass@host:5432/dbname \
#   BACKUP_DIR=$HOME/backups MEDIA_DIR=$HOME/horilla/media KEEP_DAYS=30 \
#   bash scripts/backup.sh
#
# Writes <BACKUP_DIR>/db-YYYYmmdd-HHMMSS.sql.gz and media-YYYYmmdd-HHMMSS.tar.gz,
# verifies the dump is readable, deletes files older than KEEP_DAYS, exits non-zero on any failure.
# Restore: see docs/demiller/PRODUCTION_RUNBOOK.md. Copy BACKUP_DIR off the server too.
set -euo pipefail
umask 077

: "${DATABASE_URL:?Set DATABASE_URL}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups}"
MEDIA_DIR="${MEDIA_DIR:-}"
KEEP_DAYS="${KEEP_DAYS:-30}"
stamp="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$BACKUP_DIR"
db_file="$BACKUP_DIR/db-$stamp.sql.gz"
tmp="$db_file.partial"
trap 'rm -f "$tmp"' EXIT   # never leave a half-written dump behind

pg_dump --no-owner --no-privileges "$DATABASE_URL" | gzip -9 > "$tmp"
gzip -t "$tmp"                                   # the archive must be readable
[ "$(stat -c %s "$tmp")" -gt 200 ] || { echo "backup suspiciously small" >&2; rm -f "$tmp"; exit 1; }
mv "$tmp" "$db_file"
echo "database -> $db_file"

if [ -n "$MEDIA_DIR" ] && [ -d "$MEDIA_DIR" ]; then
  tar -czf "$BACKUP_DIR/media-$stamp.tar.gz" -C "$(dirname "$MEDIA_DIR")" "$(basename "$MEDIA_DIR")"
  echo "media    -> $BACKUP_DIR/media-$stamp.tar.gz"
fi

find "$BACKUP_DIR" -maxdepth 1 \( -name 'db-*.sql.gz' -o -name 'media-*.tar.gz' \) -mtime +"$KEEP_DAYS" -delete
echo "done $(date -Is)"
