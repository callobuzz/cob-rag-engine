"""The docs grow with the product: every dashboard route has a help page, and the other way round."""

import re
from pathlib import Path

from callobuzz_rag.cob.kb import load_docs_index

ROOT = Path(__file__).resolve().parents[2]
ROUTES = ROOT / "services/dashboard/src/routes"
# LP1 landing: `app.tsx` is `/app`, the dashboard home at its second address (the public website owns
# `/` while it is on); the screen page of `/` describes it.
SKIP = {"__root.tsx", "_app.tsx", "$.tsx", "app.tsx"}


def route_of(rel: Path) -> str:
    """TanStack file name to path: `.` becomes `/`, `index` is dropped, a trailing `_` un-nests
    (`$kbId_`), and the `_app` layout folder adds nothing."""
    parts = [p for p in rel.with_suffix("").parts if p != "_app"]
    segs: list[str] = []
    for part in parts:
        for seg in part.split("."):
            seg = seg[:-1] if seg.endswith("_") else seg
            if seg and seg != "index":
                segs.append(seg)
    return "/" + "/".join(segs)


def test_route_of() -> None:
    assert route_of(Path("_app/kbs.$kbId_.setup.tsx")) == "/kbs/$kbId/setup"
    assert route_of(Path("_app/admin.customers.index.tsx")) == "/admin/customers"
    assert route_of(Path("_app/index.tsx")) == "/" and route_of(Path("login.index.tsx")) == "/login"
    assert route_of(Path("login.key.tsx")) == "/login/key"


def test_every_dashboard_route_has_a_screen_page() -> None:
    routes = {route_of(p.relative_to(ROUTES)) for p in ROUTES.rglob("*.tsx") if p.name not in SKIP}
    pages = {p.route for p in load_docs_index(ROOT).pages.values() if p.route}
    assert routes - pages == set(), (
        f"write docs/cob/screens/<name>.md for: {sorted(routes - pages)}"
    )
    # COB8 adds the Docs section's own route (`/docs/$`) together with its screen page.
    assert pages - routes - {"/docs/$"} == set(), (
        f"screen pages for routes that no longer exist: {sorted(pages - routes)}"
    )


def test_screen_pages_are_complete() -> None:
    """Front matter and the standard sections every screen page carries."""
    index = load_docs_index(ROOT)
    screens = [p for p in index.pages.values() if p.slug.startswith("screens/")]
    assert screens
    problems: list[str] = []
    for page in screens:
        titles = {s.title for s in page.sections}
        for needed in ("What you see", "Common tasks", "Troubleshooting", "Related"):
            if needed not in titles:
                problems.append(f"{page.slug}: no '## {needed}' section")
        if not 2 <= len(page.starters) <= 4:
            problems.append(f"{page.slug}: needs 2 to 4 starters, has {len(page.starters)}")
        if not page.summary:
            problems.append(f"{page.slug}: no summary")
    assert not problems, "\n".join(problems)


def _prose(text: str) -> str:
    """The page text a reader sees: no front matter, no code, no audience markers."""
    body = text.split("---", 2)[2] if text.startswith("---") else text
    body = re.sub(r"```.*?```", "", body, flags=re.DOTALL)
    body = re.sub(r"`[^`]*`", "", body)
    return re.sub(r"<!--.*?-->", "", body)


BANNED = re.compile(
    r"\b(corpus|corpora|tenants?|director|gate|gates|labels?|audience|system corpus|the bot)\b|Cob\b",
    re.IGNORECASE,
)


def test_screen_pages_follow_the_word_rules() -> None:
    problems: list[str] = []
    for path in sorted((ROOT / "docs/cob/screens").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        prose = _prose(text)
        for match in BANNED.finditer(prose):
            if match.group(0) == "Cob" or match.group(0).lower() != "cob":
                problems.append(f"{path.name}: '{match.group(0)}'")
        if "audience: admin" not in text.split("---", 2)[1] and "audience: customer" in text:
            # Customer pages say "customer" only inside an operator-only section.
            visible = re.sub(
                r"\n## [^\n]*\n<!-- audience: admin -->.*?(?=\n## |\Z)", "", text, flags=re.DOTALL
            )
            if re.search(r"\bcustomers?\b", _prose(visible), re.IGNORECASE):
                problems.append(f"{path.name}: 'customer' on a customer page")
    assert not problems, "\n".join(problems)


def test_screen_page_links_resolve_and_length_is_sane() -> None:
    index = load_docs_index(ROOT)
    problems: list[str] = []
    for page in index.pages.values():
        if not page.slug.startswith("screens/"):
            continue
        text = "\n".join(s.text for s in page.sections)
        for slug in re.findall(r"\]\(/docs/([^)#]+)", text):
            if slug not in index.pages:
                problems.append(f"{page.slug}: link to unknown page '{slug}'")
        words = len(text.split())
        if not 250 <= words <= 900:
            problems.append(f"{page.slug}: {words} words (want 250 to 900)")
    assert not problems, "\n".join(problems)
