#!/usr/bin/env sh
set -eu
# Run from the project root on a Compose host.
target=${1:?Usage: sh infra/backup.sh /absolute/path/to/new-backup}
case "$target" in /*) ;; *) echo 'An absolute backup path is required.' >&2; exit 2;; esac
if [ -e "$target" ]; then echo 'Choose a new backup directory.' >&2; exit 2; fi
umask 077
mkdir -p "$target"
docker compose stop web beat worker backend
trap 'docker compose up -d backend worker beat web' EXIT
docker compose exec -T db pg_dump -U desk -d desk -Fc > "$target/database.dump"
docker compose run --rm --no-deps -T --user "$(id -u):$(id -g)" -v "$target:/snapshot" backend python manage.py snapshot_files export /snapshot/files
docker compose run --rm --no-deps -T --user "$(id -u):$(id -g)" -v "$target:/snapshot:ro" backend python manage.py snapshot_files verify /snapshot/files
(cd "$target" && sha256sum database.dump files/manifest.json > SHA256SUMS)
echo 'Backup complete. Keep the encryption key separately.'
