#!/usr/bin/env bash
# Update cob-rag-engine to a new version, or go back to the one before.
#
#   sudo bash /opt/cob-rag/current/deploy/upgrade.sh <the release zip or a folder> [options]
#   sudo bash /opt/cob-rag/current/deploy/upgrade.sh --rollback
#
# An upgrade: back up everything, put the new version beside the old one, build it while the
# old one keeps answering, bring the settings file up to date, update the database, switch,
# start, check it is healthy. If anything fails after the backup, the exact command that puts
# the old version and its data back is printed (it is --rollback).
# Exit codes: 0 done, 1 failed, 2 bad arguments, 3 a check failed, 4 refused (same or older
# version, or the word was not typed).

set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source-path=SCRIPTDIR source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
trap 'on_error $? $LINENO' ERR

ARG_SOURCE="" ARG_YES=0 ARG_FORCE=0 ARG_SKIP_BACKUP=0 ARG_HAVE_BACKUP=0 ARG_ROLLBACK=0
IMAGES=(cob-rag cob-rag-scraper cob-rag-dashboard)
OLD_VERSION="" NEW_VERSION="" BACKUP_ID="" STARTED_AT="" SWITCHED=0 PROTECTED=0

usage() {
  cat <<'EOF'
Update to a new version, or roll back the last update.

  sudo bash deploy/upgrade.sh <the release zip or a folder> [options]
  sudo bash deploy/upgrade.sh --rollback [--yes]

  --yes                          do not ask before starting
  --dry-run                      say what would happen, change nothing
  --force                        install a version that is the same or older than the running one
  --skip-backup --i-have-a-backup  skip the automatic backup (only if you made your own just now)
  --rollback                     put back the version and data from before the last update
EOF
}

parse_args() {
  while (($#)); do
    case $1 in
      --yes | -y) ARG_YES=1; shift ;;
      --dry-run) DRY_RUN=1; shift ;;
      --force) ARG_FORCE=1; shift ;;
      --skip-backup) ARG_SKIP_BACKUP=1; shift ;;
      --i-have-a-backup) ARG_HAVE_BACKUP=1; shift ;;
      --rollback) ARG_ROLLBACK=1; shift ;;
      -h | --help) usage; exit 0 ;;
      -*) die 2 "Unknown option: $1" "run: bash deploy/upgrade.sh --help" ;;
      *)
        [[ -z $ARG_SOURCE ]] || die 2 "Only one new version can be given." "run: bash deploy/upgrade.sh --help"
        ARG_SOURCE=$1
        shift
        ;;
    esac
  done
  if ((ARG_SKIP_BACKUP == 1 && ARG_HAVE_BACKUP == 0)); then
    die 2 "--skip-backup also needs --i-have-a-backup." \
      "the automatic backup is what makes a failed update reversible. Skip it only if you took your own backup a moment ago."
  fi
  if ((ARG_ROLLBACK == 0)) && [[ -z $ARG_SOURCE ]]; then
    die 2 "no new version was given." \
      "run: sudo bash deploy/upgrade.sh <the release zip or a folder>   (or --rollback)"
  fi
}

json_value() {
  # json_value FILE KEY: a plain string value from one of our own small state files.
  sed -n "s/.*\"$2\": *\"\\([^\"]*\\)\".*/\\1/p" "$1" 2>/dev/null | head -n 1
}

record_upgrade() {
  # record_upgrade RESULT [FINISHED]
  local result=$1 finished=${2:-}
  printf '{"from": "%s", "to": "%s", "backup": "%s", "started_at": "%s", "finished_at": "%s", "result": "%s"}\n' \
    "$OLD_VERSION" "$NEW_VERSION" "$BACKUP_ID" "$STARTED_AT" "$finished" "$result" \
    >"$INSTALL_DIR/state/last-upgrade.json"
}

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# Removes the folder a zip was unpacked into (it is the whole product), whatever way the run ends.
INCOMING=""
cleanup_incoming() {
  if [[ -n ${INCOMING:-} && $INCOMING == "$INSTALL_DIR/releases/.incoming-"* && -d $INCOMING ]]; then
    rm -rf -- "$INCOMING"
  fi
  INCOMING=""
}

