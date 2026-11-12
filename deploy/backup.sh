#!/usr/bin/env bash
# Back up everything that cannot be rebuilt: the database, the uploaded files, the crawler's
# data, the search index (as Qdrant snapshots) and the settings file.
#
#   sudo bash /opt/cob-rag/current/deploy/backup.sh [--keep-days N] [--quiet] [--dry-run]
#
# One folder per backup under /opt/cob-rag/backups/, named <date>-<version>. Backups older than
# --keep-days (default 7) are removed, but only folders this script made, and never the newest.
# The queue and cache store (Valkey) is not backed up: it only holds things that rebuild
# themselves. Exit codes: 0 done, 1 failed, 2 bad arguments, 3 a check failed.

set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
trap 'on_error $? $LINENO' ERR

KEEP_DAYS=7
QUIET=0
PARTIAL_DIR=""
BACKUP_PATTERN='^[0-9]{8}T[0-9]{6}Z-[0-9]+\.[0-9]+\.[0-9]+$'

info() {
  ((QUIET == 1)) || say "$@"
}

usage() {
  cat <<'EOF'
Back up this install.

  sudo bash deploy/backup.sh [--keep-days N] [--quiet] [--dry-run]

  --keep-days N   remove backups older than N days (default 7); the newest is always kept
  --quiet         print only the final line (for the nightly job)
  --dry-run       say what would happen, change nothing
EOF
}

parse_args() {
  while (($#)); do
    case $1 in
      --keep-days)
        (($# >= 2)) || die 2 "--keep-days needs a number." "for example: --keep-days 14"
        [[ $2 =~ ^[1-9][0-9]*$ ]] || die 2 "--keep-days needs a whole number of days, like 7." "for example: --keep-days 14"
        KEEP_DAYS=$2
        shift 2
        ;;
      --quiet) QUIET=1; shift ;;
      --dry-run) DRY_RUN=1; shift ;;
      -h | --help) usage; exit 0 ;;
      *) die 2 "Unknown option: $1" "run: bash deploy/backup.sh --help" ;;
    esac
  done
}

# An unfinished backup must never look like a finished one: remove what this run made.
cleanup() {
  if [[ -n $PARTIAL_DIR && $PARTIAL_DIR == "$INSTALL_DIR"/backups/* && -d $PARTIAL_DIR ]]; then
    rm -rf -- "$PARTIAL_DIR"
    printf 'The unfinished backup was removed: %s\n' "$PARTIAL_DIR" >&2
  fi
}
trap cleanup EXIT

dump_database() {
  compose exec -T postgres pg_dump -U rag -d rag_kernel -Fc >"$1/postgres.dump"
  [[ -s $1/postgres.dump ]] || die 1 "the database dump came out empty." "check that the database is running: docker compose --project-directory $INSTALL_DIR/current ps"
}

tar_volume() {
  # tar_volume SERVICE FOLDER-IN-CONTAINER OUTPUT-FILE
  compose run --rm --no-deps -T --user 0 --entrypoint tar "$1" -C "$2" -cf - . >"$3"
}

snapshot_vectors() {
  mkdir -p "$1/qdrant"
  compose run --rm --no-deps -T --user 0 --entrypoint python -v "$1/qdrant:/backup" app \
    -m callobuzz_rag.vectordb.snapshots create /backup
}

write_manifest() {
  local dir=$1 version=$2 domain=$3 storage=$4 parts=$5
  printf '{"version": "%s", "created_at": "%s", "domain": "%s", "storage_provider": "%s", "parts": [%s]}\n' \
    "$version" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$domain" "$storage" "$parts" >"$dir/manifest.json"
}

write_checksums() {
  local sums
  sums=$(cd "$1" && find . -type f ! -name SHA256SUMS -printf '%P\0' | sort -z | xargs -0 sha256sum)
  printf '%s\n' "$sums" >"$1/SHA256SUMS"
}

prune_backups() {
  local keep_days=$1 cutoff newest dir name stamp
  cutoff=$(date -u -d "-$keep_days days" +%Y%m%dT%H%M%SZ)
  newest=$(find "$INSTALL_DIR/backups" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' |
    grep -E "$BACKUP_PATTERN" | sort | tail -n 1 || true)
  for dir in "$INSTALL_DIR"/backups/*/; do
    [[ -d $dir ]] || continue
    name=$(basename "$dir")
    [[ $name =~ ^([0-9]{8}T[0-9]{6}Z)-[0-9]+\.[0-9]+\.[0-9]+$ ]] || continue
    stamp=${BASH_REMATCH[1]}
    [[ $name == "$newest" ]] && continue
    if [[ $stamp < $cutoff ]]; then
      rm -rf -- "${dir%/}"
      info "  Removed the old backup $name."
    fi
  done
}

main() {
  parse_args "$@"
  need_root
  take_lock
  [[ -e $INSTALL_DIR/current ]] ||
    die 3 "no install was found in $INSTALL_DIR." "run deploy/install.sh first."

  local version domain storage id dir parts=""
  version=$(installed_version)
  domain=$(env_value "$INSTALL_DIR/.env" RAG_SITE_DOMAIN 2>/dev/null || true)
  storage=$(env_value "$INSTALL_DIR/.env" RAG_STORAGE_PROVIDER 2>/dev/null || true)
  storage=${storage:-local}
  id=$(backup_id "$(date +%s)" "$version")
  dir=$INSTALL_DIR/backups/$id

  info "Backing up version $version into $dir"
  if is_dry; then
    would "create $dir (owner only)"
    would "dump the database (pg_dump) into postgres.dump"
  else
    [[ -n $(compose ps --status running -q postgres 2>/dev/null) ]] ||
      die 3 "the database is not running, so there is nothing to dump." \
        "start it first: docker compose --project-directory $INSTALL_DIR/current up -d"
    mkdir -p "$INSTALL_DIR/backups"
    mkdir -m 700 "$dir"
    PARTIAL_DIR=$dir
    info "  Dumping the database..."
    dump_database "$dir"
  fi
  parts='"postgres.dump"'

  if [[ $storage == local ]]; then
    if is_dry; then
      would "pack the uploaded files into files.tar"
    else
      info "  Packing the uploaded files..."
      tar_volume app /var/lib/cob-rag/files "$dir/files.tar"
    fi
    parts="$parts, \"files.tar\""
  else
    info "  Uploaded files are kept by your S3 provider: they are not part of this backup."
  fi

  if is_dry; then
    would "pack the crawler's data into scraper.tar"
    would "snapshot the search index: python -m callobuzz_rag.vectordb.snapshots create"
    would "copy the settings file to env (owner-only)"
    would "write manifest.json and SHA256SUMS"
  else
    info "  Packing the crawler's data..."
    tar_volume scraper /data "$dir/scraper.tar"
    info "  Taking a snapshot of the search index..."
    snapshot_vectors "$dir"
    install -m 600 "$INSTALL_DIR/.env" "$dir/env"
    write_manifest "$dir" "$version" "$domain" "$storage" "$parts, \"scraper.tar\", \"qdrant/\", \"env\""
    write_checksums "$dir"
    PARTIAL_DIR=""
  fi

  if is_dry; then
    would "remove backups older than $KEEP_DAYS days (only folders this script made, never the newest)"
  else
    prune_backups "$KEEP_DAYS"
  fi

  finish_dry_run
  if ! is_dry; then
    say "Backup saved: $dir"
    info "Copy this folder to another computer or storage bucket: a backup on the same server is lost with the server."
  fi
}

main "$@"
