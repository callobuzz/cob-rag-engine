#!/usr/bin/env bash
# One-command installer for cob-rag-engine on a fresh Ubuntu or Debian server.
#
#   sudo bash deploy/install.sh
#
# It checks the server, installs Docker if it is missing, asks two questions (your web
# address and your e-mail), writes the settings, starts everything behind
# Caddy with automatic HTTPS, and prints the link you sign in with. Running it again changes
# nothing that works. Windows and macOS are for trying the product on your own computer; see
# docs/guide.html.
#
# Options: see --help. Exit codes: 0 done, 1 failed, 2 bad arguments, 3 a check failed,
# 4 refused (another version is installed: use upgrade.sh).

set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
trap 'on_error $? $LINENO' ERR

OS_RELEASE="${OS_RELEASE:-/etc/os-release}"
STEP_TOTAL=17

ARG_DOMAIN="" ARG_EMAIL="" ARG_YES=0
ARG_SOURCE="" ARG_NO_DOCKER=0 ARG_NO_SWAP=0 ARG_NO_CRON=0 ARG_FORCE_OS=0
ANSWER=""

usage() {
  cat <<'EOF'
Install cob-rag-engine on this server.

  sudo bash deploy/install.sh [options]

  --domain NAME           your web address, for example chat.sharmafurniture.in
  --email ADDRESS         your e-mail (you sign in with it)
  --yes                   ask nothing; use the options and what is already stored
  --dry-run               say what would happen, change nothing
  --source DIR            the product folder (default: the folder this script is in)
  --no-docker-install     stop instead of installing Docker
  --no-swap               do not add a 2 GB swap file on a small server
  --no-backup-cron        do not add the nightly backup
  --force-os              try an operating system that is not on the tested list
  -h, --help              this text
EOF
}

need_value() {
  if (($# < 2)); then
    die 2 "$1 needs a value." "run: bash deploy/install.sh --help"
  fi
}

parse_args() {
  while (($#)); do
    case $1 in
      --domain) need_value "$@"; ARG_DOMAIN=$2; shift 2 ;;
      --email) need_value "$@"; ARG_EMAIL=$2; shift 2 ;;
      --source) need_value "$@"; ARG_SOURCE=$2; shift 2 ;;
      --yes | -y) ARG_YES=1; shift ;;
      --dry-run) DRY_RUN=1; shift ;;
      --no-docker-install) ARG_NO_DOCKER=1; shift ;;
      --no-swap) ARG_NO_SWAP=1; shift ;;
      --no-backup-cron) ARG_NO_CRON=1; shift ;;
      --force-os) ARG_FORCE_OS=1; shift ;;
      -h | --help) usage; exit 0 ;;
      *) die 2 "Unknown option: $1" "run: bash deploy/install.sh --help" ;;
    esac
  done
}

# Answers given on the command line are checked at once, before anything else happens.
check_given_answers() {
  local cleaned
  if [[ -n $ARG_DOMAIN ]]; then
    cleaned=$(valid_domain "$ARG_DOMAIN") ||
      die 2 "'$ARG_DOMAIN' is not a public web address." \
        "use a name you own, like chat.sharmafurniture.in: no https://, no port, no IP address, no .local or .test name."
    ARG_DOMAIN=$cleaned
  fi
  if [[ -n $ARG_EMAIL ]]; then
    cleaned=$(valid_email "$ARG_EMAIL") ||
      die 2 "'$ARG_EMAIL' is not a real e-mail address." \
        "use one that can receive mail, like owner@sharmafurniture.in (not .test, .example or .local)."
    ARG_EMAIL=$cleaned
  fi
}

# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

