#!/usr/bin/env bash
# Put a backup made by deploy/backup.sh back: database, uploaded files, crawler data and the
# search index. THIS REPLACES what is on the server now.
#
#   sudo bash /opt/cob-rag/current/deploy/restore.sh <backup folder> [--yes] [--with-env] [--force] [--dry-run]
#
# The checksums are verified first. A backup made by a newer version than the one running is
# refused unless --force (an older backup into newer code is fine: the database upgrades itself
# when the engine starts). The settings file is only restored with --with-env; the current one
# is kept next to it first. Exit codes: 0 done, 1 failed, 2 bad arguments, 3 a check failed
# (damaged backup, nothing running), 4 refused (newer backup, or the word was not typed).

set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
trap 'on_error $? $LINENO' ERR

FOLDER="" ARG_YES=0 ARG_WITH_ENV=0 ARG_FORCE=0

usage() {
  cat <<'EOF'
Restore a backup. This replaces the data on this server.

  sudo bash deploy/restore.sh <backup folder> [options]

  --yes        do not ask for the typed confirmation
  --with-env   also restore the settings file from the backup (the current one is kept)
  --force      restore a backup made by a newer version than the one running
  --dry-run    say what would happen, change nothing
EOF
}

parse_args() {
  while (($#)); do
    case $1 in
      --yes | -y) ARG_YES=1; shift ;;
      --with-env) ARG_WITH_ENV=1; shift ;;
      --force) ARG_FORCE=1; shift ;;
      --dry-run) DRY_RUN=1; shift ;;
      -h | --help) usage; exit 0 ;;
      -*) die 2 "Unknown option: $1" "run: bash deploy/restore.sh --help" ;;
      *)
        [[ -z $FOLDER ]] || die 2 "Only one backup folder can be restored at a time." "run: bash deploy/restore.sh --help"
        FOLDER=${1%/}
        shift
        ;;
    esac
  done
  [[ -n $FOLDER ]] ||
    die 2 "no backup folder was given." "for example: sudo bash deploy/restore.sh $INSTALL_DIR/backups/<the backup you want>"
  [[ -d $FOLDER ]] ||
    die 2 "'$FOLDER' is not a folder." "list your backups with: ls $INSTALL_DIR/backups"
}

verify_backup() {
  [[ -f $FOLDER/SHA256SUMS && -f $FOLDER/manifest.json ]] ||
    die 3 "$FOLDER is not a finished backup (no manifest or checksum list)." \
      "choose a folder made by backup.sh; a backup that stopped halfway is removed automatically."
  if ! (cd "$FOLDER" && sha256sum --check --quiet SHA256SUMS >/dev/null 2>&1); then
    die 3 "this backup is damaged: a file does not match its checksum." \
      "use another backup. If it was copied from elsewhere, copy it again."
  fi
  say "  The backup is complete and its checksums match."
}

check_version() {
  local backup_version running
  backup_version=$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$FOLDER/manifest.json" | head -n 1)
  running=$(installed_version)
  say "  Backup made by version ${backup_version:-unknown}; this server runs ${running:-unknown}."
  if [[ -n $backup_version && -n $running ]] && version_gt "$backup_version" "$running"; then
    if ((ARG_FORCE == 1)); then
      warn "the backup is from a newer version; going on because of --force."
    else
      die 4 "this backup is from a newer version ($backup_version) than the one running ($running)." \
        "update first with deploy/upgrade.sh, then restore. Or use --force if you know what you are doing."
    fi
  fi
}

confirm() {
  ((ARG_YES == 1)) && return 0
  say ""
  say "This REPLACES the database, uploaded files and search index on this server with the ones in:"
  say "  $FOLDER"
  printf 'Type restore to continue (anything else stops here): '
  local reply=""
  read -r reply || true
  printf '\n'
  if [[ $reply != restore ]]; then
    die 4 "the word 'restore' was not typed." "nothing was changed."
  fi
}

untar_into() {
  # untar_into SERVICE FOLDER-IN-CONTAINER TAR-FILE-IN-BACKUP
  compose run --rm --no-deps -T --user 0 -v "$FOLDER:/backup:ro" --entrypoint sh "$1" -c \
    "find '$2' -mindepth 1 -delete && tar -C '$2' -xf '/backup/$3'"
}

