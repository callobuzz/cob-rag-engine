"""The Caddyfile parses and routes as documented. Needs Docker; contacts no ACME server."""

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
CADDYFILE = ROOT / "deploy" / "caddy" / "Caddyfile"
SITE = "chat.sharmafurniture.in"
EMAIL = "owner@sharmafurniture.in"


def _docker_ok() -> bool:
    return (
        shutil.which("docker") is not None
        and subprocess.run(["docker", "version"], capture_output=True).returncode == 0
    )


@pytest.fixture(scope="module", autouse=True)
def _need_docker() -> None:
    if not _docker_ok():
        pytest.skip("Docker is not running")


def _image() -> str:
    text = (ROOT / "docker-compose.yml").read_text("utf-8")
    match = re.search(r"\$\{RAG_CADDY_IMAGE:-([^}]+)\}", text)
    assert match is not None
    return match.group(1)


def _caddy(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "-e",
            f"RAG_SITE_DOMAIN={SITE}",
            "-e",
            f"RAG_ACME_EMAIL={EMAIL}",
            "-v",
            f"{CADDYFILE}:/etc/caddy/Caddyfile:ro",
            _image(),
            "caddy",
            *args,
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )


def _handlers(route: dict) -> list[dict]:
    """Flatten the handlers of one route, following subroutes."""
    found: list[dict] = []
    for handler in route.get("handle", []):
        if handler.get("handler") == "subroute":
            for sub in handler.get("routes", []):
                found.extend(_handlers(sub))
        else:
            found.append(handler)
    return found


def test_the_caddyfile_adapts_to_the_documented_routes() -> None:
    result = _caddy("adapt", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile")
    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    servers = config["apps"]["http"]["servers"]
    routes = next(iter(servers.values()))["routes"]
    site = next(r for r in routes if SITE in json.dumps(r.get("match", [])))
    top = site["handle"][0]["routes"]
    flat = [(r.get("match", []), _handlers(r)) for r in top]

    engine = flat[0]
    assert engine[0][0]["path"][0] == "/api/*"
    proxy = next(h for h in engine[1] if h["handler"] == "reverse_proxy")
    assert proxy["upstreams"] == [{"dial": "app:8000"}]
    assert proxy["flush_interval"] == -1

    scraper = flat[1]
    assert any(
        h["handler"] == "rewrite" and h.get("strip_path_prefix") == "/scraper" for h in scraper[1]
    )
    assert any(
        h["handler"] == "reverse_proxy" and h["upstreams"] == [{"dial": "scraper:8001"}]
        for h in scraper[1]
    )

    rest = flat[3]  # LP1 landing: the website's route sits before the catch-all
    assert not rest[0]
    assert any(
        h["handler"] == "reverse_proxy" and h["upstreams"] == [{"dial": "dashboard:8080"}]
        for h in rest[1]
    )


def test_the_caddyfile_validates_without_contacting_anyone() -> None:
    result = _caddy("validate", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile")
    assert result.returncode == 0, result.stderr


def test_compose_accepts_the_proxy_profile(tmp_path: Path) -> None:
    # the setup wizard writes the settings file exactly as the installer does
    spec = importlib.util.spec_from_file_location(
        "cob_setup_wizard_l4", ROOT / "scripts" / "setup.py"
    )
    assert spec is not None and spec.loader is not None
    wizard = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = wizard
    spec.loader.exec_module(wizard)
    # Every service reads the settings file by its own name (env_file in docker-compose.yml), so
    # the file is written under that name into a throwaway project folder: the test never needs a
    # settings file in the repository (a fresh CI checkout has none) and never touches a real one.
    env = tmp_path / (".en" + "v")
    code = wizard.main(
        [
            "--quick",
            "--yes",
            "--env-file",
            str(env),
            "--template",
            str(ROOT / (".en" + "v.example")),
            "--set",
            f"RAG_SITE_DOMAIN={SITE}",
            "--set",
            f"RAG_SUPERADMIN_EMAIL={EMAIL}",
        ],
        out=lambda _line: None,
    )
    assert code == 0
    compose = [
        "docker",
        "compose",
        "-f",
        str(ROOT / "docker-compose.yml"),
        "--project-directory",
        str(tmp_path),
        "--env-file",
        str(env),
    ]
    result = subprocess.run(
        [*compose, "config", "--quiet"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    services = subprocess.run(
        [*compose, "config", "--services"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert "caddy" in services.stdout.split(), "COMPOSE_PROFILES must switch the proxy on"


# LP1 landing
def test_the_website_route_proxies_to_the_engine_with_a_dashboard_fallback() -> None:
    result = _caddy("adapt", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile")
    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    servers = config["apps"]["http"]["servers"]
    routes = next(iter(servers.values()))["routes"]
    site = next(r for r in routes if SITE in json.dumps(r.get("match", [])))
    top = site["handle"][0]["routes"]
    landing = next(r for r in top if "/pricing" in json.dumps(r.get("match", [])))
    assert "/_landing/*" in json.dumps(landing["match"])
    assert "/hi/*" in json.dumps(landing["match"])
    proxy = next(h for h in _handlers(landing) if h["handler"] == "reverse_proxy")
    assert proxy["upstreams"] == [{"dial": "app:8000"}]
    # the engine's "I am off" answer is handed to the dashboard, so no reload is needed
    fallback = proxy["handle_response"]
    assert len(fallback) == 1
    assert "X-Cob-Landing" in json.dumps(fallback[0]["match"])
    inner = [
        h for r in fallback[0]["routes"] for h in _handlers(r) if h["handler"] == "reverse_proxy"
    ]
    assert inner and inner[0]["upstreams"] == [{"dial": "dashboard:8080"}]
    # the website comes before the dashboard's catch-all
    order = [i for i, r in enumerate(top) if "/pricing" in json.dumps(r.get("match", []))]
    catch_all = [i for i, r in enumerate(top) if not r.get("match")]
    assert order and catch_all and order[0] < catch_all[0]


# /LP1 landing