# os_field KEY: one value from the operating system's release file (read, never run).
os_field() {
  local line
  line=$(grep -m 1 "^$1=" "$OS_RELEASE" || true)
  line=${line#*=}
  printf '%s\n' "${line//\"/}"
}

check_linux_and_os() {
  step 2 "Checking this server runs Linux and a tested version of it."
  if [[ $(uname -s) != Linux ]]; then
    die 3 "a live server has to run Linux. Windows and macOS are for trying the product on your own computer." \
      "follow docs/guide.html on your computer, or rent a Linux server (Ubuntu 24.04 is a good choice) and run this there."
  fi
  local id="" version_id=""
  if [[ -r $OS_RELEASE ]]; then
    id=$(os_field ID)
    version_id=$(os_field VERSION_ID)
  fi
  if ! check_os "$id" "$version_id" >/dev/null; then
    if ((ARG_FORCE_OS == 1)); then
      warn "$id $version_id is not on the tested list; going on because of --force-os."
    else
      die 3 "this installer is tested on Ubuntu 22.04 or 24.04, or Debian 12 (found: ${id:-unknown} ${version_id:-})." \
        "use one of those, or run again with --force-os to try anyway."
    fi
  fi
  case $(uname -m) in
    x86_64 | amd64) ;;
    aarch64 | arm64) warn "ARM servers are not tested yet. It may work; tell support what happens." ;;
    *) die 3 "this server's processor type ($(uname -m)) is not supported." "use a 64-bit Intel or AMD server." ;;
  esac
}

# size_check LABEL RESULT MESSAGE-WHEN-TOO-LOW MESSAGE-WHEN-LOW
size_check() {
  local label=$1 result=$2 too_low=$3 low=$4
  case $result in
    too_low)
      if is_dry; then
        warn "$label: $too_low (a real run would stop here)"
      else
        die 3 "$label: $too_low" "rent a bigger server (see docs/requirements.html) and run this there."
      fi
      ;;
    low) warn "$label: $low" ;;
    *) say "  $label: fine" ;;
  esac
}

check_size() {
  step 3 "Checking this server is big enough (processors, memory, disk space)."
  local cpus mem_kb disk_path disk_kb result
  cpus=$(nproc)
  result=$(check_cpus "$cpus") || true
  size_check "Processors ($cpus)" "$result" \
    "at least 2 are needed." "2 works, 4 or more is comfortable."
  mem_kb=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)
  result=$(check_memory_kb "$mem_kb") || true
  size_check "Memory ($((mem_kb / 1024)) MB)" "$result" \
    "at least 4 GB is needed." "4 GB is the minimum, 8 GB or more is comfortable."
  disk_path=/
  [[ -d /var/lib/docker ]] && disk_path=/var/lib/docker
  disk_kb=$(df -Pk "$disk_path" | awk 'NR==2 {print $4}')
  result=$(check_disk_kb "$disk_kb") || true
  size_check "Free disk space ($((disk_kb / 1024 / 1024)) GB)" "$result" \
    "at least 20 GB free is needed." "40 GB or more free is comfortable."
}

