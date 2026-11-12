"""RAG_FRAME_ANCESTORS: who may show the dashboard in a frame (L5a). The shell cases of the
script itself are in services/dashboard/deploy/test-config.sh; this pins the template, the
compose file and the settings template to it."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = (ROOT / "services/dashboard/deploy/nginx.conf.template").read_text("utf-8")


def test_every_csp_takes_frame_ancestors_from_the_setting() -> None:
    csps = re.findall(r'Content-Security-Policy "([^"]+)"', TEMPLATE)
    assert len(csps) >= 4
    for csp in csps:
        assert "frame-ancestors 'self'" not in csp
        if "razorpay.com" in csp:  # /pay opens a real payment window: never framed
            assert "frame-ancestors 'none';" in csp
        else:
            assert "frame-ancestors __FRAME_ANCESTORS__;" in csp


def test_compose_and_env_example_pass_it_to_the_dashboard() -> None:
    compose = (ROOT / "docker-compose.yml").read_text("utf-8")
    assert "RAG_FRAME_ANCESTORS: ${RAG_FRAME_ANCESTORS:-}" in compose
    assert "RAG_FRAME_ANCESTORS=" in (ROOT / ".env.example").read_text("utf-8")


def test_the_setting_is_empty_in_the_template_so_normal_installs_keep_self() -> None:
    line = next(
        ln
        for ln in (ROOT / ".env.example").read_text("utf-8").splitlines()
        if ln.startswith("RAG_FRAME_ANCESTORS=")
    )
    assert line == "RAG_FRAME_ANCESTORS="


def test_no_x_frame_options_anywhere() -> None:
    headers = (ROOT / "services/dashboard/deploy/headers.conf").read_text("utf-8")
    assert "X-Frame-Options" not in headers and "X-Frame-Options" not in TEMPLATE


def test_the_script_substitutes_the_placeholder_and_checks_the_value() -> None:
    script = (ROOT / "services/dashboard/deploy/40-cob-config.sh").read_text("utf-8")
    assert "__FRAME_ANCESTORS__" in script and "frame_ancestors_value" in script
    assert "RAG_FRAME_ANCESTORS" in script


def test_the_demo_value_is_documented() -> None:
    env = (ROOT / ".env.example").read_text("utf-8")
    assert "self https://demo.example.com" in env


def test_the_dashboard_image_test_still_expects_self_by_default() -> None:
    spec = (ROOT / "services/dashboard/e2e/smoke-image.spec.ts").read_text("utf-8")
    assert "frame-ancestors 'self'" in spec
