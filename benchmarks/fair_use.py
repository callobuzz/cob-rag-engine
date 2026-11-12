"""Fair use, answer lanes, the free local model and website learning: measured (PIP4 final test).

    python -m benchmarks.fair_use --dry-run                     # free, no Docker: every section on fakes
    python -m benchmarks.fair_use --label 2026-10-05            # the real run (Docker up, nothing paid)
    python -m benchmarks.fair_use --label 2026-10-05 \\
        --engine-url http://127.0.0.1:8000 --widget-key pk_... --origin https://shreedental.in

Five sections, each printing plain-language results and writing JSON and Markdown:

  gate      p50 and p95 milliseconds `answer_gate` adds to one answer with no limits, with four limits
            and with all twelve, on real PostgreSQL and Redis (target: at most 5 ms of p95 added).
  lanes     4 answer slots, 50 low-priority and 5 high-priority answers of 2 s each at once: how long
            high waits (target: p95 under 0.5 s), how long low waits, how many get the busy reply,
            and that no slot is left behind.
  throttle  a Free-plan business at 30 questions an hour: answers 1 to 30 on the plan's profile, 31 to
            45 slower, 46 and on paused; one mail and one webhook per level; decisions recorded.
  local     20 widget questions (8 English, 6 Hindi, 6 Hinglish) through a running stack on the Free
            profile: first word and whole answer p50 and p90, how many answers cite a source, and the
            wait when several ask at once. Needs --engine-url, --widget-key and --origin.
  website   a fixture site on 127.0.0.1 with 200 pages and a sitemap with `lastmod`: pages a minute on
            the first read, a freshness pass (fetches, share answered "not modified", pages re-read),
            five pushed addresses, the widget's change hint, and the daily page and fetch limits
            stopping a read and resuming it the next day.

The real run starts its own PostgreSQL and Redis with testcontainers (it never touches your stack's
data and never starts the compose stack), uses a stub embedder and an in-memory vector store, and
makes no paid call: the only hosted model nobody calls is the fake one. The website section only ever
talks to 127.0.0.1 (a guard refuses any other address, and the site's pretend host name is mapped to
127.0.0.1 inside this process only). `--dry-run` runs every section against in-process fakes with small
sizes, writes only into a temporary folder, and labels its numbers as not measured.

Results: benchmarks/results/fair-use-<label>.json and .md (the numbers go into the README, the
Benchmarks page and benchmarks/README.md in the same commit).
"""

import argparse
import asyncio
import hashlib
import json
import math
import os
import platform
import random
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

if __package__ in (None, ""):  # `python benchmarks/fair_use.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("LITELLM_MODE", "PRODUCTION")

BENCH = Path(__file__).resolve().parent
ROOT = BENCH.parent
RESULTS = BENCH / "results"
SECTIONS = ("gate", "lanes", "throttle", "local", "website")
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
SITE_HOST = "shreedental.test"  # pretend host name, mapped to 127.0.0.1 inside this process only
EXIT_OK, EXIT_FAILED, EXIT_USAGE = 0, 1, 3

# Targets from the plan (PIP4 task 7); a miss is reported in words, never hidden.
GATE_P95_ADDED_MS = 5.0
HIGH_WAIT_P95_S = 0.5

INTEGRATION_TESTS = (
    "tests/integration/test_business_smoke.py",
    "tests/integration/test_pipeline_profiles_migration.py",
    "tests/integration/test_lanes_redis.py",
    "tests/integration/test_fair_use_migration.py",
    "tests/integration/test_fair_use_flow.py",
)

QUESTIONS: dict[str, list[str]] = {
    "en": [
        "What are your clinic timings?",
        "How much does a root canal cost?",
        "Do you take appointments on Sunday?",
        "Where is the clinic in Pune?",
        "Is teeth whitening painful?",
        "Can I pay by UPI?",
        "How long does a check-up take?",
        "Do you treat children?",
    ],
    "hi": [
        "क्लिनिक कितने बजे खुलता है?",
        "रूट कैनाल का खर्चा कितना है?",
        "क्या रविवार को अपॉइंटमेंट मिलता है?",
        "क्लिनिक पुणे में कहाँ है?",
        "क्या बच्चों का इलाज होता है?",
        "क्या UPI से भुगतान हो सकता है?",
    ],
    "hi-Latn": [
        "clinic kitne baje khulta hai",
        "root canal ka kharcha kitna hai",
        "Sunday ko appointment milta hai kya",
        "clinic Pune mein kahan hai",
        "bachchon ka ilaaj hota hai kya",
        "UPI se payment ho sakta hai kya",
    ],
}


# ------------------------------------------------------------------------------------ helpers


def percentile(values: Sequence[float], q: float) -> float | None:
    """The q-th percentile (0 to 100) by linear interpolation; None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (len(ordered) - 1) * q / 100
    low, high = math.floor(rank), math.ceil(rank)
    return float(ordered[low] + (ordered[high] - ordered[low]) * (rank - low))


def rnd(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def stats_ms(values_s: Sequence[float]) -> dict[str, float | None]:
    """p50/p90/p95/max of durations given in seconds, as milliseconds."""
    ms = [v * 1000 for v in values_s]
    return {
        "n": len(ms),  # type: ignore[dict-item]
        "p50_ms": rnd(percentile(ms, 50)),
        "p90_ms": rnd(percentile(ms, 90)),
        "p95_ms": rnd(percentile(ms, 95)),
        "max_ms": rnd(max(ms) if ms else None),
    }


class NotLocalError(RuntimeError):
    """The website section was asked to touch something that is not this machine."""


def assert_local(url: str) -> str:
    host = urlsplit(url).hostname or ""
    if host not in LOCAL_HOSTS:
        raise NotLocalError(f"The website section only talks to 127.0.0.1, not {host!r}")
    return url


@dataclass
class Sizes:
    """How big each section is. The real run uses the plan's numbers; a dry run uses tiny ones."""

    gate_asks: int = 300
    gate_warmup: int = 20
    lane_slots: int = 4
    lane_low: int = 50
    lane_high: int = 5
    lane_answer_s: float = 2.0
    lane_max_wait_s: float = 20.0
    lane_lease_s: int = 30
    lane_poll_ms: tuple[int, int] = (120, 300)
    throttle_limit: int = 30
    throttle_asks: int = 50
    local_concurrent: int = 4
    site_pages: int = 200
    site_changed: int = 10
    site_pushed: int = 5
    site_day_pages: int = 50
    site_day_fetches: int = 120

    @classmethod
    def quick(cls) -> "Sizes":
        return cls(
            gate_asks=20,
            gate_warmup=2,
            lane_slots=2,
            lane_low=6,
            lane_high=2,
            lane_answer_s=0.05,
            lane_max_wait_s=0.4,
            lane_lease_s=2,
            lane_poll_ms=(5, 10),
            throttle_limit=6,
            throttle_asks=12,
            local_concurrent=3,
            site_pages=24,
            site_changed=3,
            site_pushed=2,
            site_day_pages=8,
            site_day_fetches=20,
        )


@dataclass
class Section:
    key: str
    title: str
    ok: bool | None = None  # None: nothing to pass or fail (a measurement), or skipped
    skipped: str | None = None
    numbers: dict[str, Any] = field(default_factory=dict)
    lines: list[str] = field(default_factory=list)  # plain-language results
    targets: list[tuple[str, bool]] = field(default_factory=list)  # (sentence, met)
    errors: list[str] = field(default_factory=list)

    def say(self, line: str) -> None:
        self.lines.append(line)
        print(f"  {line}", flush=True)

    def target(self, sentence: str, met: bool) -> None:
        self.targets.append((sentence, met))
        print(f"  [{'met' if met else 'MISSED'}] {sentence}", flush=True)

    def finish(self) -> "Section":
        if self.skipped:
            self.ok = None
        elif self.errors:
            self.ok = False
        elif self.targets:
            self.ok = all(met for _, met in self.targets)
        return self

    def to_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "ok": self.ok,
            "skipped": self.skipped,
            "numbers": self.numbers,
            "targets": [{"target": t, "met": m} for t, m in self.targets],
            "errors": self.errors,
        }


# ------------------------------------------------------------------- the environments (real, dry)


class Env:
    """What the sections need from the outside world. `DryEnv` fakes it; `RealEnv` is Docker."""

    dry = True
    label = ""

    async def gate_driver(self, sizes: Sizes) -> "GateDriver":
        raise NotImplementedError

    async def throttle_driver(self, sizes: Sizes) -> "ThrottleDriver":
        raise NotImplementedError

    async def lane_redis(self) -> Any:
        raise NotImplementedError

    def local_client(self, args: argparse.Namespace) -> tuple[httpx.AsyncClient | None, str | None]:
        raise NotImplementedError

    async def website_driver(self, sizes: Sizes) -> "WebsiteDriver":
        raise NotImplementedError


class GateDriver:
    scenarios = ("no limits", "4 limits", "all 12 limits")

    async def prepare(self, scenario: str) -> None:
        raise NotImplementedError

    async def ask(self) -> float | None:
        """One answer's gate. Returns the seconds the gate itself took, or None to time it outside."""
        raise NotImplementedError

    async def close(self) -> None:
        return None


@dataclass
class Decision:
    action: str  # allow | downgrade | pause
    profile: str
    paused_by: str | None = None