# After the backup, any way of leaving with an error prints the one command that undoes everything.
on_upgrade_exit() {
  local status=$?
  cleanup_incoming
  if ((status == 0 || PROTECTED == 0)); then
    return 0
  fi
  PROTECTED=0
  record_upgrade failed "$(now)" || true
  if ((SWITCHED == 0)); then
    printf '\nThe version that was running before is still the one in use.\n' >&2
  fi
  rollback_help "$OLD_VERSION" "$BACKUP_ID" >&2
}

newest_backup() {
  find "$INSTALL_DIR/backups" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null |
    grep -E '^[0-9]{8}T[0-9]{6}Z-[0-9]+\.[0-9]+\.[0-9]+$' | sort | tail -n 1 || true
}

zip_version() {
  # The version inside a zip without unpacking it (the shallowest pyproject.toml).
  local entry
  entry=$(unzip -Z1 "$1" 2>/dev/null | grep -E '(^|/)pyproject\.toml$' | awk '{print length, $0}' | sort -n | head -n 1 | cut -d' ' -f2-)
  [[ -n $entry ]] || return 1
  unzip -p "$1" "$entry" | sed -n 's/^version *= *"\([^"]*\)".*/\1/p' | head -n 1
}

# Sets NEW_SOURCE (a folder with the new product) and NEW_VERSION.
resolve_source() {
  step 2 "Looking at the new version."
  NEW_SOURCE=""
  if [[ -f $ARG_SOURCE ]]; then
    case $ARG_SOURCE in
      *.zip) ;;
      *) die 2 "'$ARG_SOURCE' is not a .zip file or a folder." "give the release zip, or a folder with the cob-rag-engine code (for example a git clone)." ;;
    esac
    command -v unzip >/dev/null 2>&1 || die 3 "unzip is not installed." "install it with: sudo apt-get install -y unzip"
    NEW_VERSION=$(zip_version "$ARG_SOURCE") ||
      die 2 "'$ARG_SOURCE' does not contain the cob-rag-engine product." "get the release zip again."
    if is_dry; then
      would "unpack $ARG_SOURCE into $INSTALL_DIR/releases/.incoming-<time>"
      return 0
    fi
    local incoming
    incoming=$INSTALL_DIR/releases/.incoming-$(date -u +%Y%m%dT%H%M%SZ)
    INCOMING=$incoming
    trap cleanup_incoming EXIT
    mkdir -p "$incoming"
    unzip -q "$ARG_SOURCE" -d "$incoming"
    NEW_SOURCE=$(find_release_root "$incoming") ||
      die 2 "the zip does not contain a cob-rag-engine folder." "get the release zip again."
  elif [[ -d $ARG_SOURCE ]]; then
    NEW_SOURCE=$(find_release_root "$ARG_SOURCE") ||
      die 2 "'$ARG_SOURCE' does not contain a cob-rag-engine folder (a folder with pyproject.toml and docker-compose.yml)." \
        "give the release zip, or a folder with the cob-rag-engine code (for example a git clone)."
    NEW_VERSION=$(release_version "$NEW_SOURCE") ||
      die 2 "could not read the version from $NEW_SOURCE/pyproject.toml." "get the code again."
  else
    die 2 "'$ARG_SOURCE' does not exist." "give the release zip, or a folder with the cob-rag-engine code (for example a git clone)."
  fi
}

check_versions() {
  say "  Version: $OLD_VERSION → $NEW_VERSION"
  if ! version_gt "$NEW_VERSION" "$OLD_VERSION"; then
    if ((ARG_FORCE == 1)); then
      warn "this is not newer than the running version; going on because of --force."
    else
      die 4 "version $OLD_VERSION is already running here, and $NEW_VERSION is not newer." \
        "to install it anyway, add --force."
    fi
  fi
  local disk_kb
  disk_kb=$(df -Pk "$INSTALL_DIR" 2>/dev/null | awk 'NR==2 {print $4}')
  disk_kb=${disk_kb:-0}
  if ((disk_kb < 10 * 1024 * 1024)); then
    if is_dry; then
      warn "less than 10 GB is free; a real run would stop here."
    else
      die 3 "less than 10 GB of disk space is free ($((disk_kb / 1024 / 1024)) GB)." \
        "free up space (old backups are in $INSTALL_DIR/backups), then run this again."
    fi
  fi
}