RERUN=0
check_existing_install() {
  step 4 "Looking for an earlier install on this server."
  if command -v docker >/dev/null 2>&1; then
    local dirs dir
    dirs=$(docker ps -a --filter label=com.docker.compose.project=cob-rag \
      --format '{{.Label "com.docker.compose.project.working_dir"}}' 2>/dev/null | sort -u) || dirs=""
    for dir in $dirs; do
      case $dir in
        "$INSTALL_DIR"/* | "$INSTALL_DIR") ;;
        *)
          die 3 "this server already runs cob-rag-engine from $dir." \
            "update that one by hand (docs/deploy.html, section 10). Two copies on one server would share the same data."
          ;;
      esac
    done
  fi
  if [[ -e $INSTALL_DIR/current || -L $INSTALL_DIR/current ]]; then
    local have
    have=$(installed_version)
    if [[ -n $have && $have != "$VERSION" ]]; then
      die 4 "version $have is installed here, and this folder is version $VERSION." \
        "to change versions use: sudo bash $INSTALL_DIR/current/deploy/upgrade.sh <this folder or zip>"
    fi
    RERUN=1
    say "  Found this version ($VERSION) already installed: this run only makes sure everything is in place."
  else
    say "  Nothing installed yet. Fresh install."
  fi
}

check_ports() {
  step 5 "Checking ports 80 and 443 are free (the web address needs them)."
  if ! command -v ss >/dev/null 2>&1; then
    if is_dry; then
      warn "the 'ss' tool is missing, so ports cannot be checked in a dry run."
      return 0
    fi
    apt-get install -y iproute2 >/dev/null
  fi
  local listing port holder ours=0
  listing=$(ss -ltnHp 2>/dev/null || true)
  if command -v docker >/dev/null 2>&1 &&
    [[ -n $(docker ps -q --filter label=com.docker.compose.project=cob-rag \
      --filter label=com.docker.compose.service=caddy 2>/dev/null) ]]; then
    ours=1
  fi
  for port in 80 443; do
    if holder=$(port_busy "$port" "$listing"); then
      if ((ours == 1 && RERUN == 1)) && [[ $holder == docker-proxy ]]; then
        say "  Port $port is held by this install's own web front door. Fine."
      else
        die 3 "$holder is using port $port." \
          "stop it (for example: sudo systemctl disable --now $holder) or use a server where ports 80 and 443 are free, then run this again."
      fi
    fi
  done
}

# prompt_answer PROMPT VALIDATOR BAD-ANSWER-TEXT: sets ANSWER, asks again on a bad reply.
prompt_answer() {
  local prompt=$1 validator=$2 bad=$3 reply cleaned
  while true; do
    read -r -p "$prompt " reply || die 2 "no answer was given." "run again and type your answer, or give it with an option (--help)."
    if cleaned=$("$validator" "$reply"); then
      ANSWER=$cleaned
      return 0
    fi
    say "  $bad"
  done
}

DOMAIN="" EMAIL=""
ask_questions() {
  step 6 "Two questions (everything else is set up for you)."
  local settings=$INSTALL_DIR/.env stored=""
  DOMAIN=$ARG_DOMAIN EMAIL=$ARG_EMAIL
  if ((RERUN == 1)); then
    [[ -n $DOMAIN ]] || DOMAIN=$(env_value "$settings" RAG_SITE_DOMAIN || true)
    [[ -n $EMAIL ]] || EMAIL=$(env_value "$settings" RAG_SUPERADMIN_EMAIL || true)
    stored=" (kept from the first run)"
  fi
  if [[ -z $DOMAIN ]]; then
    if ((ARG_YES == 1)); then
      die 2 "no web address was given." "run again with --domain, for example: --domain chat.sharmafurniture.in"
    fi
    say "  1. Your web address: a name you own, with its DNS record already pointing at this server."
    say "     Example: chat.sharmafurniture.in (no https://, no port)."
    prompt_answer "Web address:" valid_domain \
      "That is not a public web address. Use a name you own, like chat.sharmafurniture.in."
    DOMAIN=$ANSWER
  fi
  if [[ -z $EMAIL ]]; then
    if ((ARG_YES == 1)); then
      die 2 "no e-mail address was given." "run again with --email, for example: --email owner@sharmafurniture.in"
    fi
    say "  2. Your e-mail: you sign in with it, and it receives the certificate expiry notices."
    prompt_answer "E-mail:" valid_email \
      "That is not a real e-mail address. Use one that can receive mail, like owner@sharmafurniture.in."
    EMAIL=$ANSWER
  fi
  say "  Web address: $DOMAIN${stored}"
  say "  E-mail:      $EMAIL"
}

check_dns() {
  step 7 "Checking your web address points at this server."
  local theirs ours address matched=0
  theirs=$(timeout 8 getent ahostsv4 "$DOMAIN" 2>/dev/null | awk '{print $1}' | sort -u) || theirs=""
  ours=$(hostname -I 2>/dev/null || true)
  if [[ -z $theirs ]]; then
    warn "$DOMAIN does not answer yet. HTTPS starts by itself once its DNS record points at this server."
    return 0
  fi
  for address in $theirs; do
    case " ${ours//$'\n'/ } " in
      *" $address "*) matched=1 ;;
    esac
  done
  if ((matched == 1)); then
    say "  $DOMAIN points at this server. Good."
  else
    warn "$DOMAIN points at $(printf '%s' "$theirs" | tr '\n' ' ')and this server's addresses are different (normal behind a provider's NAT or Cloudflare). HTTPS starts by itself once the address points here."
  fi
}

make_swap() {
  fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || printf '/swapfile none swap sw 0 0\n' >>/etc/fstab
}

ensure_swap() {
  step 8 "Making room for building the images on a small server (a swap file)."
  local mem_kb
  mem_kb=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)
  if ((ARG_NO_SWAP == 1)); then
    say "  Skipped (--no-swap)."
  elif ((mem_kb >= 6 * 1024 * 1024)); then
    say "  This server has enough memory. Nothing to add."
  elif [[ -n $(swapon --show --noheadings 2>/dev/null || true) ]]; then
    say "  Swap is already set up."
  else
    run "create a 2 GB swap file (/swapfile) so building does not run out of memory" make_swap
  fi
}

install_docker() {
  local os_id version_codename
  os_id=$(os_field ID)
  version_codename=$(os_field VERSION_CODENAME)
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y ca-certificates curl
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/$os_id/gpg" -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/%s %s stable\n' \
    "$(dpkg --print-architecture)" "$os_id" "$version_codename" >/etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
}

install_helpers() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get install -y curl unzip
}

ensure_docker() {
  step 9 "Making sure Docker is installed (it runs everything)."
  if docker compose version >/dev/null 2>&1; then
    say "  Docker and Docker Compose are already installed."
  elif ((ARG_NO_DOCKER == 1)); then
    die 3 "Docker is not installed and --no-docker-install was given." \
      "install Docker Engine with the Compose plugin (https://docs.docker.com/engine/install/), then run this again."
  else
    run "install Docker from download.docker.com (the official repository)" install_docker
  fi
  if ! command -v curl >/dev/null 2>&1 || ! command -v unzip >/dev/null 2>&1; then
    run "install curl and unzip (used for health checks and upgrades)" install_helpers
  fi
}

place_release() {
  step 10 "Putting the product in $INSTALL_DIR."
  local dest=$INSTALL_DIR/releases/$VERSION
  run "create $INSTALL_DIR with its releases, backups and state folders" \
    mkdir -p "$INSTALL_DIR/releases" "$INSTALL_DIR/backups" "$INSTALL_DIR/state"
  run "keep backups private (owner only)" chmod 700 "$INSTALL_DIR/backups"
  if [[ -d $dest && $(readlink -f "$dest") == "$(readlink -f "$SOURCE")" ]]; then
    say "  Already running from $dest."
  else
    run "copy the product to $dest" copy_release "$SOURCE" "$dest"
  fi
  run "point $INSTALL_DIR/current at version $VERSION" ln -sfn "$dest" "$INSTALL_DIR/current"
  run "link the release to the one settings file ($INSTALL_DIR/.env)" ln -sfn ../../.env "$dest/.env"
}

write_settings() {
  step 11 "Writing the settings (passwords and keys are generated for you)."
  local release=$INSTALL_DIR/releases/$VERSION
  local -a wanted=(--set "RAG_SITE_DOMAIN=$DOMAIN" --set "RAG_SUPERADMIN_EMAIL=$EMAIL")
  local shown="RAG_SITE_DOMAIN=$DOMAIN, RAG_SUPERADMIN_EMAIL=$EMAIL"
  if is_dry; then
    would "write $INSTALL_DIR/.env with the setup wizard: $shown"
    return 0
  fi
  if ! run_setup_wizard "$release" "${wanted[@]}"; then
    die 1 "the setup wizard could not write the settings." \
      "read the message above, fix what it says, and run this command again. Nothing was started."
  fi
  say "  Settings written ($INSTALL_DIR/.env, readable by the administrator only)."
}

start_stack() {
  step 12 "Building and starting everything (5 to 15 minutes the first time)."
  if is_dry; then
    would "build the images (docker compose build)"
    would "start everything (docker compose up -d --remove-orphans)"
    return 0
  fi
  compose build
  compose up -d --remove-orphans
}

check_health() {
  step 13 "Waiting until it answers (the first start downloads the search model)."
  local base
  base=$(local_app_url)
  if is_dry; then
    would "wait for $base/api/v1/health/ready (up to 10 minutes), then for https://$DOMAIN/api/v1/health (up to 3 minutes)"
    return 0
  fi
  if ! wait_for "$base/api/v1/health/ready" 600; then
    die 1 "the engine did not become ready in 10 minutes." \
      "look at the reason with: docker compose --project-directory $INSTALL_DIR/current logs --tail 100 app  (then run this installer again)."
  fi
  say "  The engine is running."
  if wait_for "https://$DOMAIN/api/v1/health" 180; then
    say "  https://$DOMAIN answers."
  else
    warn "the engine runs, but https://$DOMAIN does not answer yet. Check that its DNS record points at this server, that ports 80 and 443 are open at your provider, then see: docker compose --project-directory $INSTALL_DIR/current logs caddy"
  fi
}

open_firewall() {
  step 14 "Opening the web ports if a firewall is already on."
  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then
    run "open ports 80 and 443 in the firewall (ufw)" bash -c 'ufw allow 80/tcp && ufw allow 443/tcp && ufw allow 443/udp'
  else
    say "  No active ufw firewall found. Nothing changed (this installer never turns a firewall on)."
  fi
}

write_cron() {
  printf '%s\n' \
    "# Nightly backup of cob-rag-engine (installed by deploy/install.sh; remove this file to stop it)." \
    "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    "30 2 * * * root bash $INSTALL_DIR/current/deploy/backup.sh --quiet >>/var/log/cob-rag-backup.log 2>&1" \
    >/etc/cron.d/cob-rag-backup
  chmod 644 /etc/cron.d/cob-rag-backup
}

nightly_backup() {
  step 15 "Setting up a nightly backup (02:30, the last 7 days are kept)."
  if ((ARG_NO_CRON == 1)); then
    say "  Skipped (--no-backup-cron)."
  else
    run "write /etc/cron.d/cob-rag-backup (every night at 02:30, keeps 7 days)" write_cron
  fi
}

write_state() {
  local file=$INSTALL_DIR/state/install.json installed_at
  installed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  if [[ -r $file ]]; then
    installed_at=$(sed -n 's/.*"installed_at": *"\([^"]*\)".*/\1/p' "$file" | head -n 1)
    installed_at=${installed_at:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}
  fi
  printf '{"domain": "%s", "version": "%s", "installed_at": "%s"}\n' \
    "$DOMAIN" "$VERSION" "$installed_at" >"$file"
}

