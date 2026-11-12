"""Static checks of the install files: compose, the Caddyfile, the image list, the routes Caddy must know."""

import re
import tomllib
from fnmatch import fnmatch  # LP1 landing
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text("utf-8"))
CADDYFILE = (ROOT / "deploy/caddy/Caddyfile").read_text("utf-8")
ENGINE_ROOT_PATHS = {
    "/widget.js",
    "/privacy/withdraw",
    "/privacy/withdraw.js",
    "/legal/*",
}  # P3 legal
# P3 legal: the engine route templates that the `/legal/*` matcher covers
ENGINE_ROUTE_TEMPLATES = {"/legal/{slug}"}
DEV_ONLY = {"/docs", "/openapi.json", "/redoc", "/docs/oauth2-redirect"}
# LP1 landing: the public website's paths, which the `@landing` matcher sends to the engine
LANDING_PATHS = {
    "/",
    "/pricing",
    "/features",
    "/about",
    "/contact",
    "/hi",
    "/hi/*",
    "/hi-latn",
    "/hi-latn/*",
    "/robots.txt",
    "/sitemap.xml",
    "/_landing/*",
}
# /LP1 landing


def test_caddy_service_is_behind_the_proxy_profile() -> None:
    caddy = COMPOSE["services"]["caddy"]
    assert caddy["profiles"] == ["proxy"]
    assert caddy["image"].startswith("${RAG_CADDY_IMAGE:-caddy:2.")
    assert sorted(caddy["ports"]) == sorted(
        [
            "${RAG_PROXY_BIND_ADDR:-0.0.0.0}:80:80",
            "${RAG_PROXY_BIND_ADDR:-0.0.0.0}:443:443",
            "${RAG_PROXY_BIND_ADDR:-0.0.0.0}:443:443/udp",
        ]
    )
    assert "./deploy/caddy/Caddyfile:/etc/caddy/Caddyfile:ro" in caddy["volumes"]
    assert {"caddy_data", "caddy_config"} <= set(COMPOSE["volumes"])
    assert caddy["environment"]["RAG_SITE_DOMAIN"] == "${RAG_SITE_DOMAIN:-}"


def test_every_other_published_port_stays_on_the_bind_address() -> None:
    for name, svc in COMPOSE["services"].items():
        if name == "caddy":
            continue
        for port in svc.get("ports", []):
            assert port.startswith("${RAG_BIND_ADDR:-127.0.0.1}:"), (name, port)


def test_the_image_list_names_caddy_with_the_compose_default() -> None:
    images = tomllib.loads((ROOT / "third_party/services.toml").read_text("utf-8"))["image"]
    caddy = [i for i in images if i["name"].startswith("caddy")]
    match = re.search(r"\$\{RAG_CADDY_IMAGE:-([^}]+)\}", COMPOSE["services"]["caddy"]["image"])
    assert match is not None
    assert caddy and caddy[0]["ref"] == match.group(1) and caddy[0]["licence"] == "Apache-2.0"


def test_caddyfile_routes() -> None:
    assert "{$RAG_SITE_DOMAIN}" in CADDYFILE and "email {$RAG_ACME_EMAIL}" in CADDYFILE
    found = re.search(r"@engine path (.+)", CADDYFILE)
    assert found is not None
    matcher = found.group(1).split()
    assert matcher[0] == "/api/*" and set(matcher[1:]) == ENGINE_ROOT_PATHS
    assert re.search(
        r"handle @engine \{\s*reverse_proxy app:8000 \{[^}]*flush_interval -1", CADDYFILE
    )
    assert "header_up X-Forwarded-For {remote_host}" in CADDYFILE
    assert re.search(r"handle_path /scraper/\* \{\s*reverse_proxy scraper:8001", CADDYFILE)
    assert re.search(r"handle \{\s*reverse_proxy dashboard:8080", CADDYFILE)