confirm_upgrade() {
  ((ARG_YES == 1)) && return 0
  printf '\nGo ahead with the upgrade to %s? A backup is taken first. [y/N] ' "$NEW_VERSION"
  local reply=""
  read -r reply || true
  if [[ ${reply,,} != y && ${reply,,} != yes ]]; then
    die 4 "the upgrade was not confirmed." "nothing was changed."
  fi
}

tag_rollback_images() {
  local image
  for image in "${IMAGES[@]}"; do
    if docker image inspect "$image:local" >/dev/null 2>&1; then
      docker tag "$image:local" "$image:rollback"
    fi
  done
}

prune_releases() {
  # Keep the current release and the two before it; only folders named like a version.
  local current name
  current=$(basename "$(readlink -f "$INSTALL_DIR/current")")
  local -a all
  mapfile -t all < <(find "$INSTALL_DIR/releases" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' |
    grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' | sort -V)
  local keep=3 count=${#all[@]} index=0
  for name in "${all[@]}"; do
    index=$((index + 1))
    [[ $name == "$current" ]] && continue
    if ((count - index >= keep)); then
      rm -rf -- "${INSTALL_DIR:?}/releases/$name"
      say "  Removed the old release folder $name."
    fi
  done
}

write_install_state() {
  # write_install_state VERSION: keeps the address and the first install date.
  local file=$INSTALL_DIR/state/install.json domain installed_at
  domain=$(json_value "$file" domain)
  installed_at=$(json_value "$file" installed_at)
  printf '{"domain": "%s", "version": "%s", "installed_at": "%s"}\n' \
    "$domain" "$1" "${installed_at:-$(now)}" >"$file"
}

do_upgrade() {
  local dest=$INSTALL_DIR/releases/$NEW_VERSION
  STARTED_AT=$(now)

  step 3 "Backing up everything first."
  if ((ARG_SKIP_BACKUP == 1)); then
    warn "skipping the backup as you asked. If the update fails you will have to restore your own."
  elif is_dry; then
    would "run deploy/backup.sh (database, uploaded files, search index and settings)"
  else
    COB_RAG_LOCK_HELD=1 bash "$INSTALL_DIR/current/deploy/backup.sh"
    BACKUP_ID=$(newest_backup)
    [[ -n $BACKUP_ID ]] || die 1 "the backup did not produce a folder." "run deploy/backup.sh by hand and read its message."
  fi
  if ! is_dry; then
    record_upgrade started
    ((ARG_SKIP_BACKUP == 1)) || PROTECTED=1
    trap on_upgrade_exit EXIT
  fi

  step 4 "Putting the new version beside the old one."
  if [[ -e $dest ]] && ! is_dry; then
    local aside
    aside=$dest.old-$(date -u +%Y%m%dT%H%M%SZ)
    mv "$dest" "$aside"
    say "  A folder for $NEW_VERSION already existed; it was moved to $aside"
  fi
  if [[ ${NEW_SOURCE:-} == "$INSTALL_DIR/releases/.incoming-"* ]] && ! is_dry; then
    mv "$NEW_SOURCE" "$dest"
    cleanup_incoming
  else
    run "copy the new version to $dest" copy_release "${NEW_SOURCE:-$ARG_SOURCE}" "$dest"
  fi
  run "link the release to the one settings file ($INSTALL_DIR/.env)" ln -sfn ../../.env "$dest/.env"
  run "tag the current images as :rollback (so going back needs no rebuild)" tag_rollback_images

  step 5 "Building the new version (the old one keeps answering)."
  if is_dry; then
    would "docker compose build (the new version, 5 to 15 minutes)"
  else
    compose_in "$dest" build
  fi

  step 6 "Bringing the settings file up to date."
  if is_dry; then
    would "update $INSTALL_DIR/.env with the new settings (existing values stay, the old file is backed up first)"
  else
    run_setup_wizard "$dest"
  fi

  step 7 "Updating the database."
  if is_dry; then
    would "alembic upgrade head (before anything restarts, so a problem shows up now)"
  else
    compose_in "$dest" run --rm --no-deps -T --entrypoint alembic app upgrade head
  fi

  step 8 "Switching to the new version."
  run "switch $INSTALL_DIR/current to $dest" ln -sfn "$dest" "$INSTALL_DIR/current"
  SWITCHED=1
  if is_dry; then
    would "docker compose up -d --remove-orphans"
  else
    compose up -d --remove-orphans
  fi

  step 9 "Checking it is healthy."
  if is_dry; then
    would "wait for the engine to answer (up to 10 minutes)"
  else
    if ! wait_for "$(local_app_url)/api/v1/health/ready" 600; then
      die 1 "the new version did not become ready in 10 minutes." \
        "look at the reason with: docker compose --project-directory $INSTALL_DIR/current logs --tail 100 app"
    fi
    write_install_state "$NEW_VERSION"
    record_upgrade ok "$(now)"
    PROTECTED=0
    prune_releases
  fi
}

do_rollback() {
  local state=$INSTALL_DIR/state/last-upgrade.json
  [[ -r $state ]] ||
    die 3 "there is no recorded upgrade to roll back." "restore a backup by hand with deploy/restore.sh."
  OLD_VERSION=$(json_value "$state" from)
  NEW_VERSION=$(json_value "$state" to)
  BACKUP_ID=$(json_value "$state" backup)
  STARTED_AT=$(json_value "$state" started_at)
  local back_to=$OLD_VERSION running
  running=$(basename "$(readlink -f "$INSTALL_DIR/current")")
  local release=$INSTALL_DIR/releases/$back_to
  [[ -n $back_to && -d $release ]] ||
    die 3 "the folder of the previous version ($back_to) is gone." "restore a backup by hand with deploy/restore.sh."
  [[ -n $BACKUP_ID ]] ||
    die 3 "that upgrade was made without a backup, so there is nothing to roll back to." \
      "restore a backup of your own with deploy/restore.sh <backup folder>."
  if [[ ! -d $INSTALL_DIR/backups/$BACKUP_ID ]]; then
    if is_dry; then
      warn "the backup folder $BACKUP_ID is not there; a real run would stop here."
    else
      die 3 "the backup taken before that upgrade is not there ($BACKUP_ID)." \
        "restore a backup of your own with deploy/restore.sh <backup folder>."
    fi
  fi

  step 1 "Rolling back."
  say "  Version: $running → $back_to, with the data as it was in $BACKUP_ID"
  if ((ARG_YES == 0)); then
    printf 'This REPLACES the current data with the backup. Type rollback to continue: '
    local reply=""
    read -r reply || true
    printf '\n'
    [[ $reply == rollback ]] || die 4 "the word 'rollback' was not typed." "nothing was changed."
  fi

  run "switch $INSTALL_DIR/current to $release" ln -sfn "$release" "$INSTALL_DIR/current"
  if is_dry; then
    would "put the :rollback images back as the running ones (or rebuild them if they are missing)"
    would "run deploy/restore.sh $INSTALL_DIR/backups/$BACKUP_ID --yes"
  else
    local image have_all=1
    for image in "${IMAGES[@]}"; do
      if docker image inspect "$image:rollback" >/dev/null 2>&1; then
        docker tag "$image:rollback" "$image:local"
      else
        have_all=0
      fi
    done
    if ((have_all == 0)); then
      say "  Some saved images are missing; rebuilding $back_to (this takes a few minutes)."
      compose build
    fi
    COB_RAG_LOCK_HELD=1 bash "$release/deploy/restore.sh" "$INSTALL_DIR/backups/$BACKUP_ID" --yes
    write_install_state "$back_to"
    OLD_VERSION=$back_to
    NEW_VERSION=$running
    record_upgrade rolled_back "$(now)"
    ok "Rolled back to $back_to."
  fi
}

main() {
  parse_args "$@"
  need_root
  take_lock
  [[ -e $INSTALL_DIR/current ]] ||
    die 3 "no install was found in $INSTALL_DIR." "run deploy/install.sh first. Upgrades are for an existing install."
  is_dry || mkdir -p "$INSTALL_DIR/state"
  OLD_VERSION=$(installed_version)

  if ((ARG_ROLLBACK == 1)); then
    do_rollback
    finish_dry_run
    return 0
  fi

  say "Upgrading cob-rag-engine."
  resolve_source
  check_versions
  confirm_upgrade
  do_upgrade
  finish_dry_run
  if ! is_dry; then
    ok "Upgraded to $NEW_VERSION."
    say "Your backup from before the upgrade: $INSTALL_DIR/backups/$BACKUP_ID"
    say "If something looks wrong: sudo bash $INSTALL_DIR/releases/$NEW_VERSION/deploy/upgrade.sh --rollback"
  fi
}

main "$@"