class ThrottleDriver:
    mails: list[tuple[str, str]]  # (template, level %)
    webhooks: list[tuple[str, str, str]]  # (event, level, action)

    async def ask(self) -> Decision:
        raise NotImplementedError

    async def state(self) -> str:
        raise NotImplementedError

    async def counters(self) -> dict[str, int]:
        raise NotImplementedError

    async def audit(self) -> list[str]:
        raise NotImplementedError

    async def next_hour(self) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        return None


@dataclass
class LearnStats:
    pages_learned: int
    pages_skipped: int
    seconds: float
    skipped_by: dict[str, int] = field(default_factory=dict)


@dataclass
class CheckStats:
    fetches: int
    not_modified: int
    reread: int
    checked: int
    seconds: float


@dataclass
class LimitStats:
    first_pages: int  # learned on the day the page limit stopped the read
    stopped_by: str | None
    resumed_started: bool
    resumed_pages: int  # learned in total after the next day's read
    fetch_stopped_by: str | None = None


class WebsiteDriver:
    async def first_learn(self) -> LearnStats:
        raise NotImplementedError

    async def freshness(self, changed: int) -> CheckStats:
        raise NotImplementedError

    async def push(self, count: int) -> float:
        raise NotImplementedError

    async def sensor_hint(self) -> tuple[str, float]:
        raise NotImplementedError

    async def limits(self) -> LimitStats:
        raise NotImplementedError

    async def close(self) -> None:
        return None


# ---------------------------------------------------------------------------- the fixture site


def _words(count: int, seed: str) -> str:
    rng = random.Random(seed)
    vocab = [
        "clinic",
        "dentist",
        "appointment",
        "cleaning",
        "crown",
        "filling",
        "whitening",
        "implant",
        "braces",
        "timing",
        "Pune",
        "Sunday",
        "UPI",
        "receipt",
        "consultation",
        "x-ray",
        "gum",
        "enamel",
        "floss",
        "checkup",
        "treatment",
        "price",
        "Monday",
    ]
    return " ".join(rng.choice(vocab) for _ in range(count))


class FixtureSite:
    """A website on 127.0.0.1: N pages, a sitemap with `lastmod`, conditional requests (ETag), and a
    log of every request with its answer. Nothing else is ever contacted."""

    def __init__(self, pages: int) -> None:
        self.pages = pages
        self.files: dict[str, tuple[str, str, str]] = {}  # path -> (content type, etag, body)
        self.requests: list[tuple[str, str | None, int]] = []  # (path, If-None-Match, status)
        self.versions: dict[str, int] = {}
        self.lastmods: dict[
            int, str
        ] = {}  # a changed page moves its sitemap lastmod, as a real site does
        self.sitemap_base: str | None = None  # the host the sitemap names (default: this server)
        self._server: ThreadingHTTPServer | None = None
        self.start()
        for i in range(pages):
            self.set_page(f"/p/{i}", 0)
        self._sitemap()

    def page_path(self, i: int) -> str:
        return f"/p/{i}"

    def _body(self, i: int, version: int) -> str:
        return (
            f"<!doctype html><html><head><title>Page {i}</title></head><body><main>"
            f"<h1>Shree Dental Clinic, Pune: page {i}</h1><p>Version {version}.</p>"
            f"<p>{_words(120, f'{i}:{version}')}</p></main></body></html>"
        )

    def put(self, path: str, body: str, kind: str = "text/html; charset=utf-8") -> None:
        self.files[path] = (kind, '"' + hashlib.md5(body.encode()).hexdigest() + '"', body)

    def set_page(self, path_or_index: str | int, version: int | None = None) -> None:
        i = int(str(path_or_index).rsplit("/", 1)[-1])
        path = self.page_path(i)
        v = self.versions.get(path, 0) + 1 if version is None else version
        self.versions[path] = v
        if version is None:
            self.lastmods[i] = datetime.now(UTC).strftime("%Y-%m-%d")
        self.put(path, self._body(i, v))

    def change(self, count: int, *, start: int = 0) -> list[int]:
        """Rewrite `count` pages (new text, so a new ETag); returns their numbers."""
        numbers = list(range(start, start + count))
        for i in numbers:
            self.set_page(i)
        self._sitemap()
        return numbers

    def _sitemap(self) -> None:
        base = self.sitemap_base or self.base
        self.put(
            "/sitemap.xml",
            '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + "".join(
                f"<url><loc>{base}{self.page_path(i)}</loc>"
                f"<lastmod>{self.lastmods.get(i, '2026-09-30')}</lastmod></url>"
                for i in range(self.pages)
            )
            + "</urlset>",
            "application/xml",
        )
        self.put("/robots.txt", "User-agent: *\nDisallow: /private/\n", "text/plain")
        links = "".join(
            f'<a href="{self.page_path(i)}">Page {i}</a> ' for i in range(min(self.pages, 12))
        )
        self.put(
            "/",
            "<!doctype html><html><head><title>Shree Dental Clinic, Pune</title></head><body>"
            f"<main><h1>Shree Dental Clinic, Pune</h1><nav>{links}</nav>"
            f"<p>{_words(80, 'home')}</p></main></body></html>",
        )

    @property
    def port(self) -> int:
        assert self._server is not None
        return int(self._server.server_address[1])

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        site = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                path = urlsplit(self.path).path
                inm = self.headers.get("If-None-Match")
                entry = site.files.get(path)
                if entry is None:
                    site.requests.append((path, inm, 404))
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                kind, etag, body = entry
                if inm == etag:
                    site.requests.append((path, inm, 304))
                    self.send_response(304)
                    self.send_header("ETag", etag)
                    self.end_headers()
                    return
                data = body.encode("utf-8")
                site.requests.append((path, inm, 200))
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("ETag", etag)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args: Any) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    def page_requests(self, since: int = 0) -> list[tuple[str, str | None, int]]:
        return [r for r in self.requests[since:] if r[0].startswith("/p/")]


# --------------------------------------------------------------------------------- fake Redis


class FakeRedis:
    """Just enough of Redis for `AnswerLanes`: SET NX EX, GET, the compare-and-delete and refresh
    scripts, SCAN and DEL. Keys expire like real ones."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[str, float]] = {}

    def _live(self, key: str) -> str | None:
        found = self._data.get(key)
        if found is None:
            return None
        value, expires = found
        if expires <= time.monotonic():
            del self._data[key]
            return None
        return value

    async def set(
        self, key: str, value: str, nx: bool = False, ex: int | None = None
    ) -> bool | None:
        if nx and self._live(key) is not None:
            return None
        self._data[key] = (value, time.monotonic() + (ex if ex else 10**9))
        return True

    async def get(self, key: str) -> str | None:
        return self._live(key)

    async def delete(self, *keys: str) -> int:
        return sum(1 for k in keys if self._data.pop(k, None) is not None)

    async def eval(self, script: str, numkeys: int, key: str, *args: Any) -> int:
        if self._live(key) != args[0]:
            return 0
        if "'del'" in script:
            del self._data[key]
            return 1
        value, _ = self._data[key]
        self._data[key] = (value, time.monotonic() + int(args[1]))
        return 1

    async def scan_iter(self, match: str = "*") -> Any:
        prefix = match.rstrip("*")
        for key in list(self._data):
            if key.startswith(prefix) and self._live(key) is not None:
                yield key

    async def aclose(self) -> None:
        return None


# -------------------------------------------------------------------------------------- dry env


class DryGate(GateDriver):
    """A stand-in: each scenario costs a little more work, like more limits to read."""

    def __init__(self) -> None:
        self._work = 0

    async def prepare(self, scenario: str) -> None:
        self._work = {"no limits": 1, "4 limits": 4, "all 12 limits": 12}[scenario]

    async def ask(self) -> float | None:
        sum(range(self._work * 50))  # a little work per limit
        await asyncio.sleep(0)
        return None


class DryThrottle(ThrottleDriver):
    """Counts asks and decides like a 30-an-hour Free plan scaled to `limit` (hard line 150 %)."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.hard = math.ceil(limit * 1.5)
        self.used = 0
        self.mails = []
        self.webhooks = []
        self._state = "ok"
        self._seen: set[int] = set()
        self._decisions: dict[str, int] = {}
        self._audit: list[str] = []

    async def ask(self) -> Decision:
        used = self.used
        self.used += 1
        for level, at in ((80, math.ceil(self.limit * 0.8)), (100, self.limit), (150, self.hard)):
            if used + 1 >= at and level not in self._seen and level != 150:
                self._seen.add(level)
                self.mails += [("fair_use_alert", str(level)), ("fair_use_alert_admin", str(level))]
        if used >= self.hard:
            if 150 not in self._seen:
                self._seen.add(150)
                self.mails += [("fair_use_alert", "150"), ("fair_use_alert_admin", "150")]
                self.webhooks.append(("fair_use.limit_reached", "hard", "pause"))
                self._audit.append("fair_use.stopped")
            self._state = "stopped"
            self._decisions["decisions_paused"] = self._decisions.get("decisions_paused", 0) + 1
            return Decision("pause", "-", "fair_use")
        if used >= self.limit:
            if self._state == "ok":
                self.webhooks.append(("fair_use.limit_reached", "soft", "downgrade"))
                self._audit.append("fair_use.throttled")
            self._state = "throttled"
            self._decisions["decisions_downgraded"] = (
                self._decisions.get("decisions_downgraded", 0) + 1
            )
            return Decision("downgrade", "Economy")
        return Decision("allow", "Standard")

    async def state(self) -> str:
        return self._state

    async def counters(self) -> dict[str, int]:
        return dict(self._decisions)

    async def audit(self) -> list[str]:
        return list(self._audit)

    async def next_hour(self) -> None:
        self.used, self._state = 0, "ok"
        self._audit.append("fair_use.resumed")