restore_database() {
  # The database is recreated, not patched: a newer migration may have added tables that point
  # at the dumped ones, and --clean would fail on them and leave the data half-restored.
  compose exec -T postgres psql -U rag -d postgres -v ON_ERROR_STOP=1 \
    -c 'DROP DATABASE IF EXISTS rag_kernel WITH (FORCE);' \
    -c 'CREATE DATABASE rag_kernel OWNER rag;'
  compose exec -T postgres pg_restore -U rag -d rag_kernel --no-owner --exit-on-error <"$FOLDER/postgres.dump"
}

restore_vectors() {
  compose run --rm --no-deps -T --user 0 -v "$FOLDER/qdrant:/backup:ro" --entrypoint python app \
    -m callobuzz_rag.vectordb.snapshots restore /backup
}

restore_settings() {
  local saved
  saved=$INSTALL_DIR/.env.before-restore-$(basename "$FOLDER")
  install -m 600 "$INSTALL_DIR/.env" "$saved"
  install -m 600 "$FOLDER/env" "$INSTALL_DIR/.env"
  say "  The settings from the backup are in place; the ones before are kept in $saved"
}

warn_if_keys_differ() {
  [[ -f $FOLDER/env ]] || return 0
  local then_key now_key
  then_key=$(env_value "$FOLDER/env" RAG_ENCRYPTION_KEY 2>/dev/null || true)
  now_key=$(env_value "$INSTALL_DIR/.env" RAG_ENCRYPTION_KEY 2>/dev/null || true)
  if [[ -n $then_key && $then_key != "$now_key" ]]; then
    if ((ARG_WITH_ENV == 1)); then
      say "  The encryption key now matches the backup, so saved provider keys can be read."
    else
      warn "this backup was made with a different encryption key than this server uses. Stored keys (AI provider, WhatsApp) cannot be read without it. Run again with --with-env to restore the backup's settings too."
    fi
  fi
}

main() {
  parse_args "$@"
  need_root
  take_lock
  [[ -e $INSTALL_DIR/current ]] ||
    die 3 "no install was found in $INSTALL_DIR." "run deploy/install.sh first, then restore."
  say "Restoring from $FOLDER"
  verify_backup
  check_version
  warn_if_keys_differ
  confirm

  local has_files=0
  [[ -f $FOLDER/files.tar ]] && has_files=1

  if is_dry; then
    would "stop the engine, workers and crawler (the databases keep running)"
    would "restore the database from postgres.dump (the database is recreated first)"
    ((has_files == 1)) && would "replace the uploaded files with files.tar"
    [[ -f $FOLDER/scraper.tar ]] && would "replace the crawler's data with scraper.tar"
    [[ -d $FOLDER/qdrant ]] && would "restore the search index: python -m callobuzz_rag.vectordb.snapshots restore"
    ((ARG_WITH_ENV == 1)) && would "restore the settings file from env (the current one is kept)"
    would "start everything again (docker compose up -d) and wait for it"
    finish_dry_run
    return 0
  fi

  say "  Stopping the engine, workers and crawler..."
  compose stop app worker mailer celery-beat scraper
  say "  Restoring the database..."
  restore_database
  if ((has_files == 1)); then
    say "  Restoring the uploaded files..."
    untar_into app /var/lib/cob-rag/files files.tar
  fi
  if [[ -f $FOLDER/scraper.tar ]]; then
    say "  Restoring the crawler's data..."
    untar_into scraper /data scraper.tar
  fi
  if [[ -d $FOLDER/qdrant ]]; then
    say "  Restoring the search index..."
    restore_vectors
  fi
  if ((ARG_WITH_ENV == 1)) && [[ -f $FOLDER/env ]]; then
    restore_settings
  fi
  say "  Starting everything again..."
  compose up -d --remove-orphans
  if wait_for "$(local_app_url)/api/v1/health/ready" 600; then
    ok "Restore finished. Your data is back as it was in that backup."
  else
    die 1 "everything was restored, but the engine did not become ready in 10 minutes." \
      "look at the reason with: docker compose --project-directory $INSTALL_DIR/current logs --tail 100 app"
  fi
}

main "$@"
