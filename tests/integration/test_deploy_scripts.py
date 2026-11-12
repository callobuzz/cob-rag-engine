"""deploy/*.sh: the pure functions, dry runs and refusals.

Every case runs inside a throwaway `ubuntu:24.04` container with no network. Nothing here
touches this computer's /opt or /etc, its Docker volumes or the cob-rag stack.
"""

import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from callobuzz_rag.accounts.email_rules import EmailRuleError, check_email
from callobuzz_rag.core.install import is_dev_domain

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
UBUNTU = "ubuntu:24.04"
SHELLCHECK = "koalaman/shellcheck:v0.10.0"


def _docker_ok() -> bool:
    return (
        shutil.which("docker") is not None
        and subprocess.run(["docker", "version"], capture_output=True).returncode == 0
    )


@pytest.fixture(scope="module", autouse=True)
def _need_docker() -> None:
    if not _docker_ok():
        pytest.skip("Docker is not running")


def bash(
    script: str, *, network: str = "none", extra: tuple[str, ...] = ()
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            network,
            "-v",
            f"{ROOT}:/src:ro",
            *extra,
            UBUNTU,
            "bash",
            "-c",
            script,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )


def cases(calls: list[str]) -> list[tuple[int, str]]:
    """Run `source common.sh; <call>` for each call in ONE container; returns (rc, stdout) per call."""
    lines = ["source /src/deploy/lib/common.sh"]
    for call in calls:
        lines.append(f'out=$({call}); rc=$?; printf "%s\\t%s\\n" "$rc" "$out"')
    r = bash("\n".join(lines))
    assert r.returncode == 0, r.stderr
    rows = [line.split("\t", 1) for line in r.stdout.splitlines()]
    assert len(rows) == len(calls), r.stdout
    return [(int(rc), out) for rc, out in rows]