class DryWebsite(WebsiteDriver):
    """A plain crawler of the local fixture site: sitemap, then each page, then conditional
    requests. It shows the harness's measuring works; it measures nothing about the engine."""

    def __init__(self, sizes: Sizes) -> None:
        self.sizes = sizes
        self.site = FixtureSite(sizes.site_pages)
        self.etags: dict[str, str] = {}
        self.bodies: dict[str, str] = {}
        self.client = httpx.Client(timeout=10, trust_env=False)

    def _get(self, path: str, etag: str | None = None) -> httpx.Response:
        url = assert_local(self.site.base + path)
        return self.client.get(url, headers={"If-None-Match": etag} if etag else {})

    async def first_learn(self) -> LearnStats:
        started = time.monotonic()
        sitemap = self._get("/sitemap.xml").text
        paths = [urlsplit(m).path for m in re.findall(r"<loc>([^<]+)</loc>", sitemap)]
        skipped = 0
        for path in paths:
            r = self._get(path)
            if r.status_code == 200:
                self.etags[path] = r.headers["etag"]
                self.bodies[path] = r.text
            else:
                skipped += 1
        return LearnStats(len(self.etags), skipped, time.monotonic() - started)

    async def freshness(self, changed: int) -> CheckStats:
        self.site.change(changed)
        before = len(self.site.requests)
        started = time.monotonic()
        reread = 0
        for path in list(self.etags):
            r = self._get(path, self.etags[path])
            if r.status_code == 200:
                reread += 1
                self.etags[path] = r.headers["etag"]
        made = self.site.requests[before:]
        return CheckStats(
            fetches=len(made),
            not_modified=sum(1 for _, _, s in made if s == 304),
            reread=reread,
            checked=len(self.etags),
            seconds=time.monotonic() - started,
        )

    async def push(self, count: int) -> float:
        numbers = self.site.change(count, start=self.sizes.site_changed)
        started = time.monotonic()
        for i in numbers:
            r = self._get(self.site.page_path(i))
            self.etags[self.site.page_path(i)] = r.headers["etag"]
        return time.monotonic() - started

    async def sensor_hint(self) -> tuple[str, float]:
        i = self.sizes.site_changed + self.sizes.site_pushed
        self.site.set_page(i)
        started = time.monotonic()
        r = self._get(self.site.page_path(i))
        self.etags[self.site.page_path(i)] = r.headers["etag"]
        return "queued", time.monotonic() - started

    async def limits(self) -> LimitStats:
        day = self.sizes.site_day_pages
        learned = min(day, len(self.etags))
        return LimitStats(
            first_pages=learned,
            stopped_by="daily_limit",
            resumed_started=True,
            resumed_pages=min(2 * day, len(self.etags)),
            fetch_stopped_by="fetch_limit",
        )

    async def close(self) -> None:
        self.client.close()
        self.site.stop()


class DryEnv(Env):
    dry = True

    async def gate_driver(self, sizes: Sizes) -> GateDriver:
        return DryGate()

    async def throttle_driver(self, sizes: Sizes) -> ThrottleDriver:
        return DryThrottle(sizes.throttle_limit)

    async def lane_redis(self) -> Any:
        return FakeRedis()

    def local_client(self, args: argparse.Namespace) -> tuple[httpx.AsyncClient | None, str | None]:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(_fake_widget), base_url="http://127.0.0.1:8000"
        ), None

    async def website_driver(self, sizes: Sizes) -> WebsiteDriver:
        return DryWebsite(sizes)


def _fake_widget(request: httpx.Request) -> httpx.Response:
    """A pretend `POST /api/v1/widget/chat`: a queued stage, a few tokens, sources, done."""
    body = json.loads(request.content or b"{}")
    n = len(str(body.get("message", "")))
    frames = [
        ("stage", {"stage": "queued"}) if n % 5 == 0 else ("stage", {"stage": "searching"}),
        ("sources", {"sources": [{"file_name": "faq", "snippet": "timings"}] if n % 4 else []}),
        *[("token", {"content": "word "}) for _ in range(6)],
        ("done", {"route": "answer"}),
    ]
    text = "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in frames)
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=text)


# ------------------------------------------------------------------------------------ the sections


async def section_gate(env: Env, sizes: Sizes) -> Section:
    out = Section("gate", "Gate overhead")
    print("Gate overhead: one fair-use decision per answer", flush=True)
    driver = await env.gate_driver(sizes)
    results: dict[str, dict[str, Any]] = {}
    try:
        for scenario in driver.scenarios:
            await driver.prepare(scenario)
            for _ in range(sizes.gate_warmup):
                await driver.ask()
            durations: list[float] = []
            for _ in range(sizes.gate_asks):
                started = time.perf_counter()
                inner = await driver.ask()
                durations.append(inner if inner is not None else time.perf_counter() - started)
            results[scenario] = stats_ms(durations)
            s = results[scenario]
            out.say(f"{scenario}: p50 {s['p50_ms']} ms, p95 {s['p95_ms']} ms over {s['n']} answers")
    except Exception as exc:  # report, do not crash the run
        out.errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        await driver.close()
    out.numbers = {"scenarios": results}
    base = (results.get("no limits") or {}).get("p95_ms")
    for scenario in ("4 limits", "all 12 limits"):
        got = (results.get(scenario) or {}).get("p95_ms")
        if base is not None and got is not None:
            added = round(got - base, 2)
            out.numbers[f"added_p95_ms_{scenario.replace(' ', '_')}"] = added
            out.target(
                f"{scenario} add at most {GATE_P95_ADDED_MS:g} ms at p95 (added {added} ms)",
                added <= GATE_P95_ADDED_MS,
            )
    return out.finish()


async def section_lanes(env: Env, sizes: Sizes) -> Section:
    from callobuzz_rag.tiers.lanes import AnswerLanes, LaneConfig, LaneTimeout

    out = Section("lanes", "Answers under load")
    print(
        f"Answer lanes: {sizes.lane_low} low and {sizes.lane_high} high answers at once on "
        f"{sizes.lane_slots} slots",
        flush=True,
    )
    redis = await env.lane_redis()
    cfg = LaneConfig(
        slots=sizes.lane_slots,
        reserve_high=1,
        low_max=0,
        lease_s=sizes.lane_lease_s,
        max_wait_s=sizes.lane_max_wait_s,
        poll_ms=sizes.lane_poll_ms,
    )
    lanes = AnswerLanes(redis, {"engine": cfg, "local": cfg})
    waits: dict[str, list[float]] = {"high": [], "low": []}
    busy = {"high": 0, "low": 0}

    async def one(priority: str, delay: float) -> None:
        await asyncio.sleep(delay)
        try:
            lease = await lanes.acquire(
                pool="engine",
                tenant_id=uuid.uuid4(),
                priority=priority,
                max_concurrency=None,  # type: ignore[arg-type]
            )
        except LaneTimeout:
            busy[priority] += 1
            return
        waits[priority].append(lease.waited_ms / 1000)
        try:
            await asyncio.sleep(sizes.lane_answer_s)  # the fake answer
        finally:
            await lease.release()

    try:
        started = time.monotonic()
        low = [one("low", 0.0) for _ in range(sizes.lane_low)]
        # The high-priority answers arrive once the low-priority flood is already queued.
        high = [one("high", 0.5 * sizes.lane_answer_s) for _ in range(sizes.lane_high)]
        await asyncio.gather(*low, *high)
        elapsed = time.monotonic() - started
        out.numbers = {
            "slots": sizes.lane_slots,
            "answer_s": sizes.lane_answer_s,
            "elapsed_s": rnd(elapsed),
            "high": {**stats_ms(waits["high"]), "busy": busy["high"]},
            "low": {**stats_ms(waits["low"]), "busy": busy["low"]},
        }
        hw = percentile(waits["high"], 95)
        lw = percentile(waits["low"], 95)
        out.say(
            f"high priority: waited p95 {rnd((hw or 0) * 1000)} ms, busy replies {busy['high']} of {sizes.lane_high}"
        )
        out.say(
            f"low priority: waited p95 {rnd((lw or 0) * 1000)} ms, busy replies {busy['low']} of {sizes.lane_low}"
        )
        # Nothing may be left behind: every slot key is gone, at the latest when its lease ends.
        deadline = time.monotonic() + sizes.lane_lease_s + 1
        leaked = [k async for k in redis.scan_iter("pip:*")]
        while leaked and time.monotonic() < deadline:
            await asyncio.sleep(0.2)
            leaked = [k async for k in redis.scan_iter("pip:*")]
        out.numbers["slot_keys_left"] = len(leaked)
        out.say(f"slots left behind after the run: {len(leaked)}")
        limit = HIGH_WAIT_P95_S if not env.dry else max(HIGH_WAIT_P95_S, 4 * sizes.lane_answer_s)
        out.target(
            f"high priority waits under {limit:g} s at p95 (waited {rnd(hw, 3)} s)",
            (hw or 0) < limit,
        )
        out.target("no slot is left behind", not leaked)
    except Exception as exc:
        out.errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        await redis.aclose()
    return out.finish()


