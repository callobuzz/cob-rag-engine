# shellcheck shell=bash
# Shared plumbing for deploy/install.sh, upgrade.sh, backup.sh and restore.sh.
#
# Sourced, never run. It sets no shell options (the four scripts do that themselves) so the
# small pure functions below can be tried one by one:
#
#   valid_domain, is_dev_host, valid_email, check_os,
#   check_cpus, check_memory_kb, check_disk_kb, port_busy, version_gt, release_version,
#   find_release_root, backup_id, env_value, rollback_help
#
# Each pure function prints its answer (or nothing) and returns 0 or 1 (version_gt: 2 for
# something that is not X.Y.Z). Exit codes of the scripts: 0 done, 1 failed, 2 bad arguments,
# 3 a check failed, 4 refused.

# RAG_INSTALL_DIR only exists so the tests can point the scripts at a throwaway folder.
INSTALL_DIR="${RAG_INSTALL_DIR:-/opt/cob-rag}"
DRY_RUN="${DRY_RUN:-0}"
STEP_TOTAL="${STEP_TOTAL:-0}"
CURRENT_STEP="starting"

if [[ -t 1 ]]; then
  C_BOLD=$'\033[1m'
  C_RED=$'\033[31m'
  C_YELLOW=$'\033[33m'
  C_GREEN=$'\033[32m'
  C_OFF=$'\033[0m'
else
  C_BOLD="" C_RED="" C_YELLOW="" C_GREEN="" C_OFF=""
fi

# ---------------------------------------------------------------------------
# Talking to the person at the keyboard
# ---------------------------------------------------------------------------

say() { printf '%s\n' "$*"; }

warn() { printf '%sWarning:%s %s\n' "$C_YELLOW" "$C_OFF" "$*" >&2; }

ok() { printf '%s%s%s\n' "$C_GREEN" "$*" "$C_OFF"; }

# die CODE MESSAGE HINT: what went wrong, what to do next, and leave.
die() {
  local code=$1 message=$2 hint=${3:-}
  printf '\n%sProblem:%s %s\n' "$C_RED" "$C_OFF" "$message" >&2
  if [[ -n $hint ]]; then
    printf '%sWhat to do:%s %s\n' "$C_BOLD" "$C_OFF" "$hint" >&2
  fi
  exit "$code"
}

# step N TITLE: announce a step in one plain sentence.
step() {
  CURRENT_STEP="$2"
  if ((STEP_TOTAL > 0)); then
    printf '\n%s[%s/%s] %s%s\n' "$C_BOLD" "$1" "$STEP_TOTAL" "$2" "$C_OFF"
  else
    printf '\n%s[%s] %s%s\n' "$C_BOLD" "$1" "$2" "$C_OFF"
  fi
}

# The ERR trap of every script: name the step and say what to do.
on_error() {
  local status=$1 line=$2
  printf '\n%sProblem:%s step "%s" failed (line %s, exit %s).\n' \
    "$C_RED" "$C_OFF" "$CURRENT_STEP" "$line" "$status" >&2
  printf '%sWhat to do:%s read the message above it, fix what it says and run the same command again. Keep this output if you write to support.\n' \
    "$C_BOLD" "$C_OFF" >&2
}

is_dry() { [[ $DRY_RUN == 1 ]]; }

# would TEXT: what a dry run says instead of doing it.
would() { printf 'would: %s\n' "$*"; }

# run DESCRIPTION COMMAND [ARG...]: do it, or only say it under --dry-run. Every write goes
# through here.
run() {
  local description=$1
  shift
  if is_dry; then
    would "$description"
    return 0
  fi
  "$@"
}

finish_dry_run() {
  if is_dry; then
    printf '\nDry run: nothing was changed.\n'
  fi
}

need_root() {
  is_dry && return 0
  if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    die 3 "this has to run as the server's administrator (root)." \
      "run it again with: sudo bash $0"
  fi
}

# Only one install, upgrade, backup or restore at a time. upgrade.sh calls backup.sh with
# COB_RAG_LOCK_HELD=1, so the inner script does not wait for the lock its parent holds.
take_lock() {
  is_dry && return 0
  [[ ${COB_RAG_LOCK_HELD:-0} == 1 ]] && return 0
  mkdir -p "$INSTALL_DIR"
  exec 9>"$INSTALL_DIR/.lock"
  if ! flock -n 9; then
    die 3 "another install, upgrade, backup or restore is running on this server." \
      "wait for it to finish, then run this again."
  fi
  export COB_RAG_LOCK_HELD=1
}

