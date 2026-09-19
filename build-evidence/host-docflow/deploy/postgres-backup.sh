#!/bin/sh
set -eu
umask 077

interval="${DOCFLOW_BACKUP_INTERVAL_SECONDS:-86400}"
retention_days="${DOCFLOW_BACKUP_RETENTION_DAYS:-14}"
database="${POSTGRES_DB:-docflow}"
user="${POSTGRES_USER:-docflow}"
export PGPASSWORD="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"

mkdir -p /backups

while :; do
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  database_tmp="/backups/.docflow-${stamp}.dump.tmp"
  database_final="/backups/docflow-${stamp}.dump"
  data_tmp="/backups/.docflow-data-${stamp}.tar.gz.tmp"
  data_final="/backups/docflow-data-${stamp}.tar.gz"

  pg_dump \
    --username "$user" \
    --dbname "$database" \
    --format custom \
    --compress 6 \
    --no-owner \
    --no-acl \
    --file "$database_tmp"
  mv "$database_tmp" "$database_final"

  tar -C /source-data -czf "$data_tmp" .
  mv "$data_tmp" "$data_final"

  sha256sum "$database_final" > "${database_final}.sha256"
  sha256sum "$data_final" > "${data_final}.sha256"
  date -u +%FT%TZ > /backups/last-success

  find /backups -type f -mtime "+${retention_days}" \
    ! -name last-success -delete
  sleep "$interval"
done