async def section_throttle(env: Env, sizes: Sizes) -> Section:
    out = Section("throttle", "Throttle then pause")
    limit, hard = sizes.throttle_limit, math.ceil(sizes.throttle_limit * 1.5)
    print(f"Throttle to pause: a Free-plan business at {limit} questions an hour", flush=True)
    driver = await env.throttle_driver(sizes)
    try:
        actions: list[Decision] = []
        for _ in range(sizes.throttle_asks):
            actions.append(await driver.ask())
        plan_n = sum(1 for d in actions[:limit] if d.action == "allow")
        slow_n = sum(1 for d in actions[limit:hard] if d.action == "downgrade")
        paused_n = sum(1 for d in actions[hard:] if d.action == "pause")
        out.say(f"answers 1 to {limit} on the plan's profile: {plan_n} of {limit}")
        out.say(f"answers {limit + 1} to {hard}, slower: {slow_n} of {hard - limit}")
        out.say(f"answers {hard + 1} and on, paused: {paused_n} of {max(len(actions) - hard, 0)}")
        owner = [lvl for t, lvl in driver.mails if t == "fair_use_alert"]
        admin = [lvl for t, lvl in driver.mails if t == "fair_use_alert_admin"]
        out.say(
            f"mails to the owner at {owner}, to you at {admin}; webhooks {[w[1] for w in driver.webhooks]}"
        )
        counters = await driver.counters()
        state = await driver.state()
        out.say(f"decisions recorded {counters}; the business's state is now '{state}'")
        await driver.next_hour()
        back = await driver.ask()
        out.say(
            f"the next hour: answered on '{back.profile}' again ({back.action}), audit {await driver.audit()}"
        )
        out.numbers = {
            "limit": limit,
            "hard_line": hard,
            "on_plan": plan_n,
            "slowed": slow_n,
            "paused": paused_n,
            "owner_mails": owner,
            "admin_mails": admin,
            "webhooks": [list(w) for w in driver.webhooks],
            "decisions": counters,
            "state_after_pause": state,
            "back_after_hour": back.action,
        }
        out.target(f"answers 1 to {limit} are on the plan's profile", plan_n == limit)
        out.target(f"answers {limit + 1} to {hard} are slower", slow_n == hard - limit)
        out.target(
            f"answers {hard + 1} and on are paused", paused_n == max(len(actions) - hard, 0) > 0
        )
        out.target(
            "one mail to the owner and one to you per level (80, 100 and the hard line)",
            owner == ["80", "100", "150"] and admin == ["80", "100", "150"],
        )
        out.target(
            "one webhook per level (at the limit and at the hard line)",
            [w[1] for w in driver.webhooks] == ["soft", "hard"],
        )
        out.target("the decisions are recorded", bool(counters))
        out.target("the next hour answers on the plan's profile again", back.action == "allow")
    except Exception as exc:
        out.errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        await driver.close()
    return out.finish()


def _parse_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    frames: list[tuple[str, dict[str, Any]]] = []
    for block in text.split("\n\n"):
        event, data = None, {}
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                try:
                    data = json.loads(line[len("data: ") :])
                except ValueError:
                    data = {}
        if event:
            frames.append((event, data))
    return frames


async def _ask_widget(
    client: httpx.AsyncClient, headers: dict[str, str], question: str
) -> dict[str, Any]:
    started = time.perf_counter()
    first: float | None = None
    buffer = ""
    queued = 0
    async with client.stream(
        "POST", "/api/v1/widget/chat", json={"message": question}, headers=headers, timeout=900
    ) as r:
        status = r.status_code
        async for chunk in r.aiter_text():
            buffer += chunk
            if first is None and "event: token" in buffer:
                first = time.perf_counter() - started
    total = time.perf_counter() - started
    frames = _parse_sse(buffer)
    queued = sum(1 for e, d in frames if e == "stage" and d.get("stage") == "queued")
    sources = [s for e, d in frames if e == "sources" for s in d.get("sources", [])]
    done = next((d for e, d in frames if e == "done"), {})
    return {
        "status": status,
        "first_s": first if first is not None else total,
        "total_s": total,
        "cited": bool(sources),
        "queued": queued,
        "route": done.get("route"),
    }


async def section_local(env: Env, sizes: Sizes, args: argparse.Namespace) -> Section:
    out = Section("local", "The free local model, end to end")
    client, skip = env.local_client(args)
    if client is None:
        out.skipped = skip or "needs --engine-url, --widget-key and --origin"
        print(f"Local model: skipped ({out.skipped})", flush=True)
        return out.finish()
    print("Local model: 20 widget questions on the Free profile", flush=True)
    headers = {
        "X-Widget-Key": args.widget_key or "pk_dry",
        "Origin": args.origin or "http://127.0.0.1",
    }
    questions: dict[str, list[str]] = QUESTIONS
    if args.questions_file:
        questions = json.loads(Path(args.questions_file).read_text(encoding="utf-8"))
    try:
        async with client:
            rows: list[dict[str, Any]] = []
            by_lang: dict[str, list[dict[str, Any]]] = {}
            for lang, items in questions.items():
                for q in items:
                    row = await _ask_widget(client, headers, q)
                    row["lang"] = lang
                    rows.append(row)
                    by_lang.setdefault(lang, []).append(row)
            ok_rows = [r for r in rows if r["status"] == 200]
            firsts = [r["first_s"] for r in ok_rows]
            totals = [r["total_s"] for r in ok_rows]
            cited = sum(1 for r in ok_rows if r["cited"])
            out.say(f"{len(ok_rows)} of {len(rows)} questions answered")
            out.say(
                f"first words: p50 {rnd((percentile(firsts, 50) or 0), 2)} s, p90 {rnd((percentile(firsts, 90) or 0), 2)} s"
            )
            out.say(
                f"whole answer: p50 {rnd((percentile(totals, 50) or 0), 2)} s, p90 {rnd((percentile(totals, 90) or 0), 2)} s"
            )
            pct = round(100 * cited / len(ok_rows)) if ok_rows else 0
            out.say(f"answers that cite a source: {pct}%")
            for lang, rs in by_lang.items():
                t = [r["total_s"] for r in rs if r["status"] == 200]
                out.say(f"  {lang}: whole answer p50 {rnd(percentile(t, 50), 2)} s over {len(t)}")
            # Several asking at once: with one local slot the others wait their turn.
            n = sizes.local_concurrent
            solo = percentile(totals, 50) or 0.0
            burst = await asyncio.gather(
                *[
                    _ask_widget(client, headers, questions["en"][i % len(questions["en"])])
                    for i in range(n)
                ]
            )
            waits = [max(r["total_s"] - solo, 0.0) for r in burst]
            queued = sum(r["queued"] for r in burst)
            out.say(
                f"{n} at once: slowest finished after {rnd(max(r['total_s'] for r in burst), 2)} s; "
                f"extra wait p50 {rnd(percentile(waits, 50), 2)} s; 'One moment' shown {queued} times"
            )
            out.numbers = {
                "questions": len(rows),
                "answered": len(ok_rows),
                "first_s": {"p50": rnd(percentile(firsts, 50)), "p90": rnd(percentile(firsts, 90))},
                "total_s": {"p50": rnd(percentile(totals, 50)), "p90": rnd(percentile(totals, 90))},
                "cited_pct": pct,
                "by_language_total_p50_s": {
                    lang: rnd(percentile([r["total_s"] for r in rs if r["status"] == 200], 50))
                    for lang, rs in by_lang.items()
                },
                "concurrent": {
                    "n": n,
                    "extra_wait_p50_s": rnd(percentile(waits, 50)),
                    "queued_shown": queued,
                },
            }
            out.target(
                "every question was answered (an answer or the polite pause, never an error)",
                len(ok_rows) == len(rows),
            )
    except Exception as exc:
        out.errors.append(f"{type(exc).__name__}: {exc}")
    return out.finish()


