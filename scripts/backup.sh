#!/usr/bin/env bash
# Backs up the live SQLite DB using sqlite3's online .backup command (safe to
# run while the server is up - WAL mode means readers/writers aren't blocked).
#
# Usage: scripts/backup.sh
# Restore: cp backups/scsrg_<timestamp>.db data/scsrg.db  (server must be stopped)
# Gap description: anything written between the last backup and a crash is
# lost - readings are low-value/high-volume, so this is an accepted gap;
# incidents/acknowledgments are the rows that matter and are written
# synchronously well before this script would typically run (e.g. cron).

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_PATH="${ROOT_DIR}/data/scsrg.db"
BACKUP_DIR="${ROOT_DIR}/backups"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_PATH="${BACKUP_DIR}/scsrg_${TIMESTAMP}.db"

mkdir -p "${BACKUP_DIR}"

if [ ! -f "${DB_PATH}" ]; then
  echo "No DB found at ${DB_PATH} - run scripts/init_db.py first." >&2
  exit 1
fi

sqlite3 "${DB_PATH}" ".backup '${BACKUP_PATH}'"
echo "Backed up ${DB_PATH} -> ${BACKUP_PATH}"