record_state() {
  step 16 "Remembering what was installed."
  run "write $INSTALL_DIR/state/install.json (address and version, no secrets)" write_state
}

print_link() {
  step 17 "Your sign-in link."
  if is_dry; then
    would "print the sign-in link (the engine's bootstrap command)"
    return 0
  fi
  # Shown on this screen only: the link is single-use and is never written to a log file.
  if ! compose exec -T app python -m callobuzz_rag.accounts.bootstrap --print-link; then
    warn "could not print the sign-in link. Print it again with:"
    say "  docker compose --project-directory $INSTALL_DIR/current exec app python -m callobuzz_rag.accounts.bootstrap --print-link"
  fi
}

closing_screen() {
  cat <<EOF

All done. Your engine is at: https://$DOMAIN

  1. Open the sign-in link above, choose a password, and sign in with $EMAIL.
  2. Follow the first-run steps: your AI provider key, then your first knowledge base.
  3. To send e-mail from your own address (invitations, password resets), set up an
     e-mail sending service: see docs/deploy.html, section 5.3.

Later:
  Update to a new version:  sudo bash $INSTALL_DIR/current/deploy/upgrade.sh <new zip or folder>
  Back up now:              sudo bash $INSTALL_DIR/current/deploy/backup.sh
  Restore a backup:         sudo bash $INSTALL_DIR/current/deploy/restore.sh <backup folder>

A nightly backup stays on this server: copy backups to another computer or storage bucket now and then.
EOF
}