# compose_in RELEASE-DIR ARGS...: docker compose for one release folder (the project name is
# pinned to cob-rag by the compose file, so every release shares the same volumes).
compose_in() {
  local dir=$1
  shift
  docker compose --project-directory "$dir" -f "$dir/docker-compose.yml" "$@"
}

# Every docker compose command runs against the current release.
compose() {
  compose_in "$INSTALL_DIR/current" "$@"
}

# The version running here: what state/install.json says, else the current release's own file.
installed_version() {
  local file=$INSTALL_DIR/state/install.json found=""
  if [[ -r $file ]]; then
    found=$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$file" | head -n 1)
  fi
  if [[ -z $found && -e $INSTALL_DIR/current ]]; then
    found=$(release_version "$INSTALL_DIR/current" 2>/dev/null || true)
  fi
  if [[ -z $found && -L $INSTALL_DIR/current ]]; then
    found=$(basename "$(readlink -f "$INSTALL_DIR/current")")
  fi
  printf '%s\n' "$found"
}

# copy_release SOURCE DEST: the product without development leftovers or any settings file.
copy_release() {
  local source=$1 dest=$2
  mkdir -p "$dest"
  tar -C "$source" --exclude=.git --exclude=.venv --exclude=node_modules --exclude=__pycache__ \
    --exclude=.env --exclude='.env.backup-*' -cf - . | tar -C "$dest" -xf -
}

# run_setup_wizard RELEASE-DIR [--set KEY=VALUE ...]: the stdlib setup wizard inside a throwaway
# Python container (never the server's own Python). Keeps every existing value, generates
# missing secrets, adds new settings with their defaults. Prints its own output only on failure.
run_setup_wizard() {
  local release=$1 log
  shift
  if ! log=$(docker run --rm --network none -v "$INSTALL_DIR:$INSTALL_DIR" python:3.12-slim \
    python "$release/scripts/setup.py" --quick --yes --env-file "$INSTALL_DIR/.env" \
    --template "$release/.env.example" "$@" 2>&1); then
    printf '%s\n' "$log" >&2
    return 1
  fi
  chmod 600 "$INSTALL_DIR/.env"
}

# wait_for URL SECONDS: 0 as soon as the address answers, 1 when time runs out.
wait_for() {
  local url=$1 seconds=$2 waited=0
  while ((waited < seconds)); do
    if curl -fsS -o /dev/null --max-time 5 "$url" 2>/dev/null; then
      printf '\n'
      return 0
    fi
    printf '.'
    sleep 5
    waited=$((waited + 5))
  done
  printf '\n'
  return 1
}

# The engine's address on this server (RAG_APP_HOST_PORT, default 8000).
local_app_url() {
  local port
  port=$(env_value "$INSTALL_DIR/.env" RAG_APP_HOST_PORT 2>/dev/null || true)
  printf 'http://127.0.0.1:%s\n' "${port:-8000}"
}

# ---------------------------------------------------------------------------
# Pure functions (tested in tests/integration/test_deploy_scripts.py)
# ---------------------------------------------------------------------------

# Development addresses: the same rule as core.install.is_dev_domain for names.
is_dev_host() {
  local name=${1,,}
  name=${name%.}
  [[ $name == localhost || $name == *.localhost || $name == *.local || $name == *.test ||
    $name == *.internal || $name == *.lan || $name == *.home.arpa ]]
}