async def section_website(env: Env, sizes: Sizes) -> Section:
    out = Section("website", "Website learning, freshness and push")
    print(f"Website learning: a fixture site of {sizes.site_pages} pages on 127.0.0.1", flush=True)
    driver = await env.website_driver(sizes)
    try:
        first = await driver.first_learn()
        per_min = round(first.pages_learned / first.seconds * 60, 1) if first.seconds > 0 else None
        out.say(
            f"first read: {first.pages_learned} pages kept and {first.pages_skipped} skipped in "
            f"{rnd(first.seconds, 2)} s ({per_min} pages a minute)"
        )
        if first.skipped_by:
            out.say(f"  skipped because: {first.skipped_by}")
        fresh = await driver.freshness(sizes.site_changed)
        share = round(100 * fresh.not_modified / fresh.fetches) if fresh.fetches else 0
        out.say(
            f"freshness pass: {fresh.fetches} fetches, {share}% answered 'not modified', "
            f"{fresh.reread} of {fresh.checked} pages re-read in {rnd(fresh.seconds, 2)} s"
        )
        pushed = await driver.push(sizes.site_pushed)
        out.say(f"push: {sizes.site_pushed} addresses re-read {rnd(pushed, 2)} s after the push")
        outcome, hint = await driver.sensor_hint()
        out.say(f"the widget's change hint: '{outcome}', page re-read {rnd(hint, 2)} s later")
        lim = await driver.limits()
        out.say(
            f"daily limits: the read stopped at {lim.first_pages} pages (stopped by {lim.stopped_by}); "
            f"next day it {'resumed' if lim.resumed_started else 'did NOT resume'} and holds {lim.resumed_pages} pages; "
            f"fetch limit stopped by {lim.fetch_stopped_by}"
        )
        out.numbers = {
            "pages": sizes.site_pages,
            "first_read": {
                "learned": first.pages_learned,
                "skipped": first.pages_skipped,
                "seconds": rnd(first.seconds),
                "pages_per_min": per_min,
                "skipped_by": first.skipped_by,
            },
            "freshness": {
                "fetches": fresh.fetches,
                "not_modified_pct": share,
                "reread": fresh.reread,
                "checked": fresh.checked,
                "seconds": rnd(fresh.seconds),
            },
            "push": {"urls": sizes.site_pushed, "seconds": rnd(pushed)},
            "sensor": {"outcome": outcome, "seconds": rnd(hint)},
            "limits": {
                "pages_first_day": lim.first_pages,
                "stopped_by": lim.stopped_by,
                "resumed": lim.resumed_started,
                "pages_after_resume": lim.resumed_pages,
                "fetch_stopped_by": lim.fetch_stopped_by,
            },
        }
        out.target(
            "the first read kept every page of the site",
            first.pages_learned >= sizes.site_pages - 1,
        )
        out.target(
            "a freshness pass re-reads only the pages that changed",
            fresh.reread == sizes.site_changed,
        )
        out.target("most freshness fetches are answered 'not modified'", share >= 50)
        out.target(
            "a read stops at the daily page limit and resumes the next day",
            lim.stopped_by in ("daily_limit", "fetch_limit")
            and lim.resumed_started
            and lim.resumed_pages > lim.first_pages,
        )
    except Exception as exc:
        out.errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        await driver.close()
    return out.finish()


# -------------------------------------------------------------------- the integration re-runs


def run_integration(skip: bool, dry: bool) -> dict[str, Any]:
    """Re-run the business smoke and the PIP integration tests (Docker up) and record the outcome."""
    if dry or skip:
        return {"skipped": "dry run" if dry else "--skip-integration", "tests": {}}
    results: dict[str, Any] = {}
    for path in INTEGRATION_TESTS:
        if not (ROOT / path).exists():
            results[path] = {"status": "missing"}
            continue
        started = time.monotonic()
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                path,
                "-q",
                "-m",
                "integration",
                "-p",
                "no:cacheprovider",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        tail = (proc.stdout or "").strip().splitlines()[-1:] or [""]
        results[path] = {
            "status": "passed" if proc.returncode == 0 else "failed",
            "seconds": round(time.monotonic() - started, 1),
            "summary": tail[0],
        }
        print(f"  {path}: {results[path]['status']} ({results[path]['summary']})", flush=True)
    return {"tests": results}


# ------------------------------------------------------------------------------------- the report


def hardware() -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpus": os.cpu_count(),
        "processor": platform.processor() or None,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Fair use, answer lanes, local model and website learning: {report['label']}",
        "",
        f"Date: {report['date']} · Mode: {report['mode']}",
        "",
    ]
    if report["dry_run"]:
        lines += [
            "> **Not measured.** This is a dry run against in-process fakes: it proves the harness runs, "
            "not how the engine performs. Do not publish these numbers.",
            "",
        ]
    lines += [f"Machine: {report['hardware']['platform']}, {report['hardware']['cpus']} CPUs.", ""]
    for s in report["sections"]:
        lines += [f"## {s['title']}", ""]
        if s["skipped"]:
            lines += [f"Skipped: {s['skipped']}", ""]
            continue
        for line in s["lines"]:
            lines.append(f"- {line}")
        lines.append("")
        if s["targets"]:
            lines += ["| Target | Result |", "|---|---|"]
            lines += [
                f"| {t['target']} | {'met' if t['met'] else 'missed'} |" for t in s["targets"]
            ]
            lines.append("")
        for e in s["errors"]:
            lines.append(f"**Error:** {e}")
        if s["errors"]:
            lines.append("")
    integ = report.get("integration") or {}
    lines += ["## Existing integration tests re-run", ""]
    if integ.get("skipped"):
        lines += [f"Not run: {integ['skipped']}", ""]
    else:
        lines += ["| Test file | Result |", "|---|---|"]
        for path, r in (integ.get("tests") or {}).items():
            lines.append(f"| `{path}` | {r['status']} ({r.get('summary', '')}) |")
        lines.append("")
    done = report.get("business_done_check")
    if done:
        lines += ["## Business layer (BIZ1 to BIZ5) done-check", "", done, ""]
    return "\n".join(lines)


async def run_sections(
    env: Env, sizes: Sizes, wanted: Sequence[str], args: argparse.Namespace
) -> list[Section]:
    runners: dict[str, Callable[[], Awaitable[Section]]] = {
        "gate": lambda: section_gate(env, sizes),
        "lanes": lambda: section_lanes(env, sizes),
        "throttle": lambda: section_throttle(env, sizes),
        "local": lambda: section_local(env, sizes, args),
        "website": lambda: section_website(env, sizes),
    }
    done: list[Section] = []
    for key in SECTIONS:
        if key in wanted:
            done.append(await runners[key]())
    return done


def build_report(
    sections: list[Section],
    *,
    label: str,
    dry: bool,
    integration: dict[str, Any],
    done_check: str | None,
) -> dict[str, Any]:
    return {
        "label": label,
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "mode": "dry run (fakes, not measured)"
        if dry
        else "real (PostgreSQL and Redis in Docker, stub models)",
        "dry_run": dry,
        "hardware": hardware(),
        "sections": [s.to_json() | {"lines": s.lines} for s in sections],
        "integration": integration,
        "business_done_check": done_check,
        "all_met": all(s.ok in (True, None) for s in sections),
    }