def test_every_engine_path_is_under_api_or_routed_by_caddy() -> None:
    from callobuzz_rag.api.app import create_app

    paths = engine_paths(create_app())  # LP1 landing: included routers are walked too
    stray = (
        {p for p in paths if p and not p.startswith("/api/")}
        - ENGINE_ROOT_PATHS
        - ENGINE_ROUTE_TEMPLATES  # P3 legal
        - DEV_ONLY
        - {p for p in paths if p and covered_by_landing(p)}  # LP1 landing
    )
    assert stray == set(), f"add these to the Caddyfile's @engine matcher: {sorted(stray)}"


# LP1 landing
def engine_paths(app: object) -> set[str]:
    """Every route path of the app, including those of included routers (FastAPI nests them)."""
    from fastapi.routing import iter_route_contexts

    return {c.path for c in iter_route_contexts(app.routes)}  # type: ignore[attr-defined]


def landing_matcher() -> list[str]:
    found = re.search(r"@landing path (.+)", CADDYFILE)
    assert found is not None, "the Caddyfile needs an @landing matcher"
    return found.group(1).split()


def covered_by_landing(path: str) -> bool:
    concrete = re.sub(r"\{[^}]+\}(:path)?", "x", path)
    return any(fnmatch(concrete, pattern) for pattern in landing_matcher())


def test_the_landing_matcher_lists_exactly_the_website_paths() -> None:
    assert set(landing_matcher()) == LANDING_PATHS


def test_every_website_route_is_inside_the_landing_matcher() -> None:
    from callobuzz_rag.api.app import create_app

    website = {
        p
        for p in engine_paths(create_app())
        if p == "/" or p.startswith(("/_landing/", "/hi", "/robots", "/sitemap"))
    }
    assert {"/", "/_landing/preview/{page}", "/hi/pricing", "/sitemap.xml"} <= website
    for path in website:
        assert covered_by_landing(path), path


def test_the_landing_block_proxies_to_the_engine_and_falls_back_to_the_dashboard() -> None:
    block = CADDYFILE.split("# LP1 landing", 1)[1].split("# /LP1 landing", 1)[0]
    assert "handle @landing {" in block and "reverse_proxy app:8000 {" in block
    assert "header_up X-Forwarded-For {remote_host}" in block
    assert "@off header X-Cob-Landing off" in block
    assert re.search(r"handle_response @off \{\s*reverse_proxy dashboard:8080", block)
    # the website comes before the dashboard's catch-all, and the engine block comes first
    assert CADDYFILE.index("handle @landing") < CADDYFILE.rindex("handle {")
    assert CADDYFILE.index("handle @engine") < CADDYFILE.index("handle @landing")


# /LP1 landing


def test_no_dashboard_route_is_shadowed_by_the_proxy() -> None:
    routes = [p.stem for p in (ROOT / "services/dashboard/src/routes").rglob("*.tsx")]
    assert not any(r.split(".")[0] in ("api", "scraper") for r in routes)
    assert "widget" in routes  # /widget is the dashboard's; only /widget.js goes to the engine


def test_ci_runs_shellcheck_and_the_install_script_tests() -> None:
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text("utf-8"))
    jobs = {job["name"]: job for job in ci["jobs"].values()}
    steps = "\n".join(str(step.get("run", "")) for step in jobs["Deploy scripts"]["steps"])
    assert "shellcheck" in steps
    assert "tests/integration/test_deploy_scripts.py" in steps
    assert "tests/integration/test_caddy_config.py" in steps
    compose = "\n".join(str(step.get("run", "")) for step in jobs["Compose config"]["steps"])
    assert "--profile proxy config --quiet" in compose


def test_install_accepts_a_busy_port_only_when_docker_proxy_holds_it() -> None:
    text = (ROOT / "deploy/install.sh").read_text("utf-8")
    assert "((ours == 1 && RERUN == 1)) && [[ $holder == docker-proxy ]]" in text


def test_deploy_scripts_are_executable_in_git() -> None:
    import shutil
    import subprocess

    if shutil.which("git") is None or not (ROOT / ".git").exists():
        return
    rows = subprocess.run(
        ["git", "ls-files", "-s", "deploy/install.sh", "deploy/upgrade.sh", "deploy/backup.sh",
         "deploy/restore.sh"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.splitlines()  # fmt: skip
    assert len(rows) == 4 and all(r.startswith("100755 ") for r in rows), rows