# valid_domain HOST: a public host name; prints it lower-cased and trimmed.
valid_domain() {
  local name=${1:-}
  name=${name#"${name%%[![:space:]]*}"}
  name=${name%"${name##*[![:space:]]}"}
  name=${name,,}
  name=${name%.}
  local label_re='^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$'
  [[ -n $name && ${#name} -le 253 ]] || return 1
  [[ $name != *[/:@\ _\[\]]* ]] || return 1
  local -a labels
  IFS=. read -ra labels <<<"$name"
  ((${#labels[@]} >= 2)) || return 1
  local label
  for label in "${labels[@]}"; do
    [[ $label =~ $label_re ]] || return 1
  done
  [[ ${labels[-1]} =~ ^[0-9]+$ ]] && return 1 # an IPv4 address, not a name
  is_dev_host "$name" && return 1
  printf '%s\n' "$name"
}

# valid_email ADDRESS: the engine's production rule (no .test, .example, .invalid, .localhost, .local).
valid_email() {
  local address=${1:-}
  address=${address#"${address%%[![:space:]]*}"}
  address=${address%"${address##*[![:space:]]}"}
  local re='^[^@[:space:]<>]+@[^@[:space:]<>]+\.[A-Za-z]{2,}$'
  [[ $address =~ $re ]] || return 1
  local domain=${address##*@}
  local tld=${domain##*.}
  case "${tld,,}" in
    local | test | example | localhost | invalid) return 1 ;;
  esac
  printf '%s\n' "$address"
}

check_os() {
  case "${1:-}:${2:-}" in
    ubuntu:22.04 | ubuntu:24.04 | debian:12)
      echo ok
      return 0
      ;;
  esac
  echo unsupported
  return 1
}

# check_cpus N: below 2 stop, below 4 warn.
check_cpus() {
  local n=${1:-0}
  if ((n < 2)); then
    echo too_low
    return 1
  fi
  if ((n < 4)); then
    echo low
    return 0
  fi
  echo ok
}

# check_memory_kb KB: below 3.5 million kB stop ("4 GB" shows as about 3.8 million), below 7.5 million warn.
check_memory_kb() {
  local kb=${1:-0}
  if ((kb < 3500000)); then
    echo too_low
    return 1
  fi
  if ((kb < 7500000)); then
    echo low
    return 0
  fi
  echo ok
}

# check_disk_kb KB of free space: below 20 GiB stop, below 40 GiB warn.
check_disk_kb() {
  local kb=${1:-0}
  if ((kb < 20 * 1024 * 1024)); then
    echo too_low
    return 1
  fi
  if ((kb < 40 * 1024 * 1024)); then
    echo low
    return 0
  fi
  echo ok
}

# port_busy PORT SS_OUTPUT: SS_OUTPUT is `ss -ltnHp`. Prints the program holding the port.
port_busy() {
  local port=$1 listing=$2 line prog
  local IFS=$' \t'
  local -a fields
  while IFS= read -r line; do
    read -ra fields <<<"$line"
    [[ ${fields[3]:-} == *:"$port" ]] || continue
    prog=$(sed -n 's/.*users:(("\([^"]*\)".*/\1/p' <<<"$line")
    printf '%s\n' "${prog:-another program}"
    return 0
  done <<<"$listing"
  return 1
}

# version_gt A B: 0 when A is newer than B, 1 when not, 2 when either is not X.Y.Z.
version_gt() {
  local re='^[0-9]+\.[0-9]+\.[0-9]+$'
  [[ ${1:-} =~ $re && ${2:-} =~ $re ]] || return 2
  local IFS=.
  local -a a b
  read -ra a <<<"$1"
  read -ra b <<<"$2"
  local i
  for i in 0 1 2; do
    ((10#${a[i]} > 10#${b[i]})) && return 0
    ((10#${a[i]} < 10#${b[i]})) && return 1
  done
  return 1
}

is_release_dir() { [[ -f $1/pyproject.toml && -f $1/docker-compose.yml ]]; }

# release_version DIR: the version in DIR/pyproject.toml.
release_version() {
  local found
  found=$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$1/pyproject.toml" 2>/dev/null | head -n 1)
  [[ -n $found ]] || return 1
  printf '%s\n' "$found"
}

# find_release_root DIR: DIR itself, or the product folder one or two levels inside it (a zip).
find_release_root() {
  local dir=${1%/} candidate
  if is_release_dir "$dir"; then
    printf '%s\n' "$dir"
    return 0
  fi
  for candidate in "$dir"/*/ "$dir"/*/*/; do
    [[ -d $candidate ]] || continue
    candidate=${candidate%/}
    if is_release_dir "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

# backup_id EPOCH VERSION: 20261007T121000Z-0.1.0
backup_id() {
  local stamp
  stamp=$(date -u -d "@$1" +%Y%m%dT%H%M%SZ) || return 1
  printf '%s-%s\n' "$stamp" "$2"
}

# env_value FILE KEY: the last KEY=value of the settings file, without surrounding quotes.
env_value() {
  local file=$1 key=$2 line value="" found=0
  [[ -r $file ]] || return 1
  while IFS= read -r line || [[ -n $line ]]; do
    if [[ $line == "$key="* ]]; then
      value=${line#*=}
      found=1
    fi
  done <"$file"
  ((found == 1)) || return 1
  if ((${#value} >= 2)); then
    local first=${value:0:1} last=${value: -1}
    if [[ $first == "$last" && ($first == '"' || $first == "'") ]]; then
      value=${value:1:${#value}-2}
    fi
  fi
  printf '%s\n' "$value"
}

# The one command that puts the old version and its data back (printed whenever an upgrade fails).
# rollback_help OLD_VERSION BACKUP_ID
rollback_help() {
  local old=$1 backup=$2
  cat <<EOF
Your data is safe: a backup was taken before anything changed.
To go back to version $old and the data as it was, run:

  sudo bash $INSTALL_DIR/releases/$old/deploy/upgrade.sh --rollback

That backup is in $INSTALL_DIR/backups/$backup
EOF
}