def q(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def test_shellcheck_is_clean() -> None:
    scripts = [p.name for p in (ROOT / "deploy").glob("*.sh")]
    r = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "-v",
            f"{ROOT}/deploy:/mnt:ro",
            SHELLCHECK,
            "-x",
            "-S",
            "style",
            *[f"/mnt/{name}" for name in scripts],
            "/mnt/lib/common.sh",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert r.returncode == 0, r.stdout + r.stderr


HOSTS = [
    "chat.sharmafurniture.in",
    "Chat.SharmaFurniture.in",
    "sharmafurniture.in",
    "xn--h2brj9c.in",
    "localhost",
    "app.localhost",
    "myshop.local",
    "myshop.test",
    "box.internal",
    "nas.lan",
    "x.home.arpa",
    "203.0.113.10",
    "192.168.1.20",
    "::1",
    "https://chat.sharmafurniture.in",
    "chat.sharmafurniture.in:443",
    "chat sharmafurniture.in",
    "-bad.in",
    "bad-.in",
    "chat..in",
    "under_score.in",
    "a" * 64 + ".in",
    "",
    "testing.in",
    "local.store",
]


def _rfc1123(name: str) -> bool:
    labels = name.split(".")
    return (
        len(name) <= 253
        and len(labels) >= 2
        and all(re.fullmatch(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)
    )


def test_valid_domain_matches_the_engine_rules() -> None:
    got = cases([f"valid_domain {q(h)}" for h in HOSTS])
    for host, (rc, out) in zip(HOSTS, got, strict=True):
        name = host.strip().lower()
        plain = (
            name
            and "/" not in name
            and ":" not in name
            and " " not in name
            and not name.replace(".", "").isdigit()
        )
        expected_ok = bool(plain) and not is_dev_domain(name) and _rfc1123(name)
        assert (rc == 0) == expected_ok, (host, rc, out)
        if rc == 0:
            assert out == name


EMAILS = [
    "owner@sharmafurniture.in",
    "Owner@SharmaFurniture.IN",
    "owner@shop.test",
    "owner@x.example",
    "owner@x.invalid",
    "owner@x.localhost",
    "owner",
    "owner@",
    "@x.in",
    "a b@x.in",
    "owner@x",
]


def test_valid_email_follows_the_engine_production_rule() -> None:
    got = cases([f"valid_email {q(e)}" for e in EMAILS])
    for addr, (rc, _) in zip(EMAILS, got, strict=True):
        try:
            check_email(addr, allow_special_use=False)
            engine_ok = "@" in addr and "." in addr.rsplit("@", 1)[-1]
        except EmailRuleError:
            engine_ok = False
        assert (rc == 0) == engine_ok, addr


@pytest.mark.parametrize(
    ("call", "rc", "out"),
    [
        ("check_os ubuntu 24.04", 0, "ok"),
        ("check_os ubuntu 22.04", 0, "ok"),
        ("check_os debian 12", 0, "ok"),
        ("check_os ubuntu 20.04", 1, "unsupported"),
        ("check_os centos 9", 1, "unsupported"),
        ("check_cpus 1", 1, "too_low"),
        ("check_cpus 2", 0, "low"),
        ("check_cpus 4", 0, "ok"),
        ("check_memory_kb 2000000", 1, "too_low"),
        ("check_memory_kb 3800000", 0, "low"),
        ("check_memory_kb 8000000", 0, "ok"),
        ("check_disk_kb 10000000", 1, "too_low"),
        ("check_disk_kb 30000000", 0, "low"),
        ("check_disk_kb 90000000", 0, "ok"),
        ("version_gt 0.2.0 0.1.9", 0, ""),
        ("version_gt 0.10.0 0.9.0", 0, ""),
        ("version_gt 0.1.0 0.1.0", 1, ""),
        ("version_gt 0.1.0 0.2.0", 1, ""),
        ("version_gt 0.2.0-rc1 0.1.0", 2, ""),
        ("backup_id 1791375000 0.2.0", 0, "20261007T121000Z-0.2.0"),  # 2026-10-07 12:10 UTC
    ],
)
def test_pure_functions(call: str, rc: int, out: str) -> None:
    assert cases([call]) == [(rc, out)]


SS = """LISTEN 0 4096 0.0.0.0:80 0.0.0.0:* users:(("apache2",pid=811,fd=4))
LISTEN 0 4096 0.0.0.0:443 0.0.0.0:* users:(("docker-proxy",pid=912,fd=4))
LISTEN 0 4096 127.0.0.1:8000 0.0.0.0:* users:(("docker-proxy",pid=913,fd=4))
LISTEN 0 4096 [::]:8443 [::]:*"""


def test_port_busy_reads_ss_output() -> None:
    got = cases(
        [
            f"port_busy 80 {q(SS)}",
            f"port_busy 443 {q(SS)}",
            f"port_busy 8080 {q(SS)}",
            f"port_busy 8443 {q(SS)}",
            f"port_busy 0 {q('')}",
        ]
    )
    assert got == [
        (0, "apache2"),
        (0, "docker-proxy"),
        (1, ""),
        (0, "another program"),
        (1, ""),
    ]


def _pyproject_version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["version"]


def test_release_helpers() -> None:
    r = bash(
        "set -e; source /src/deploy/lib/common.sh; mkdir -p /tmp/z/cob-rag-engine; "
        "cp /src/pyproject.toml /src/docker-compose.yml /tmp/z/cob-rag-engine/; "
        "find_release_root /tmp/z; release_version /tmp/z/cob-rag-engine; "
        'printf \'A=1\\nRAG_SITE_DOMAIN="chat.sharmafurniture.in"\\nB=["x"]\\n\' > /tmp/e; '
        "env_value /tmp/e RAG_SITE_DOMAIN; env_value /tmp/e B; "
        "env_value /tmp/e MISSING || echo missing"
    )
    root, version, domain, listed, missing = r.stdout.split()
    assert root == "/tmp/z/cob-rag-engine" and version == _pyproject_version()
    assert domain == "chat.sharmafurniture.in" and listed == '["x"]' and missing == "missing"


SETTINGS = "/opt/cob-rag/" + ".en" + "v"


def test_install_dry_run_changes_nothing() -> None:
    r = bash(
        "bash /src/deploy/install.sh --dry-run --yes --domain chat.sharmafurniture.in "
        "--email owner@sharmafurniture.in "
        "--source /src; rc=$?; test ! -e /opt/cob-rag && test ! -e /etc/cron.d/cob-rag-backup; "
        'echo "rc=$rc left=$?"'
    )
    out = r.stdout
    assert "rc=0 left=0" in out, r.stdout + r.stderr
    for expected in (
        "would: install Docker from download.docker.com",
        "would: copy the product to /opt/cob-rag/releases/",
        f"would: write {SETTINGS}",
        "RAG_SITE_DOMAIN=chat.sharmafurniture.in",
        "would: start",
        "Dry run: nothing was changed.",
    ):
        assert expected in out, expected
    assert "RAG_LICENSE_" not in out + r.stderr


@pytest.mark.parametrize(
    ("args", "code", "says"),
    [
        ("--dry-run --yes --email owner@sharmafurniture.in", 2, "--domain"),
        (
            "--dry-run --yes --domain localhost --email owner@sharmafurniture.in",
            2,
            "public web address",
        ),
        (
            "--dry-run --yes --domain chat.sharmafurniture.in --email owner@shop.test",
            2,
            "real e-mail",
        ),
        (
            "--dry-run --yes --domain chat.sharmafurniture.in --email owner@sharmafurniture.in "
            "--purchase-code nope",
            2,
            "Unknown option",
        ),
        ("--dry-run --frobnicate", 2, "Unknown option"),
    ],
)
def test_install_refuses_bad_answers(args: str, code: int, says: str) -> None:
    r = bash(f"bash /src/deploy/install.sh {args} --source /src; echo rc=$?")
    assert f"rc={code}" in r.stdout and says in (r.stdout + r.stderr)


def test_install_refuses_an_unsupported_os() -> None:
    r = bash(
        "printf 'ID=ubuntu\\nVERSION_ID=\"20.04\"\\n' > /tmp/os; OS_RELEASE=/tmp/os bash "
        "/src/deploy/install.sh --dry-run --yes --domain chat.sharmafurniture.in "
        "--email owner@sharmafurniture.in --source /src; echo rc=$?"
    )
    assert "rc=3" in r.stdout and "Ubuntu 22.04 or 24.04, or Debian 12" in (r.stdout + r.stderr)


def test_install_rerun_of_another_version_points_to_upgrade() -> None:
    r = bash(
        "mkdir -p /opt/cob-rag/releases/0.0.1 /opt/cob-rag/state && "
        "ln -s /opt/cob-rag/releases/0.0.1 /opt/cob-rag/current && "
        f"printf 'RAG_SITE_DOMAIN=chat.sharmafurniture.in\\n' > {SETTINGS} && "
        'echo \'{"version": "0.0.1"}\' > /opt/cob-rag/state/install.json && '
        "bash /src/deploy/install.sh --dry-run --yes --source /src; echo rc=$?"
    )
    assert "rc=4" in r.stdout and "upgrade.sh" in (r.stdout + r.stderr)


def test_install_rerun_of_the_same_version_asks_nothing_and_keeps_the_stored_answers() -> None:
    version = _pyproject_version()
    r = bash(
        "mkdir -p /opt/cob-rag/releases/x /opt/cob-rag/state && "
        f"ln -s /opt/cob-rag/releases/{version} /opt/cob-rag/current && "
        "printf 'RAG_SITE_DOMAIN=chat.sharmafurniture.in\\nRAG_SUPERADMIN_EMAIL=owner@sharmafurniture.in\\n' "
        f"> {SETTINGS} && "
        f'echo \'{{"version": "{version}"}}\' > /opt/cob-rag/state/install.json && '
        "bash /src/deploy/install.sh --dry-run --yes --source /src; echo rc=$?"
    )
    out = r.stdout + r.stderr
    assert "rc=0" in out, out
    assert "kept from the first run" in out and "RAG_SITE_DOMAIN=chat.sharmafurniture.in" in out


# --- backup.sh and restore.sh (dry runs against a fake install tree in the container) -----

FAKE = (
    "mkdir -p /opt/cob-rag/releases/0.1.0 /opt/cob-rag/state /opt/cob-rag/backups && "
    "cp -r /src/deploy /src/pyproject.toml /src/docker-compose.yml /opt/cob-rag/releases/0.1.0/ && "
    "ln -s /opt/cob-rag/releases/0.1.0 /opt/cob-rag/current && "
    "printf 'RAG_SITE_DOMAIN=chat.sharmafurniture.in\\nRAG_STORAGE_PROVIDER=local\\n' "
    f"> {SETTINGS} && "
    'echo \'{"version": "0.1.0"}\' > /opt/cob-rag/state/install.json && '
)


def test_backup_dry_run_lists_every_part() -> None:
    r = bash(
        FAKE + "bash /src/deploy/backup.sh --dry-run; echo rc=$?; ls /opt/cob-rag/backups | wc -l"
    )
    out = r.stdout
    assert "rc=0" in out and out.strip().endswith("0"), out + r.stderr
    for part in (
        "pg_dump",
        "files.tar",
        "scraper.tar",
        "callobuzz_rag.vectordb.snapshots create",
        "env (owner-only)",
        "SHA256SUMS",
        "older than 7 days",
        "Dry run: nothing was changed.",
    ):
        assert part in out, part


def test_backup_skips_files_with_external_storage() -> None:
    r = bash(FAKE + f"sed -i 's/=local/=s3/' {SETTINGS}; bash /src/deploy/backup.sh --dry-run")
    assert "files.tar" not in r.stdout and "your S3 provider" in r.stdout


def test_backup_needs_an_install_and_a_sane_keep_days() -> None:
    r = bash("bash /src/deploy/backup.sh --dry-run; echo rc=$?")
    assert "rc=3" in r.stdout and "install.sh" in (r.stdout + r.stderr)
    r = bash(FAKE + "bash /src/deploy/backup.sh --dry-run --keep-days nope; echo rc=$?")
    assert "rc=2" in r.stdout


def test_backup_retention_removes_only_its_own_old_folders_and_never_the_newest() -> None:
    # sourced, not run: prune_backups with a throwaway tree, real deletion inside the container
    r = bash(
        "mkdir -p /opt/cob-rag/backups && cd /opt/cob-rag/backups && "
        "mkdir 20200101T023000Z-0.1.0 20200102T023000Z-0.1.0 my-own-notes 20200103T023000Z-0.1.0 && "
        "sed -n '/^prune_backups/,/^}/p;/^info()/,/^}/p;/^BACKUP_PATTERN/p' /src/deploy/backup.sh "
        "> /tmp/prune.sh && source /src/deploy/lib/common.sh && QUIET=1 && source /tmp/prune.sh && "
        "prune_backups 7 && ls"
    )
    assert r.stdout.split() == ["20200103T023000Z-0.1.0", "my-own-notes"], r.stdout + r.stderr


NEWER = (
    "mkdir -p /opt/cob-rag/backups/20261007T023000Z-0.9.0 && cd /opt/cob-rag/backups/20261007T023000Z-0.9.0 && "
    'echo \'{"version": "0.9.0"}\' > manifest.json && touch postgres.dump && '
    "sha256sum manifest.json postgres.dump > SHA256SUMS && "
)


def test_restore_refuses_a_backup_from_a_newer_version_and_a_bad_checksum() -> None:
    r = bash(
        FAKE
        + NEWER
        + "bash /src/deploy/restore.sh /opt/cob-rag/backups/20261007T023000Z-0.9.0 --dry-run --yes; echo rc=$?"
    )
    assert "rc=4" in r.stdout and "newer" in (r.stdout + r.stderr)
    bad = NEWER.replace("0.9.0", "0.1.0") + "echo x >> postgres.dump && "
    r = bash(
        FAKE
        + bad
        + "bash /src/deploy/restore.sh /opt/cob-rag/backups/20261007T023000Z-0.1.0 --dry-run --yes; echo rc=$?"
    )
    assert "rc=3" in r.stdout and "damaged" in (r.stdout + r.stderr)


def test_restore_without_yes_needs_the_typed_word() -> None:
    ok = NEWER.replace("0.9.0", "0.1.0")
    r = bash(
        FAKE
        + ok
        + "echo nope | bash /src/deploy/restore.sh /opt/cob-rag/backups/20261007T023000Z-0.1.0 --dry-run; echo rc=$?"
    )
    assert "rc=4" in r.stdout and "Type restore" in r.stdout


def test_checksums_written_by_backup_are_accepted_by_restore() -> None:
    r = bash(
        FAKE + "d=/opt/cob-rag/backups/20261007T023000Z-0.1.0 && mkdir -p $d/qdrant && cd $d && "
        'echo \'{"version": "0.1.0"}\' > manifest.json && echo data > postgres.dump && '
        "echo '{}' > qdrant/manifest.json && echo snap > qdrant/c-1.snapshot && "
        "sed -n '/^write_checksums/,/^}/p' /src/deploy/backup.sh > /tmp/sums.sh && "
        "source /tmp/sums.sh && write_checksums $d && cat $d/SHA256SUMS && "
        "bash /src/deploy/restore.sh $d --dry-run --yes; echo rc=$?"
    )
    out = r.stdout
    assert (
        "rc=0" in out
        and "qdrant/c-1.snapshot" in out
        and "SHA256SUMS" not in out.split("rc=")[0].split("\n")[0]
    ), out + r.stderr
    assert "checksums match" in out


def test_restore_dry_run_lists_the_steps_and_changes_nothing() -> None:
    ok = NEWER.replace("0.9.0", "0.1.0")
    r = bash(
        FAKE
        + ok
        + "bash /src/deploy/restore.sh /opt/cob-rag/backups/20261007T023000Z-0.1.0 --dry-run --yes; "
        "echo rc=$?; ls /opt/cob-rag/backups"
    )
    out = r.stdout
    assert "rc=0" in out, out + r.stderr
    assert "would: restore the database" in out and "would: start everything again" in out
    assert "Dry run: nothing was changed." in out


# --- upgrade.sh ---------------------------------------------------------------------------

NEW = (
    "mkdir -p /tmp/new/cob-rag-engine && "
    "cp -r /src/deploy /src/docker-compose.yml /tmp/new/cob-rag-engine/ && "
    "sed 's/^version = .*/version = \"9.9.9\"/' /src/pyproject.toml > /tmp/new/cob-rag-engine/pyproject.toml && "
)


def test_upgrade_dry_run_plans_every_step_in_order() -> None:
    r = bash(
        FAKE
        + NEW
        + "bash /src/deploy/upgrade.sh /tmp/new --dry-run --yes; echo rc=$?; readlink /opt/cob-rag/current"
    )
    out = r.stdout
    assert "rc=0" in out and out.strip().endswith("/opt/cob-rag/releases/0.1.0"), out + r.stderr
    order = [
        "0.1.0 → 9.9.9",
        "would: run deploy/backup.sh",
        "would: tag the current images as :rollback",
        "would: docker compose build",
        f"would: update {SETTINGS} with the new settings",
        "would: alembic upgrade head",
        "would: switch /opt/cob-rag/current",
        "would: docker compose up -d",
        "would: wait for",
        "Dry run: nothing was changed.",
    ]
    positions = [out.index(s) for s in order]
    assert positions == sorted(positions), out


def test_upgrade_refuses_the_same_or_an_older_version() -> None:
    same = NEW.replace("9.9.9", "0.1.0")
    r = bash(FAKE + same + "bash /src/deploy/upgrade.sh /tmp/new --dry-run --yes; echo rc=$?")
    assert "rc=4" in r.stdout and "already" in (r.stdout + r.stderr)


def test_upgrade_needs_an_install_and_a_real_product_folder() -> None:
    r = bash("bash /src/deploy/upgrade.sh /tmp/nothing --dry-run --yes; echo rc=$?")
    assert "rc=3" in r.stdout and "install.sh" in (r.stdout + r.stderr)
    r = bash(
        FAKE
        + "mkdir -p /tmp/empty && bash /src/deploy/upgrade.sh /tmp/empty --dry-run --yes; echo rc=$?"
    )
    assert "rc=2" in r.stdout and "cob-rag-engine" in (r.stdout + r.stderr)


def test_skipping_the_backup_needs_both_flags() -> None:
    r = bash(
        FAKE
        + NEW
        + "bash /src/deploy/upgrade.sh /tmp/new --dry-run --yes --skip-backup; echo rc=$?"
    )
    assert "rc=2" in r.stdout and "--i-have-a-backup" in (r.stdout + r.stderr)


def test_upgrade_without_a_new_version_says_what_to_give() -> None:
    r = bash(FAKE + "bash /src/deploy/upgrade.sh --dry-run --yes; echo rc=$?")
    assert "rc=2" in r.stdout and "--rollback" in (r.stdout + r.stderr)


def test_upgrade_dry_run_leaves_the_install_tree_alone() -> None:
    r = bash(
        FAKE + NEW + "find /opt/cob-rag | sort > /tmp/before; "
        "bash /src/deploy/upgrade.sh /tmp/new --dry-run --yes >/dev/null; "
        "find /opt/cob-rag | sort > /tmp/after; diff /tmp/before /tmp/after && echo same"
    )
    assert r.stdout.strip().endswith("same"), r.stdout + r.stderr


def test_a_failure_after_the_backup_prints_the_rollback_command() -> None:
    # exercise the exit handler directly: it is what turns any failure into one command
    r = bash(
        FAKE + "source /src/deploy/lib/common.sh && "
        "sed -n '/^record_upgrade/,/^}/p;/^now()/p;/^on_upgrade_exit/,/^}/p' "
        "/src/deploy/upgrade.sh > /tmp/h.sh && source /tmp/h.sh && "
        "OLD_VERSION=0.1.0 NEW_VERSION=9.9.9 BACKUP_ID=20261007T023000Z-0.1.0 PROTECTED=1 SWITCHED=1 && "
        "(exit 1) || on_upgrade_exit; "
        "cat /opt/cob-rag/state/last-upgrade.json"
    )
    out = r.stdout + r.stderr
    assert "sudo bash /opt/cob-rag/releases/0.1.0/deploy/upgrade.sh --rollback" in out
    assert '"result": "failed"' in out


def test_rollback_dry_run_uses_the_recorded_upgrade() -> None:
    state = (
        'echo \'{"from": "0.1.0", "to": "9.9.9", "backup": "20261007T023000Z-0.1.0", '
        '"result": "health_failed"}\' > /opt/cob-rag/state/last-upgrade.json && '
        "mkdir -p /opt/cob-rag/releases/9.9.9 && ln -sfn /opt/cob-rag/releases/9.9.9 /opt/cob-rag/current && "
    )
    r = bash(FAKE + state + "bash /src/deploy/upgrade.sh --rollback --dry-run --yes; echo rc=$?")
    out = r.stdout
    assert "rc=0" in out and "9.9.9 → 0.1.0" in out, out + r.stderr
    assert "would: switch /opt/cob-rag/current to /opt/cob-rag/releases/0.1.0" in out
    assert "would: run deploy/restore.sh /opt/cob-rag/backups/20261007T023000Z-0.1.0 --yes" in out


def test_rollback_without_a_recorded_upgrade_stops() -> None:
    r = bash(FAKE + "bash /src/deploy/upgrade.sh --rollback --dry-run --yes; echo rc=$?")
    assert "rc=3" in r.stdout and "no recorded upgrade to roll back" in (r.stdout + r.stderr)


def test_rollback_help_is_one_command() -> None:
    r = bash("source /src/deploy/lib/common.sh; rollback_help 0.1.0 20261007T023000Z-0.1.0")
    assert "sudo bash /opt/cob-rag/releases/0.1.0/deploy/upgrade.sh --rollback" in r.stdout
    assert "/opt/cob-rag/backups/20261007T023000Z-0.1.0" in r.stdout


def test_restore_database_recreates_the_database_over_newer_tables() -> None:
    # A rollback restores an older dump into a database a newer migration already changed: a
    # table added since, with a foreign key to a dumped table, must not break the restore.
    inner = r"""
set -Eeuo pipefail
export PGHOST=/tmp
psql -d postgres -v ON_ERROR_STOP=1 -qc "create role rag superuser login"
createdb -O rag rag_kernel
psql -U rag -d rag_kernel -v ON_ERROR_STOP=1 -q \
  -c "create table corpora (id int primary key, name text)" \
  -c "insert into corpora values (1, 'old')"
FOLDER=/tmp/backup; mkdir -p $FOLDER
pg_dump -U rag -d rag_kernel -Fc > $FOLDER/postgres.dump
psql -U rag -d rag_kernel -v ON_ERROR_STOP=1 -q \
  -c "create table newer (id int primary key, corpus_id int references corpora(id))" \
  -c "insert into newer values (1, 1)" -c "update corpora set name = 'changed'"
compose() { shift 3; "$@"; }
sed -n '/^restore_database() {/,/^}/p' /src/deploy/restore.sh > /tmp/fn.sh
source /tmp/fn.sh
restore_database
echo "name=$(psql -U rag -d rag_kernel -Atc 'select name from corpora')"
echo "newer=$(psql -U rag -d rag_kernel -Atc "select to_regclass('public.newer') is null")"
"""
    outer = (
        "set -e; export PGDATA=/tmp/pg; "
        "gosu postgres initdb -D $PGDATA --auth=trust >/dev/null; "
        "gosu postgres pg_ctl -D $PGDATA -o \"-c listen_addresses='' -c unix_socket_directories=/tmp\" "
        "-w start >/dev/null; "
        f"cat > /tmp/s.sh <<'EOS'\n{inner}\nEOS\n"
        "gosu postgres bash /tmp/s.sh; echo rc=$?"
    )
    r = subprocess.run(
        [
            "docker", "run", "--rm", "--network", "none", "-v", f"{ROOT}:/src:ro",
            "--entrypoint", "bash", "postgres:16", "-c", outer,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )  # fmt: skip
    out = r.stdout + r.stderr
    assert "rc=0" in r.stdout, out
    assert "name=old" in r.stdout and "newer=t" in r.stdout, out


def test_the_nightly_cron_line_runs_through_bash() -> None:
    # The scripts are not guaranteed to keep an executable bit through a copy or a zip.
    r = bash(
        "mkdir -p /etc/cron.d && INSTALL_DIR=/opt/cob-rag && "
        "sed -n '/^write_cron() {/,/^}/p' /src/deploy/install.sh > /tmp/fn.sh && "
        "source /tmp/fn.sh && write_cron && cat /etc/cron.d/cob-rag-backup"
    )
    lines = [x for x in r.stdout.splitlines() if x.startswith("30 2 ")]
    assert len(lines) == 1, r.stdout + r.stderr
    assert " root bash /opt/cob-rag/current/deploy/backup.sh --quiet " in lines[0]


def test_two_backups_in_one_minute_get_different_folders() -> None:
    first, second = cases(["backup_id 1791375000 0.2.0", "backup_id 1791375001 0.2.0"])
    assert first != second


def test_upgrade_removes_the_unpacked_zip_folder_it_made() -> None:
    r = bash(
        "INSTALL_DIR=/opt/cob-rag && mkdir -p $INSTALL_DIR/releases/.incoming-1/x $INSTALL_DIR/releases/0.1.0 && "
        "sed -n '/^cleanup_incoming() {/,/^}/p' /src/deploy/upgrade.sh > /tmp/fn.sh && source /tmp/fn.sh && "
        "INCOMING=$INSTALL_DIR/releases/.incoming-1 && cleanup_incoming && "
        "INCOMING=$INSTALL_DIR/releases/0.1.0 && cleanup_incoming; ls -A $INSTALL_DIR/releases"
    )
    # the unpacked folder is gone; a folder that is not an .incoming-* one is never touched
    assert r.stdout.split() == ["0.1.0"], r.stdout + r.stderr


def test_install_help_mentions_no_purchase_code() -> None:
    r = bash("bash /src/deploy/install.sh --help")
    text = (r.stdout + r.stderr).lower()
    for word in ("purchase", "evaluation", "codecanyon", "--staging"):
        assert word not in text, word