def write_report(report: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"fair-use-{report['label']}"
    js, md = out_dir / f"{stem}.json", out_dir / f"{stem}.md"
    js.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md.write_text(render_markdown(report) + "\n", encoding="utf-8")
    return js, md


# ------------------------------------------------------------------------------- the real world


def real_main(args: argparse.Namespace, sizes: Sizes, wanted: Sequence[str]) -> list[Section]:
    """Start PostgreSQL and Redis with testcontainers, migrate, run the sections."""
    from testcontainers.postgres import PostgresContainer
    from testcontainers.redis import RedisContainer
    from tests.integration.conftest import _async_url  # the same helpers the integration tests use
    from tests.integration.test_accounts_flow import _alembic, _redis_url

    with (
        PostgresContainer("postgres:16-alpine") as pg,
        RedisContainer("valkey/valkey:8.1-alpine") as redis,
    ):
        db_url = _async_url(pg)
        _alembic(db_url, "head")  # alembic's env.py needs to run outside an event loop
        env = RealEnv(db_url, _redis_url(redis), args)
        return asyncio.run(run_sections(env, sizes, wanted, args))


class RealEnv(Env):
    dry = False

    def __init__(self, db_url: str, redis_url: str, args: argparse.Namespace) -> None:
        self.db_url, self.redis_url, self.args = db_url, redis_url, args

    async def gate_driver(self, sizes: Sizes) -> GateDriver:
        return await RealGate.create(self.db_url, self.redis_url)

    async def throttle_driver(self, sizes: Sizes) -> ThrottleDriver:
        return await RealThrottle.create(self.db_url, self.redis_url, sizes.throttle_limit)

    async def lane_redis(self) -> Any:
        import redis.asyncio as aioredis

        client = aioredis.from_url(self.redis_url)  # type: ignore[no-untyped-call]
        await client.flushdb()
        return client

    def local_client(self, args: argparse.Namespace) -> tuple[httpx.AsyncClient | None, str | None]:
        if not (args.engine_url and args.widget_key and args.origin):
            return (
                None,
                "needs --engine-url, --widget-key and --origin (a running stack with the local model ready)",
            )
        return httpx.AsyncClient(base_url=args.engine_url.rstrip("/")), None

    async def website_driver(self, sizes: Sizes) -> WebsiteDriver:
        return await RealWebsite.create(self.db_url, self.redis_url, sizes)


async def _seed_business(
    factory: Any, *, limits: dict[str, Any], policy: dict[str, Any] | None = None
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """A plan, a fallback profile (Economy: low priority), a business and a knowledge base, the way
    `tests/integration/test_fair_use_flow.py` sets them up. Returns (tenant, corpus, plan, profile)."""
    from sqlalchemy import text

    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.db.models.tenant import Tenant

    async with factory() as s:
        fallback_id = uuid.uuid4()
        await s.execute(
            text(
                "INSERT INTO pipeline_profiles (id, name, label, config, updated_by) "
                "VALUES (:i, :n, 'Economy', '{\"priority\": \"low\"}'::jsonb, 'bench')"
            ),
            {"i": fallback_id, "n": f"economy-{fallback_id.hex[:8]}"},
        )
        plan_id = uuid.uuid4()
        merged = {"downgrade_profile_id": str(fallback_id), **(policy or {})}
        await s.execute(
            text(
                "INSERT INTO plans (id, name, fair_use_limits, fair_use_policy) VALUES "
                "(:i, :n, CAST(:limits AS jsonb), CAST(:policy AS jsonb))"
            ),
            {
                "i": plan_id,
                "n": f"bench-{plan_id.hex[:8]}",
                "limits": json.dumps(limits),
                "policy": json.dumps(merged),
            },
        )
        tenant = Tenant(name="Shree Dental Clinic, Pune", plan_id=plan_id)
        s.add(tenant)
        await s.flush()
        corpus = Corpus(name="KB", tenant_id=tenant.id)
        s.add(corpus)
        await s.flush()
        await s.commit()
    return tenant.id, corpus.id, plan_id, fallback_id


class _GateBase:
    """The pieces `Flow` in `tests/integration/test_fair_use_flow.py` builds: sessions, Redis, a fake
    app carrying the settings, and the mail and webhook senders replaced by recorders."""

    factory: Any
    redis: Any
    engine: Any
    tenant_id: uuid.UUID
    corpus_id: uuid.UUID
    plan_id: uuid.UUID
    app: Any
    corpus: Any

    async def _open(self, db_url: str, redis_url: str, *, limits: dict[str, Any]) -> None:
        from types import SimpleNamespace

        import redis.asyncio as aioredis
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from callobuzz_rag.core.config import Settings
        from callobuzz_rag.db.models.corpus import Corpus

        self.engine = create_async_engine(db_url, pool_size=10, max_overflow=10)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.redis = aioredis.from_url(redis_url)  # type: ignore[no-untyped-call]
        await self.redis.flushdb()
        self.tenant_id, self.corpus_id, self.plan_id, _ = await _seed_business(
            self.factory, limits=limits
        )
        settings = Settings(_env_file=None, superadmin_email="admin@callobuzz.example")  # type: ignore[call-arg]
        self.app = SimpleNamespace(
            state=SimpleNamespace(
                settings=settings, session_factory=self.factory, fair_use_redis=self.redis
            )
        )
        async with self.factory() as s:
            self.corpus = await s.get(Corpus, self.corpus_id)

    async def _gate(self) -> Any:
        from types import SimpleNamespace

        from callobuzz_rag.fairuse import gate

        request = SimpleNamespace(state=SimpleNamespace(is_admin=False), app=self.app)
        async with self.factory() as session:
            started = time.perf_counter()
            decision = await gate.answer_gate(
                session, request, self.corpus, payer="operator", surface="api"
            )  # type: ignore[arg-type]
            self._last = time.perf_counter() - started
            await session.commit()
        return decision

    async def _await_tasks(self) -> None:
        tasks = list(getattr(self.app.state, "fair_use_tasks", ()))
        if tasks:
            await asyncio.gather(*tasks)

    async def close(self) -> None:
        await self.redis.aclose()
        await self.engine.dispose()


HUGE = 10**9
FOUR = {
    "questions_per_minute": HUGE,
    "questions_per_hour": HUGE,
    "questions_per_day": HUGE,
    "fetches_per_day": HUGE,
}
ALL_TWELVE = {
    **FOUR,
    "concurrent_answers": 100_000,
    "website_pages_per_day": HUGE,
    "uploads_per_day": HUGE,
    "storage_mb": HUGE,
    "ai_spend_per_day_usd_micro": 10**15,
    "ai_spend_per_month_usd_micro": 10**15,
    "cob_questions_per_day": HUGE,
}


class RealGate(_GateBase, GateDriver):
    @classmethod
    async def create(cls, db_url: str, redis_url: str) -> "RealGate":
        self = cls()
        await self._open(db_url, redis_url, limits={})
        return self

    async def prepare(self, scenario: str) -> None:
        from sqlalchemy import text

        limits = {"no limits": {}, "4 limits": FOUR, "all 12 limits": ALL_TWELVE}[scenario]
        async with self.factory() as s:
            await s.execute(
                text(
                    "UPDATE plans SET fair_use_limits = CAST(:l AS jsonb), website_pages_max = :w WHERE id = :p"
                ),
                {
                    "l": json.dumps(limits),
                    "w": HUGE if scenario == "all 12 limits" else None,
                    "p": self.plan_id,
                },
            )
            await s.commit()
        await self.redis.flushdb()

    async def ask(self) -> float | None:
        await self._gate()
        return self._last


class RealThrottle(_GateBase, ThrottleDriver):
    @classmethod
    async def create(cls, db_url: str, redis_url: str, limit: int) -> "RealThrottle":
        from unittest.mock import patch

        self = cls()
        await self._open(db_url, redis_url, limits={"questions_per_hour": limit})
        self.mails, self.webhooks = [], []

        async def owners(session: Any, tenant_id: Any) -> list[str]:
            return ["owner@shree-dental.example"]

        def enqueue(template: str, to: str, context: Any, **_: Any) -> None:
            self.mails.append((template, str(context["level_pct"])))

        class Dispatcher:
            def __init__(self, *a: Any, **k: Any) -> None:
                pass

            async def dispatch(self, event: Any, data: dict[str, Any]) -> None:
                self.outer.webhooks.append((event.value, data["level"], data["action"]))

        Dispatcher.outer = self  # type: ignore[attr-defined]
        self._patches = [
            patch("callobuzz_rag.accounts.lifecycle.owner_emails", owners),
            patch("callobuzz_rag.accounts.email.dispatch.enqueue_email", enqueue),
            patch("callobuzz_rag.core.webhooks.WebhookDispatcher", Dispatcher),
        ]
        for p in self._patches:
            p.start()
        return self

    async def ask(self) -> Decision:
        decision = await self._gate()
        await self._await_tasks()
        return Decision(decision.action, decision.profile.name, decision.paused_by)

    async def state(self) -> str:
        from sqlalchemy import text

        async with self.factory() as s:
            row = (
                await s.execute(
                    text("SELECT state FROM tenant_pipeline_settings WHERE tenant_id = :t"),
                    {"t": self.tenant_id},
                )
            ).first()
        return row[0] if row else "ok"

    async def counters(self) -> dict[str, int]:
        from sqlalchemy import text

        async with self.factory() as s:
            rows = await s.execute(
                text(
                    "SELECT metric, value FROM fair_use_counters WHERE tenant_id = :t AND metric LIKE 'decisions_%'"
                ),
                {"t": self.tenant_id},
            )
            return {m: int(v) for m, v in rows}

    async def audit(self) -> list[str]:
        from sqlalchemy import text

        async with self.factory() as s:
            rows = await s.execute(
                text(
                    "SELECT action FROM audit_log WHERE tenant_id = :t AND action LIKE 'fair_use.%' "
                    "ORDER BY created_at, id"
                ),
                {"t": self.tenant_id},
            )
            return [r[0] for r in rows]

    async def next_hour(self) -> None:
        for key in [k async for k in self.redis.scan_iter("fu:q:*")]:
            await self.redis.delete(key)

    async def close(self) -> None:
        for p in self._patches:
            p.stop()
        await super().close()


class RealWebsite(WebsiteDriver):
    """The business smoke test's setup (`tests/integration/test_business_smoke.py`) at benchmark
    size: a business signs up from the site address, the engine reads the fixture site through its
    own learner, scheduler, push and widget hint, with a stub embedder and an in-memory vector store.
    The site's host name is mapped to 127.0.0.1 inside this process and every other name fails."""

    @classmethod
    async def create(cls, db_url: str, redis_url: str, sizes: Sizes) -> "RealWebsite":
        self = cls()
        await self._open(db_url, redis_url, sizes)
        return self

    async def _open(self, db_url: str, redis_url: str, sizes: Sizes) -> None:
        import socket
        from contextlib import ExitStack
        from dataclasses import asdict
        from unittest.mock import patch

        import redis.asyncio as aioredis
        from httpx import ASGITransport, AsyncClient
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from tests.integration.test_business_smoke import MemoryVectors, StubEmbedder, StubLLM

        import callobuzz_rag.website.push as push_mod
        import callobuzz_rag.website.service as service_mod
        from callobuzz_rag.accounts.passwords import hash_password
        from callobuzz_rag.accounts.ratelimit import AccountRateLimiter
        from callobuzz_rag.api.app import create_app
        from callobuzz_rag.chunking.chunker import chunker_from_settings
        from callobuzz_rag.core.config import Settings
        from callobuzz_rag.db.models.tenant import Tenant
        from callobuzz_rag.db.models.tenant_member import TenantMember
        from callobuzz_rag.db.models.user import User
        from callobuzz_rag.enrichment.extractive import ExtractiveEnrichmentProvider
        from callobuzz_rag.pipeline.orchestrator import IngestionOrchestrator
        from callobuzz_rag.retrieval.context_assembly import ContextAssembler
        from callobuzz_rag.retrieval.safety import ContentSanitizer
        from callobuzz_rag.worker.website_tasks import build_deps

        self.sizes = sizes
        self.stack = ExitStack()
        self.engine = create_async_engine(db_url, pool_size=10, max_overflow=10)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.redis = aioredis.from_url(redis_url)  # type: ignore[no-untyped-call]
        self.settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            session_secret="e" * 64,  # type: ignore[arg-type]
            email_provider="log",
            admin_api_key="admin-key-fair-use-bench",  # type: ignore[arg-type]
            redis_url=redis_url,
            website_allow_private_networks=True,
            website_request_delay_ms=0,
        )
        self.site = FixtureSite(sizes.site_pages)
        self.base = f"http://{SITE_HOST}:{self.site.port}"
        self.site.sitemap_base = self.base  # the sitemap names the pretend host, not 127.0.0.1
        self.site._sitemap()
        self.embedder, self.vectors, llm = StubEmbedder(), MemoryVectors(), StubLLM()
        self.dispatched: list[tuple[str, list[str], str, str]] = []
        self.learn_calls: list[tuple[str, str, str]] = []
        real_getaddrinfo = socket.getaddrinfo

        def fake_getaddrinfo(host: Any, port: Any, *a: Any, **k: Any) -> Any:
            name = host.decode() if isinstance(host, bytes) else host
            if isinstance(name, str) and name.endswith(SITE_HOST):
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", int(port or 80)))]
            if isinstance(name, str) and name in LOCAL_HOSTS:
                return real_getaddrinfo(host, port, *a, **k)
            raise NotLocalError(f"The website section refuses to look up {name!r}")

        def dispatch_learn(source_id: Any, kind: str, trigger: str) -> bool:
            self.learn_calls.append((str(source_id), kind, trigger))
            return True

        def dispatch_urls_service(source_id: Any, urls: Any, trigger: str = "manual") -> bool:
            self.dispatched.append((str(source_id), list(urls), "page", trigger))
            return True

        def dispatch_urls_push(source_id: Any, urls: Any, kind: str, trigger: str) -> bool:
            self.dispatched.append((str(source_id), list(urls), kind, trigger))
            return True

        stack = self.stack
        stack.enter_context(
            patch.dict(
                os.environ, {"RAG_ENABLE_CONTENT_PORTAL": "false", "NO_PROXY": "*", "no_proxy": "*"}
            )
        )
        stack.enter_context(patch.object(socket, "getaddrinfo", fake_getaddrinfo))
        stack.enter_context(
            patch(
                "callobuzz_rag.llm.litellm_provider.LiteLLMProvider.from_resolved_config",
                lambda *a, **k: llm,
            )
        )
        stack.enter_context(patch.object(service_mod, "dispatch_learn", dispatch_learn))
        stack.enter_context(patch.object(service_mod, "dispatch_urls", dispatch_urls_service))
        stack.enter_context(patch.object(push_mod, "dispatch_urls", dispatch_urls_push))
        stack.enter_context(
            patch("callobuzz_rag.website.learn.enqueue_email", lambda *a, **k: None)
        )
        self.push_mod = push_mod

        self.app = create_app()
        self.app.state.settings = self.settings
        self.app.state.session_factory = self.factory
        self.app.state.vector_store = self.vectors
        self.app.state.embedder = self.embedder
        self.app.state.context_assembler = ContextAssembler(
            self.settings, sanitizer=ContentSanitizer(self.settings)
        )
        self.limiter = AccountRateLimiter(redis_url)
        self.app.state.account_rate_limiter = self.limiter
        self.app.state.fair_use_redis = self.redis

        async def ingest(batch: list[Any], corpus_id: uuid.UUID, job_id: uuid.UUID) -> Any:
            from callobuzz_rag.accounts.caps import ensure_document_room_for_job

            async with self.factory() as session:
                collection = (
                    await session.execute(
                        text("SELECT qdrant_collection FROM corpora WHERE id = :c"),
                        {"c": corpus_id},
                    )
                ).scalar_one()
                orchestrator = IngestionOrchestrator(
                    session=session,
                    storage=None,  # type: ignore[arg-type]
                    parser=None,  # type: ignore[arg-type]
                    chunker=chunker_from_settings(self.settings),
                    enricher=ExtractiveEnrichmentProvider(),
                    embedder=self.embedder,
                    vector_store=self.vectors,
                    bucket="unused",
                    collection=collection,
                    settings=self.settings,
                    document_room=ensure_document_room_for_job,
                )
                outcome = await orchestrator.ingest_records(batch, corpus_id, job_id)
            return {"job_id": str(job_id), **asdict(outcome)}

        # The production wiring: every fetch spends the daily budget, new pages are counted.
        self.deps = build_deps(self.settings, self.factory, self.vectors, ingest)
        self.client = AsyncClient(ASGITransport(app=self.app), base_url="http://t")
        await self.client.__aenter__()
        self.hash_password = hash_password
        self.Tenant, self.TenantMember, self.User = Tenant, TenantMember, User

        owner = await self._owner("owner@shreedental.in", None)
        self.auth, self.kb_id, self.source_id, self.tenant_id, self.plan_id = owner

    async def _owner(
        self, email: str, limits: dict[str, Any] | None
    ) -> tuple[dict[str, str], str, Any, Any, Any]:
        """A business that signs in and sets up from the site address; optionally on a plan with limits."""
        from sqlalchemy import text

        async with self.factory() as session:
            default = (
                await session.execute(text("SELECT id FROM plans WHERE is_default"))
            ).scalar_one()
            plan_id = default
            if limits is not None:
                plan_id = uuid.uuid4()
                await session.execute(
                    text(
                        "INSERT INTO plans (id, name, fair_use_limits, fair_use_policy) VALUES "
                        "(:i, :n, CAST(:l AS jsonb), '{}'::jsonb)"
                    ),
                    {"i": plan_id, "n": f"limited-{plan_id.hex[:8]}", "l": json.dumps(limits)},
                )
            tenant = self.Tenant(name=f"Shree Dental Clinic, Pune ({email})", api_key_hash="")
            tenant.plan_id = plan_id
            session.add(tenant)
            await session.flush()
            user = self.User(
                email=email,
                name="Dr. Meera Shah",
                password_hash=await self.hash_password("correct horse battery"),
                email_verified_at=text("now()"),
            )
            session.add(user)
            await session.flush()
            session.add(self.TenantMember(tenant_id=tenant.id, user_id=user.id, role="owner"))
            await session.commit()
        login = await self.client.post(
            "/api/v1/auth/login", json={"email": email, "password": "correct horse battery"}
        )
        login.raise_for_status()
        auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
        started = await self.client.post(
            "/api/v1/onboarding/website",
            json={"website": self.base, "assistant_name": "Shree Dental Assistant"},
            headers=auth,
        )
        started.raise_for_status()
        kb_id = started.json()["kb_id"]
        async with self.factory() as session:
            source_id = (
                await session.execute(
                    text("SELECT id FROM website_sources WHERE corpus_id = :c"),
                    {"c": uuid.UUID(kb_id)},
                )
            ).scalar_one()
        return auth, kb_id, source_id, tenant.id, plan_id

    async def _rows(self, sql: str, **params: Any) -> list[Any]:
        from sqlalchemy import text

        async with self.factory() as session:
            return [tuple(r) for r in (await session.execute(text(sql), params)).all()]

    async def first_learn(self) -> LearnStats:
        from callobuzz_rag.website.learn import learn_source

        started = time.monotonic()
        result = await learn_source(
            self.source_id, kind="first", trigger="onboarding", deps=self.deps
        )
        seconds = time.monotonic() - started
        rows = await self._rows(
            "SELECT status, coalesce(skip_reason, '') FROM website_pages WHERE source_id = :s",
            s=self.source_id,
        )
        learned = sum(1 for st, _ in rows if st == "learned")
        skipped_by: dict[str, int] = {}
        for st, reason in rows:
            if st != "learned":
                key = reason or st
                skipped_by[key] = skipped_by.get(key, 0) + 1
        if result.status != "done":
            raise RuntimeError(f"The first read ended '{result.status}' ({result.reason})")
        return LearnStats(learned, len(rows) - learned, seconds, skipped_by)

    async def freshness(self, changed: int) -> CheckStats:
        from dataclasses import replace

        from callobuzz_rag.website.check import check_pages, schedule_checks

        self.site.change(changed)
        before = len(self.site.requests)
        t1 = datetime.now(UTC) + timedelta(days=2)
        due: list[tuple[str, list[str], str]] = []
        await schedule_checks(
            self.factory,
            self.settings,
            dispatch_check=lambda s, ids, trig: due.append((s, ids, trig)),
            dispatch_learn=lambda *a: None,
            now=t1,
        )
        started = time.monotonic()
        reread = checked = 0
        for source_id, ids, trigger in due:
            res = await check_pages(
                uuid.UUID(source_id),
                [uuid.UUID(i) for i in ids],
                trigger=trigger,
                deps=replace(self.deps, clock=lambda: t1),
            )
            checked += len(res.outcomes)
            reread += sum(1 for o in res.outcomes.values() if o != "unchanged")
        seconds = time.monotonic() - started
        made = self.site.requests[before:]
        return CheckStats(
            len(made), sum(1 for _, _, s in made if s == 304), reread, checked, seconds
        )

    async def push(self, count: int) -> float:
        from callobuzz_rag.website.learn import learn_urls

        made = await self.client.post(
            f"/api/v1/corpora/{self.kb_id}/website/learn-key", headers=self.auth
        )
        made.raise_for_status()
        key = made.json()["key"]
        numbers = self.site.change(count, start=self.sizes.site_changed)
        self.dispatched.clear()
        started = time.monotonic()
        for i in numbers:
            pushed = await self.client.post(
                f"/api/v1/public/learn/{key}", json={"url": f"{self.base}{self.site.page_path(i)}"}
            )
            if pushed.status_code != 202:
                raise RuntimeError(f"A push was refused: {pushed.status_code} {pushed.text}")
        for source_id, urls, kind, trigger in list(self.dispatched):
            await learn_urls(uuid.UUID(source_id), urls, kind=kind, trigger=trigger, deps=self.deps)
        return time.monotonic() - started

    async def sensor_hint(self) -> tuple[str, float]:
        from callobuzz_rag.db.models.corpus import Corpus
        from callobuzz_rag.website import sensor
        from callobuzz_rag.website.learn import learn_urls

        i = self.sizes.site_changed + self.sizes.site_pushed
        url = f"{self.base}{self.site.page_path(i)}"
        async with self.factory() as session:
            corpus = await session.get(Corpus, uuid.UUID(self.kb_id))
            await sensor.report(
                session,
                self.redis,
                self.settings,
                corpus,
                origin=self.base,
                page_url=url,
                fingerprint="a",
                now=datetime.now(UTC),
            )  # the first sighting is only stored
            self.site.set_page(i)
            self.dispatched.clear()
            started = time.monotonic()
            outcome = await sensor.report(
                session,
                self.redis,
                self.settings,
                corpus,
                origin=self.base,
                page_url=url,
                fingerprint="b",
                now=datetime.now(UTC),
            )
        for source_id, urls, kind, trigger in list(self.dispatched):
            await learn_urls(uuid.UUID(source_id), urls, kind=kind, trigger=trigger, deps=self.deps)
        return outcome, time.monotonic() - started

    async def limits(self) -> LimitStats:
        from dataclasses import replace
        from unittest.mock import patch

        from callobuzz_rag.fairuse import budget as fair_budget
        from callobuzz_rag.fairuse import counters
        from callobuzz_rag.website.fetch import WebsiteFetcher
        from callobuzz_rag.website.learn import learn_source
        from callobuzz_rag.website.resume import resume_limited

        s = self.sizes
        # A read that runs into the daily FETCH limit stops cleanly too.
        _, _, fetch_source, _, _ = await self._owner(
            "owner3@shreedental.in", {"fetches_per_day": s.site_day_fetches}
        )
        fetched = await learn_source(
            fetch_source, kind="first", trigger="onboarding", deps=self.deps
        )
        fetch_stopped = (
            str(fetched.counts.get("limited_by")) if fetched.counts.get("limited_by") else None
        )
        # A read that runs into the daily PAGE limit, then resumes the next day.
        _, _, source_id, _, _ = await self._owner(
            "owner2@shreedental.in", {"website_pages_per_day": s.site_day_pages}
        )
        result = await learn_source(source_id, kind="first", trigger="onboarding", deps=self.deps)
        first_pages = (
            await self._rows(
                "SELECT count(*) FROM website_pages WHERE source_id = :s AND status = 'learned'",
                s=source_id,
            )
        )[0][0]
        stopped_by = (
            str(result.counts.get("limited_by")) if result.counts.get("limited_by") else None
        )
        # The next day: the counters are another day's, the scheduler resumes the read.
        tomorrow = datetime.now(UTC) + timedelta(days=1)
        started: list[Any] = []

        def tomorrows_fetcher(source: Any) -> Any:
            budget = fair_budget.FetchBudget(
                self.factory, source.tenant_id, today=lambda: tomorrow.date()
            )
            return WebsiteFetcher(
                self.settings, origin=source.origin, scope=source.scope, budget=budget
            )

        with patch.object(counters, "utc_day", lambda now=None: tomorrow.date()):
            resumed = await resume_limited(
                self.factory, dispatch_learn=lambda *a: started.append(a), now=tomorrow
            )
            if started:
                await learn_source(
                    source_id,
                    kind="full",
                    trigger="schedule",
                    deps=replace(self.deps, fetcher=tomorrows_fetcher, clock=lambda: tomorrow),
                )
        total = (
            await self._rows(
                "SELECT count(*) FROM website_pages WHERE source_id = :s AND status = 'learned'",
                s=source_id,
            )
        )[0][0]
        return LimitStats(int(first_pages), stopped_by, bool(resumed), int(total), fetch_stopped)

    async def close(self) -> None:
        try:
            await self.client.__aexit__(None, None, None)
            await self.limiter.close()
        finally:
            self.stack.close()
            self.site.stop()
            await self.redis.aclose()
            await self.engine.dispose()


# -------------------------------------------------------------------------------- done-check


def _expand_braces(text: str) -> list[str]:
    match = re.search(r"{([^{}]*)}", text)
    if match is None:
        return [text]
    out: list[str] = []
    for option in match.group(1).split(","):
        out += _expand_braces(text[: match.start()] + option.strip() + text[match.end() :])
    return out


def _resolves(path: str) -> bool:
    """Does the path (or the glob) exist, as written or under the folders the epics abbreviate?"""
    bases = (
        "",
        "src/callobuzz_rag/",
        "services/dashboard/",
        "services/dashboard/src/",
        "tests/unit/",
        "tests/",
    )
    for base in bases:
        target = ROOT / (base + path)
        if "*" in path:
            if any(True for _ in ROOT.glob(base + path)):
                return True
        elif target.exists():
            return True
    return False


# Commit subjects that show an epic was built (the epics' commit messages use their area, not the id).
EPIC_COMMITS = {
    "BIZ1": r"feat\((website|sdk)\)|website learning|From your website",
    "BIZ2": r"freshness|push endpoint|refresh schedule|sensor",
    "BIZ3": r"simple (menu|business mode)|Knowledge page",
    "BIZ4": r"onboarding|avatars",
    "BIZ5": r"biz5|business layer smoke|business layer guide|website status card",
}


def business_done_check() -> str | None:
    """AUDIT section 6: every file the BIZ1 to BIZ5 epic file tables list exists, and each epic has
    commits in git log. docs/internal is private to the maintainers' machines, so this is skipped
    when it is not there."""
    folder = ROOT / "docs" / "internal" / "plans" / "epics" / "business"
    if not folder.exists():
        return "Skipped: docs/internal/plans/epics/business is not on this machine."
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=ROOT, capture_output=True, text=True, check=False
    ).stdout
    lines: list[str] = []
    for epic in ("BIZ1", "BIZ2", "BIZ3", "BIZ4", "BIZ5"):
        plan = next(iter(sorted(folder.glob(f"{epic}-*.md"))), None)
        if plan is None:
            lines.append(f"- {epic}: no plan file found")
            continue
        text = plan.read_text(encoding="utf-8")
        table = text.split("## Files", 1)[-1].split("## Tasks", 1)[0]
        named = sorted(
            {
                path
                for ref in re.findall(r"`([^`]+)`", table)
                for path in _expand_braces(ref)
                if re.search(r"\.[A-Za-z0-9]+$", path) and "/" in path and "**" not in path
            }
        )
        missing = [p for p in named if not _resolves(p)]
        built = bool(re.search(EPIC_COMMITS[epic], log, re.IGNORECASE))
        lines.append(
            f"- {epic}: {len(named)} files named, {len(named) - len(missing)} found, "
            f"{len(missing)} not found; commits in git log: {'yes' if built else 'NO'}"
        )
        lines += [f"  - not found: `{p}`" for p in missing[:30]]
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------ cli


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="every section on in-process fakes, tiny sizes, nothing written outside a temporary folder",
    )
    parser.add_argument("--label", default=datetime.now(UTC).strftime("%Y-%m-%d"))
    parser.add_argument(
        "--sections",
        default=",".join(SECTIONS),
        help=f"comma separated, from {', '.join(SECTIONS)}",
    )
    parser.add_argument("--quick", action="store_true", help="small sizes (a dry run always is)")
    parser.add_argument(
        "--out",
        default=None,
        help="results folder (default benchmarks/results; a temporary folder for --dry-run)",
    )
    parser.add_argument(
        "--engine-url", default=None, help="a running stack, for the local model section"
    )
    parser.add_argument(
        "--widget-key",
        default=None,
        help="a publishable key (pk_...) of a business on the Free profile",
    )
    parser.add_argument("--origin", default=None, help="an allowed origin of that key")
    parser.add_argument(
        "--questions-file",
        default=None,
        help="JSON {language: [questions]} instead of the built-in 20",
    )
    parser.add_argument(
        "--skip-integration", action="store_true", help="do not re-run the integration tests"
    )
    args = parser.parse_args()
    wanted = [s.strip() for s in args.sections.split(",") if s.strip()]
    bad = [s for s in wanted if s not in SECTIONS]
    if bad:
        print(f"Unknown section(s): {', '.join(bad)}", file=sys.stderr)
        return EXIT_USAGE
    sizes = Sizes.quick() if (args.dry_run or args.quick) else Sizes()
    out_dir = (
        Path(args.out)
        if args.out
        else (Path(tempfile.mkdtemp(prefix="fair-use-bench-")) if args.dry_run else RESULTS)
    )
    if args.dry_run:
        sections = asyncio.run(run_sections(DryEnv(), sizes, wanted, args))
    else:
        sections = real_main(args, sizes, wanted)
    integration = run_integration(args.skip_integration or args.dry_run, args.dry_run)
    done = business_done_check() if not args.dry_run else None
    report = build_report(
        sections, label=args.label, dry=args.dry_run, integration=integration, done_check=done
    )
    js, md = write_report(report, out_dir)
    print(f"\nWrote {js}\n      {md}")
    failed = [s.key for s in sections if s.ok is False]
    failed_tests = [
        p for p, r in (integration.get("tests") or {}).items() if r.get("status") == "failed"
    ]
    if failed or failed_tests:
        print(f"Needs attention: {', '.join(failed + failed_tests)}")
        return EXIT_FAILED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