main() {
  parse_args "$@"
  check_given_answers
  step 1 "Getting ready."
  need_root
  take_lock
  if [[ -z $ARG_SOURCE ]]; then
    SOURCE=$(cd -- "$SCRIPT_DIR/.." && pwd)
  else
    SOURCE=$(cd -- "$ARG_SOURCE" 2>/dev/null && pwd) ||
      die 2 "the product folder '$ARG_SOURCE' does not exist." "check the --source path."
  fi
  is_release_dir "$SOURCE" ||
    die 2 "'$SOURCE' does not look like the cob-rag-engine product folder." \
      "unzip the product, then run: sudo bash <that folder>/deploy/install.sh"
  VERSION=$(release_version "$SOURCE") ||
    die 2 "could not read the product version from $SOURCE/pyproject.toml." "unzip the product again."
  say "  Installing cob-rag-engine $VERSION."
  if is_dry; then
    say "  Dry run: nothing will be changed."
  fi
  check_linux_and_os
  check_size
  check_existing_install
  check_ports
  ask_questions
  check_dns
  ensure_swap
  ensure_docker
  place_release
  write_settings
  start_stack
  check_health
  open_firewall
  nightly_backup
  record_state
  print_link
  finish_dry_run
  if ! is_dry; then
    closing_screen
  fi
}

main "$@"
