#!/usr/bin/env sh
set -eu
# Start in a separate Compose project, using an empty database and private bucket.
source_dir=${1:?Usage: sh infra/restore.sh /absolute/path/to/backup}
case "$source_dir" in /*) ;; *) echo 'An absolute backup path is required.' >&2; exit 2;; esac
(cd "$source_dir" && sha256sum -c SHA256SUMS)
docker compose up -d --wait db redis
existing=$(docker compose exec -T db psql -At -U desk -d desk -c "SELECT count(*) FROM pg_tables WHERE schemaname='public'")
if [ "$existing" != 0 ]; then echo 'Restore requires an empty destination database.' >&2; exit 2; fi
docker compose exec -T db pg_restore -U desk -d desk --no-owner --single-transaction --exit-on-error < "$source_dir/database.dump"
docker compose run --rm --no-deps -T --user "$(id -u):$(id -g)" -v "$source_dir:/snapshot:ro" backend python manage.py snapshot_files restore /snapshot/files
echo 'Database and file manifest restored. Verify the destination before enabling workers or external traffic.'
