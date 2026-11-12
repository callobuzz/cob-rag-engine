"""First-run onboarding (BIZ4): the routes on the in-memory database, with a scripted engine.

`FakeEngine` stands in for the in-process client: it answers the knowledge-base, assistant,
widget-key and website routes the service calls, keeping its own state, and records every call.
The routes it replaces are tested in their own files; here the point is what onboarding does
with them: the order, what it reuses, what it never repeats, and how a failure resumes.
"""

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from callobuzz_rag.cob.tools.http import InternalResponse
from callobuzz_rag.db.models.audit_log import AuditLog
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.onboarding import OnboardingState, WidgetOriginSeen
from callobuzz_rag.db.models.website import WebsiteSource
from callobuzz_rag.db.models.widget_key import WidgetKey
from callobuzz_rag.onboarding import service
from callobuzz_rag.website import netguard
from tests.unit.accounts_fakes import FakeSession
from tests.unit.test_auth_routes import ADMIN, PASSWORD, World, login, req
from tests.unit.test_website_api import with_app

pytestmark = pytest.mark.usefixtures("unlimited_plan")

START: dict[str, Any] = {
    "website": "shreedental.in",
    "assistant_name": "Shree Dental Assistant",
    "business_type": "clinic",
    "whatsapp": "+91 98220 12345",
    "hours": [
        {"day": d, "open": "10:00", "close": "19:00"}
        for d in ("mon", "tue", "wed", "thu", "fri", "sat")
    ],
}
SITE = "https://shreedental.in"
WWW = "https://www.shreedental.in"


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    async def public(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr(netguard, "resolve_host", public)


def merge(target: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """RFC 7396, enough for these tests."""
    out = dict(target)
    for key, value in patch.items():
        if value is None:
            out.pop(key, None)
        elif isinstance(value, dict):
            out[key] = merge(out.get(key) or {}, value)
        else:
            out[key] = value
    return out


class FakeEngine:
    """The engine's routes as onboarding sees them."""

    def __init__(self, world: World, tenant: Any) -> None:
        self.world, self.tenant = world, tenant
        self.calls: list[tuple[str, str, Any]] = []
        self.fail: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}
        self.published: dict[uuid.UUID, dict[str, Any]] = {}
        self.drafts: dict[uuid.UUID, dict[str, Any]] = {}
        self.publishes = 0
        self.default_template_on_create = True

    # -- recording helpers
    def paths(self, method: str) -> list[str]:
        return [p for m, p, _ in self.calls if m == method]

    def count(self, method: str, pattern: str) -> int:
        return sum(1 for m, p, _ in self.calls if m == method and re.search(pattern, p))

    async def request(
        self, method: str, path: str, *, params: Any = None, json: Any = None
    ) -> InternalResponse:
        self.calls.append((method, path, json))
        for (m, pattern), (status, body) in self.fail.items():
            if m == method and re.search(pattern, path):
                return InternalResponse(status=status, body=body)
        handler = self._route(method, path)
        status, body = handler(json)
        return InternalResponse(status=status, body=body)

    def _route(self, method: str, path: str) -> Any:
        if (method, path) == ("POST", "/api/v1/corpora"):
            return self._create_kb
        m = re.match(r"^/api/v1/corpora/([^/]+)/(.*)$", path)
        assert m, f"unscripted call {method} {path}"
        kb = uuid.UUID(m.group(1))
        tail = m.group(2)
        table: dict[tuple[str, str], Any] = {
            ("GET", "assistant"): lambda _b: self._assistant(kb),
            ("POST", "assistant/draft/from-template"): lambda b: self._template(kb, b),
            ("PATCH", "assistant/draft"): lambda b: self._patch(kb, b),
            ("POST", "assistant/publish"): lambda b: self._publish(kb, b),
            ("GET", "widget-keys"): lambda _b: (200, self._keys(kb)),
            ("POST", "widget-keys"): lambda b: self._create_key(kb, b),
            ("GET", "website"): lambda _b: self._website(kb),
            ("POST", "website/learn"): lambda b: self._learn(kb, b),
        }
        if (method, tail) in table:
            return table[(method, tail)]
        k = re.match(r"^widget-keys/([^/]+)$", tail)
        if method == "PATCH" and k:
            return lambda b: self._patch_key(kb, k.group(1), b)
        raise AssertionError(f"unscripted call {method} {path}")

    # -- knowledge base
    def _create_kb(self, body: dict[str, Any]) -> tuple[int, Any]:
        corpus = self.world.db.add(
            Corpus(
                name=body["name"],
                tenant_id=self.tenant.id,
                qdrant_collection=f"corpus_{uuid.uuid4().hex[:8]}",
                api_key_hash="",
                is_system=False,
                created_at=datetime.now(UTC),
            )
        )
        if self.default_template_on_create:
            self.published[corpus.id] = {
                "version": 1,
                "config": {"identity": {"business_type": "generic_support"}},
                "draft_revision": 0,
            }
        return 201, {"id": str(corpus.id), "name": corpus.name}

    # -- assistant
    def _assistant(self, kb: uuid.UUID) -> tuple[int, Any]:
        return 200, {
            "published": self.published.get(kb),
            "draft": self.drafts.get(kb),
        }

    def _template(self, kb: uuid.UUID, body: dict[str, Any]) -> tuple[int, Any]:
        base = self.drafts.get(kb) or {"version": None, "config": {}, "draft_revision": 0}
        base = {
            **base,
            "config": merge(base["config"], {"identity": {"business_type": body["template"]}}),
            "draft_revision": base["draft_revision"] + 1,
        }
        self.drafts[kb] = base
        return 200, base

    def _patch(self, kb: uuid.UUID, body: dict[str, Any]) -> tuple[int, Any]:
        base = self.drafts.get(kb) or {
            "version": None,
            "config": dict((self.published.get(kb) or {}).get("config") or {}),
            "draft_revision": 0,
        }
        base = {
            **base,
            "config": merge(base["config"], body["patch"]),
            "draft_revision": base["draft_revision"] + 1,
        }
        self.drafts[kb] = base
        return 200, base

    def _publish(self, kb: uuid.UUID, body: dict[str, Any]) -> tuple[int, Any]:
        draft = self.drafts.pop(kb)
        assert body["base_revision"] == draft["draft_revision"]
        self.publishes += 1
        version = int((self.published.get(kb) or {}).get("version") or 0) + 1
        self.published[kb] = {"version": version, "config": draft["config"], "draft_revision": 0}
        return 200, self.published[kb]

    # -- widget keys
    def _keys(self, kb: uuid.UUID) -> list[dict[str, Any]]:
        return [self._key_out(k) for k in self.world.db.all(WidgetKey) if k.corpus_id == kb]

    @staticmethod
    def _key_out(key: WidgetKey) -> dict[str, Any]:
        return {
            "id": str(key.id),
            "public_key": key.public_key,
            "allowed_origins": list(key.allowed_origins),
            "is_active": key.is_active,
            "embed_snippet": f'<script src="https://rag.example.com/widget.js" '
            f'data-key="{key.public_key}" async></script>',
        }

    def _create_key(self, kb: uuid.UUID, body: dict[str, Any]) -> tuple[int, Any]:
        key = self.world.db.add(
            WidgetKey(
                corpus_id=kb,
                name=body["name"],
                public_key=f"pk_{uuid.uuid4().hex[:12]}",
                allowed_origins=body["allowed_origins"],
                is_active=True,
            )
        )
        self._learn_from(kb, key.allowed_origins)
        return 201, self._key_out(key)

    def _patch_key(self, kb: uuid.UUID, key_id: str, body: dict[str, Any]) -> tuple[int, Any]:
        key = next(k for k in self.world.db.all(WidgetKey) if str(k.id) == key_id)
        key.allowed_origins = body["allowed_origins"]
        self._learn_from(kb, key.allowed_origins)
        return 200, self._key_out(key)

    # -- website learning (the widget-key hook makes the source; a manual start does too)
    def _learn_from(self, kb: uuid.UUID, origins: list[str]) -> None:
        if any(s.corpus_id == kb for s in self.world.db.all(WebsiteSource)):
            return
        self.world.db.add(
            WebsiteSource(
                corpus_id=kb,
                tenant_id=self.tenant.id,
                origin=origins[0],
                scope="shreedental.in",
                status="discovering",
            )
        )

    def _website(self, kb: uuid.UUID) -> tuple[int, Any]:
        sources = [s for s in self.world.db.all(WebsiteSource) if s.corpus_id == kb]
        keys = [k for k in self.world.db.all(WidgetKey) if k.corpus_id == kb]
        unlearned = [] if sources else [o for k in keys for o in k.allowed_origins][:1]
        return 200, {
            "sources": [{"scope": s.scope, "origin": s.origin} for s in sources],
            "unlearned_origins": unlearned,
        }

    def _learn(self, kb: uuid.UUID, body: dict[str, Any]) -> tuple[int, Any]:
        self._learn_from(kb, [body["origin"]])
        return 202, {"origin": body["origin"]}


def setup(world: World | None = None) -> tuple[World, FakeEngine, Any]:
    world = world or World()
    engine = FakeEngine(world, world.shree)
    owner = world.user("asha@shree.example")
    world.member(owner, world.shree, "owner")
    member = world.user("ravi@shree.example")
    world.member(member, world.shree, "member")
    return world, engine, owner


@pytest.fixture
def engine_for(monkeypatch: pytest.MonkeyPatch):
    def install(engine: FakeEngine) -> None:
        monkeypatch.setattr(service, "make_client", lambda _request: engine)

    return install


async def token(app: Any, email: str = "asha@shree.example") -> str:
    return str((await login(app, email, PASSWORD)).json()["access_token"])


async def post(app: Any, tok: str, body: dict[str, Any] | None = None) -> Any:
    return await req(app, "POST", "/api/v1/onboarding/website", token=tok, json=body or START)


async def get(app: Any, tok: str) -> Any:
    return await req(app, "GET", "/api/v1/onboarding", token=tok)


def states(world: World) -> list[OnboardingState]:
    return world.db.all(OnboardingState)


# ---------------------------------------------------------------- start


async def test_start_sets_everything_up_once(engine_for) -> None:
    world, engine, _ = setup()
    engine_for(engine)
    with with_app(world) as app:
        tok = await token(app)
        res = await post(app, tok)
        assert res.status_code == 200, res.text
        s = res.json()
        again = (await post(app, tok)).json()
    assert s["website"] == SITE and s["step"] == 2 and s["status"] == "in_progress"
    assert s["snippet"].startswith("<script src=") and 'data-key="pk_' in s["snippet"]
    kb = uuid.UUID(s["kb_id"])
    identity = engine.published[kb]["config"]["identity"]
    assert identity["assistant_name"] == "Shree Dental Assistant"
    assert identity["business_name"] == "Shree Labs"  # the workspace's name
    assert identity["business_type"] == "clinic"
    assert identity["card"]["whatsapp"] == "+919822012345"
    assert identity["card"]["website"] == SITE
    assert len(identity["card"]["hours"]) == 6
    (key,) = world.db.all(WidgetKey)
    assert key.allowed_origins == [SITE, WWW]
    assert (again["kb_id"], again["widget_key_id"]) == (s["kb_id"], s["widget_key_id"])
    assert len(world.db.all(Corpus)) == 1 and len(world.db.all(WidgetKey)) == 1
    assert engine.publishes == 1  # the repeat found the assistant already saying it
    assert engine.count("POST", "/website/learn") == 0  # the key hook already started learning
    started = world.audits("onboarding.website_started")
    assert len(started) == 1
    assert started[0].details == {
        "kb_id": s["kb_id"],
        "widget_key_id": s["widget_key_id"],
        "origin": SITE,
    }


async def test_the_default_name_is_the_workspace_name_plus_assistant(engine_for) -> None:
    world, engine, _ = setup()
    engine_for(engine)
    body = {"website": "https://www.shreedental.in/contact"}
    with with_app(world) as app:
        res = await post(app, await token(app), body)
    assert res.status_code == 200, res.text
    kb = uuid.UUID(res.json()["kb_id"])
    assert engine.published[kb]["config"]["identity"]["assistant_name"] == "Shree Labs Assistant"
    assert res.json()["website"] == WWW
    (key,) = world.db.all(WidgetKey)
    assert key.allowed_origins == [WWW, SITE]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Shree Dental Clinic", "Shree Dental Clinic Assistant"),
        ("Shree Dental and Maternity Hospital, Pune", "Shree Dental and Maternity Assistant"),
        ("A" * 60, "A" * 30 + " Assistant"),
        ("  ", "Assistant"),
    ],
)
def test_default_name_is_cut_on_a_word_and_fits_forty(name: str, expected: str) -> None:
    out = service.default_assistant_name(name)
    assert out == expected and len(out) <= 40


@pytest.mark.parametrize(
    ("typed", "origin"),
    [
        ("shreedental.in", SITE),
        ("www.shreedental.in", WWW),
        ("https://shreedental.in/any/path?x=1", SITE),
        ("http://shreedental.in", "http://shreedental.in"),
        ("SHREEDENTAL.IN", SITE),
    ],
)
def test_websites_are_typed_as_domains(typed: str, origin: str) -> None:
    assert service.normalize_website(typed) == origin


def test_the_site_and_its_twin() -> None:
    assert service.allowed_origins_for(SITE) == [SITE, WWW]
    assert service.allowed_origins_for(WWW) == [WWW, SITE]
    assert service.allowed_origins_for("http://shreedental.in:8080") == [
        "http://shreedental.in:8080",
        "http://www.shreedental.in:8080",
    ]


async def test_a_failing_step_keeps_what_succeeded_and_resumes(engine_for) -> None:
    world, engine, _ = setup()
    engine_for(engine)
    engine.fail[("POST", "/widget-keys$")] = (
        409,
        {
            "detail": "Widget key limit reached (0)",
            "code": "widget_key_limit_reached",
            "limit": 0,
        },
    )
    with with_app(world) as app:
        tok = await token(app)
        res = await post(app, tok)
        assert res.status_code == 409 and res.json()["code"] == "widget_key_limit_reached"
        assert res.json()["detail"] == "Widget key limit reached (0)" and res.json()["limit"] == 0
        state = (await get(app, tok)).json()
        assert state["kb_id"] is not None and state["widget_key_id"] is None
        assert state["status"] == "in_progress" and state["step"] == 1
        assert world.audits("onboarding.website_started") == []
        del engine.fail[("POST", "/widget-keys$")]
        done = (await post(app, tok)).json()
    assert done["kb_id"] == state["kb_id"] and done["widget_key_id"] is not None
    assert len(world.db.all(Corpus)) == 1
    assert engine.count("POST", r"^/api/v1/corpora$") == 1
    assert engine.publishes == 1
    assert len(world.audits("onboarding.website_started")) == 1


async def test_a_step_that_answers_without_a_sentence_becomes_a_plain_retry(engine_for) -> None:
    world, engine, _ = setup()
    engine_for(engine)
    engine.fail[("POST", r"^/api/v1/corpora$")] = (500, {})
    with with_app(world) as app:
        res = await post(app, await token(app))
    assert res.status_code == 502
    assert res.json()["code"] == "onboarding_failed"
    assert "please try again" in res.json()["detail"]
    assert world.db.all(Corpus) == []


async def test_existing_kb_and_assistant_are_reused_not_overwritten(engine_for) -> None:
    world, engine, _ = setup()
    engine_for(engine)
    corpus = world.db.add(
        Corpus(
            name="Main",
            tenant_id=world.shree.id,
            qdrant_collection="corpus_main",
            api_key_hash="",
            is_system=False,
            created_at=datetime.now(UTC) - timedelta(days=30),
        )
    )
    newer = world.db.add(
        Corpus(
            name="Newer",
            tenant_id=world.shree.id,
            qdrant_collection="corpus_newer",
            api_key_hash="",
            is_system=False,
            created_at=datetime.now(UTC),
        )
    )
    engine.published[corpus.id] = {
        "version": 4,
        "config": {
            "identity": {"business_type": "store", "assistant_name": "Old"},
            "leads": {"enabled": True},
            "language": {"primary": "hi-Latn"},
        },
        "draft_revision": 0,
    }
    with with_app(world) as app:
        res = await post(app, await token(app), {**START, "business_type": "clinic"})
    assert res.json()["kb_id"] == str(corpus.id) != str(newer.id)
    assert len(world.db.all(Corpus)) == 2  # nothing created
    config = engine.published[corpus.id]["config"]
    assert config["identity"]["assistant_name"] == "Shree Dental Assistant"
    assert config["identity"]["business_type"] == "store"  # a configured assistant keeps its type
    assert config["leads"] == {"enabled": True} and config["language"] == {"primary": "hi-Latn"}
    assert engine.count("POST", "from-template") == 0
    assert engine.publishes == 1


async def test_an_untouched_first_version_gets_the_chosen_business_type(engine_for) -> None:
    world, engine, _ = setup()
    engine_for(engine)
    with with_app(world) as app:
        res = await post(app, await token(app), {**START, "business_type": "restaurant"})
    kb = uuid.UUID(res.json()["kb_id"])
    assert engine.count("POST", "from-template") == 1
    assert engine.published[kb]["config"]["identity"]["business_type"] == "restaurant"


async def test_a_workspace_without_a_default_assistant_gets_one_from_a_template(
    engine_for,
) -> None:
    world, engine, _ = setup()
    engine.default_template_on_create = False
    engine_for(engine)
    with with_app(world) as app:
        res = await post(app, await token(app), {"website": "shreedental.in"})
    kb = uuid.UUID(res.json()["kb_id"])
    assert engine.count("POST", "from-template") == 1
    assert engine.published[kb]["config"]["identity"]["business_type"] == "generic_support"


async def test_existing_key_that_allows_the_site_is_reused_and_gets_the_twin(engine_for) -> None:
    world, engine, _ = setup()
    engine_for(engine)
    corpus = world.db.add(
        Corpus(
            name="Main",
            tenant_id=world.shree.id,
            qdrant_collection="corpus_main",
            api_key_hash="",
            is_system=False,
            created_at=datetime.now(UTC),
        )
    )
    key = world.db.add(
        WidgetKey(
            corpus_id=corpus.id,
            name="Old site",
            public_key="pk_existing",
            allowed_origins=[SITE],
            is_active=True,
        )
    )
    world.db.add(
        WebsiteSource(
            corpus_id=corpus.id,
            tenant_id=world.shree.id,
            origin=SITE,
            scope="shreedental.in",
            status="ready",
        )
    )
    with with_app(world) as app:
        res = await post(app, await token(app))
    assert res.json()["widget_key_id"] == str(key.id)
    assert key.allowed_origins == [SITE, WWW]
    assert len(world.db.all(WidgetKey)) == 1
    assert engine.count("POST", "/widget-keys") == 0


async def test_an_existing_key_for_another_site_gets_the_origins_added(engine_for) -> None:
    world, engine, _ = setup()
    engine_for(engine)
    corpus = world.db.add(
        Corpus(
            name="Main",
            tenant_id=world.shree.id,
            qdrant_collection="corpus_main",
            api_key_hash="",
            is_system=False,
            created_at=datetime.now(UTC),
        )
    )
    key = world.db.add(
        WidgetKey(
            corpus_id=corpus.id,
            name="Other",
            public_key="pk_other",
            allowed_origins=["https://shreedental.example"],
            is_active=True,
        )
    )
    with with_app(world) as app:
        res = await post(app, await token(app))
    assert res.json()["widget_key_id"] == str(key.id)
    assert key.allowed_origins == ["https://shreedental.example", SITE, WWW]


async def test_a_key_that_existed_but_never_learned_starts_learning(engine_for) -> None:
    world, engine, _ = setup()
    engine_for(engine)
    corpus = world.db.add(
        Corpus(
            name="Main",
            tenant_id=world.shree.id,
            qdrant_collection="corpus_main",
            api_key_hash="",
            is_system=False,
            created_at=datetime.now(UTC),
        )
    )
    world.db.add(
        WidgetKey(
            corpus_id=corpus.id,
            name="Site",
            public_key="pk_site",
            allowed_origins=[SITE, WWW],
            is_active=True,
        )
    )
    with with_app(world) as app:
        res = await post(app, await token(app))
    assert res.status_code == 200
    assert engine.count("POST", "/website/learn") == 1


@pytest.mark.parametrize(
    ("website", "code"),
    [
        ("not a url", "website_url_invalid"),
        ("localhost", "website_url_invalid"),
        ("192.168.1.1", "website_url_invalid"),
        ("https://", "website_url_invalid"),
    ],
)
async def test_a_bad_website_is_refused_before_anything_is_created(
    engine_for, website: str, code: str
) -> None:
    world, engine, _ = setup()
    engine_for(engine)
    with with_app(world) as app:
        res = await post(app, await token(app), {"website": website})
    assert (res.status_code, res.json()["code"]) == (422, code), res.text
    assert world.db.all(Corpus) == [] and engine.calls == [] and states(world) == []


async def test_a_private_address_is_refused_before_anything_is_created(
    engine_for, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def private(host: str, port: int) -> list[str]:
        return ["10.0.0.5"]

    monkeypatch.setattr(netguard, "resolve_host", private)
    world, engine, _ = setup()
    engine_for(engine)
    with with_app(world) as app:
        res = await post(app, await token(app))
    assert (res.status_code, res.json()["code"]) == (422, "website_blocked")
    assert engine.calls == [] and world.db.all(Corpus) == []


@pytest.mark.parametrize(
    "body",
    [
        {**START, "whatsapp": "12"},
        {**START, "assistant_name": "x" * 41},
        {**START, "avatar_url": "javascript:alert(1)"},
        {**START, "business_type": "spaceship"},
        {**START, "surprise": 1},
        {**START, "hours": [{"day": "mon", "open": "10:00", "close": "10:00"}]},
    ],
)
async def test_bodies_are_strict(engine_for, body: dict[str, Any]) -> None:
    world, engine, _ = setup()
    engine_for(engine)
    with with_app(world) as app:
        res = await post(app, await token(app), body)
    assert res.status_code == 422 and engine.calls == []


async def test_the_avatar_and_a_typed_avatar_path_go_into_the_identity(engine_for) -> None:
    world, engine, _ = setup()
    engine_for(engine)
    path = f"/api/v1/public/avatars/{uuid.uuid4()}.png"
    with with_app(world) as app:
        res = await post(app, await token(app), {**START, "avatar_url": path})
    kb = uuid.UUID(res.json()["kb_id"])
    assert engine.published[kb]["config"]["identity"]["avatar_url"] == path


async def test_member_and_admin_are_refused(engine_for) -> None:
    world, engine, _ = setup()
    engine_for(engine)
    with with_app(world) as app:
        member = await post(app, await token(app, "ravi@shree.example"))
        admin = await req(app, "POST", "/api/v1/onboarding/website", key=ADMIN, json=START)
        admin_get = await req(app, "GET", "/api/v1/onboarding", key=ADMIN)
    assert (member.status_code, member.json()["code"]) == (403, "owner_required")
    assert (admin.status_code, admin.json()["code"]) == (403, "no_workspace")
    assert (admin_get.status_code, admin_get.json()["code"]) == (403, "no_workspace")
    assert engine.calls == []


async def test_the_inner_calls_carry_the_callers_own_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    class Recorder:
        def __init__(self, app: Any, authorization: str | None, **kw: Any) -> None:
            seen.update(authorization=authorization, headers=kw.get("headers"))

        async def request(self, *a: Any, **k: Any) -> InternalResponse:
            return InternalResponse(status=500, body={})

    monkeypatch.setattr("callobuzz_rag.cob.tools.http.InternalClient", Recorder)
    world, _engine, _ = setup()
    with with_app(world) as app:
        tok = await token(app)
        await post(app, tok)
    assert seen["authorization"] == f"Bearer {tok}" and seen["headers"] == {}


async def test_concurrent_starts_use_one_state_row(engine_for) -> None:
    """The row is created once; a second start finds it (the race itself is on PostgreSQL in
    tests/integration)."""
    world, engine, _ = setup()
    engine_for(engine)
    with with_app(world) as app:
        tok = await token(app)
        await post(app, tok)
        await post(app, tok)
    assert len(states(world)) == 1


# ---------------------------------------------------------------- get / patch


async def test_a_workspace_that_never_started_is_new(engine_for) -> None:
    world, engine, _ = setup()
    engine_for(engine)
    with with_app(world) as app:
        res = await get(app, await token(app))
    assert res.json() == {
        "step": 1,
        "status": "new",
        "website": None,
        "kb_id": None,
        "widget_key_id": None,
        "snippet": None,
        "connection": {"status": "waiting", "origin": None, "first_seen_at": None, "refused": []},
        "learning": None,
        "files": {"count": 0, "learned": 0},  # TV5 brochure
    }


async def test_get_reports_connection_and_learning(engine_for) -> None:
    world, engine, _ = setup()
    engine_for(engine)
    with with_app(world) as app:
        tok = await token(app)
        started = (await post(app, tok)).json()
        waiting = (await get(app, tok)).json()
        key_id = uuid.UUID(started["widget_key_id"])
        (key,) = world.db.all(WidgetKey)
        key.allowed_origins = [SITE]  # the owner typed one spelling only
        world.db.add(
            WidgetOriginSeen(
                widget_key_id=key_id,
                origin=WWW,
                corpus_id=uuid.UUID(started["kb_id"]),
                allowed=False,
                first_seen_at=datetime(2026, 10, 5, 9, 0, tzinfo=UTC),
                last_seen_at=datetime(2026, 10, 5, 9, 5, tzinfo=UTC),
            )
        )
        refused = (await get(app, tok)).json()
        world.db.add(
            WidgetOriginSeen(
                widget_key_id=key_id,
                origin=SITE,
                corpus_id=uuid.UUID(started["kb_id"]),
                allowed=True,
                first_seen_at=datetime(2026, 10, 5, 9, 10, tzinfo=UTC),
                last_seen_at=datetime(2026, 10, 5, 9, 10, tzinfo=UTC),
            )
        )
        (source,) = world.db.all(WebsiteSource)
        source.status, source.pages_learned, source.pages_found = "learning", 23, 60
        connected = (await get(app, tok)).json()
        member = (await get(app, await token(app, "ravi@shree.example"))).json()
    assert waiting["connection"]["status"] == "waiting"
    assert waiting["learning"] == {
        "status": "learning",
        "pages_learned": 0,
        "pages_found": 0,
        "first_pass_done": False,
    }
    assert refused["connection"]["status"] == "waiting"
    assert [r["origin"] for r in refused["connection"]["refused"]] == [WWW]
    assert connected["connection"]["status"] == "connected"
    assert connected["connection"]["origin"] == SITE
    assert connected["learning"] == {
        "status": "learning",
        "pages_learned": 23,
        "pages_found": 60,
        "first_pass_done": False,
    }
    assert member["status"] == "in_progress"  # members may look, never change


async def test_patch_moves_steps_and_finishes(engine_for) -> None:
    world, engine, _ = setup()
    engine_for(engine)
    with with_app(world) as app:
        tok = await token(app)
        await post(app, tok)
        step3 = await req(app, "PATCH", "/api/v1/onboarding", token=tok, json={"step": 3})
        done = await req(app, "PATCH", "/api/v1/onboarding", token=tok, json={"status": "done"})
        again = await post(app, tok)  # re-opened from Home
    assert step3.json()["step"] == 3 and step3.json()["status"] == "in_progress"
    assert done.json()["status"] == "done"
    assert states(world)[0].completed_at is None  # re-opened
    assert again.json()["status"] == "in_progress"
    assert len(world.audits("onboarding.website_started")) == 1


async def test_a_workspace_can_skip_before_it_starts(engine_for) -> None:
    world, engine, _ = setup()
    engine_for(engine)
    with with_app(world) as app:
        tok = await token(app)
        skipped = await req(
            app, "PATCH", "/api/v1/onboarding", token=tok, json={"status": "dismissed"}
        )
        member = await req(
            app,
            "PATCH",
            "/api/v1/onboarding",
            token=await token(app, "ravi@shree.example"),
            json={"status": "dismissed"},
        )
    assert skipped.json()["status"] == "dismissed"
    assert states(world)[0].completed_at is not None
    assert (member.status_code, member.json()["code"]) == (403, "owner_required")


@pytest.mark.parametrize("body", [{}, {"step": 5}, {"step": 0}, {"status": "new"}, {"x": 1}])
async def test_patch_bodies_are_strict(engine_for, body: dict[str, Any]) -> None:
    world, engine, _ = setup()
    engine_for(engine)
    with with_app(world) as app:
        res = await req(app, "PATCH", "/api/v1/onboarding", token=await token(app), json=body)
    assert res.status_code == 422 or (body == {} and res.status_code == 200)


async def test_another_workspaces_state_is_never_shown(engine_for) -> None:
    world, engine, _ = setup()
    engine_for(engine)
    other = world.user("meera@pune.example")
    world.member(other, world.pune, "owner")
    with with_app(world) as app:
        await post(app, await token(app))
        theirs = (await get(app, await token(app, "meera@pune.example"))).json()
    assert theirs["status"] == "new" and theirs["kb_id"] is None


def audit_rows(world: World) -> list[AuditLog]:
    return world.db.all(AuditLog)


# ---------------------------------------------------------------- Home


async def test_home_says_where_the_first_run_steps_are_and_whether_the_site_is_connected(
    engine_for, monkeypatch: pytest.MonkeyPatch
) -> None:
    from callobuzz_rag.api.schemas.home import (
        HomeAssistant,
        HomeOut,
        HomeToday,
        HomeWebsite,
    )
    from callobuzz_rag.business import home as business_home

    async def base(_session: Any, _settings: Any, corpus: Any, **_kw: Any) -> HomeOut:
        return HomeOut(
            kb_id=corpus.id if corpus else None,
            kb_name=corpus.name if corpus else None,
            assistant=HomeAssistant(
                enabled=True, published=True, name="A", avatar_url=None, business_name="B"
            ),
            website=HomeWebsite(sites=[], learning=True),
            today=HomeToday(
                date=datetime(2026, 10, 5).date(),
                timezone="Asia/Kolkata",
                conversations=0,
                leads=0,
                handoffs_open=0,
            ),
        )

    monkeypatch.setattr(business_home, "_build_home", base)
    world, engine, _ = setup()
    engine_for(engine)
    with with_app(world) as app:
        tok = await token(app)
        before = await req(app, "GET", "/api/v1/home", token=tok)
        started = (await post(app, tok)).json()
        waiting = await req(app, "GET", "/api/v1/home", token=tok)
        world.db.add(
            WidgetOriginSeen(
                widget_key_id=uuid.UUID(started["widget_key_id"]),
                origin=SITE,
                corpus_id=uuid.UUID(started["kb_id"]),
                allowed=True,
                first_seen_at=datetime(2026, 10, 5, 9, 10, tzinfo=UTC),
                last_seen_at=datetime(2026, 10, 5, 9, 10, tzinfo=UTC),
            )
        )
        connected = await req(app, "GET", "/api/v1/home", token=tok)
    assert before.json()["onboarding"] == {"status": "new", "step": 1}
    assert before.json()["website"]["connected"] is False
    assert waiting.json()["onboarding"] == {"status": "in_progress", "step": 2}
    assert waiting.json()["website"]["connected"] is False
    assert connected.json()["website"]["connected"] is True


async def _started(world: World, engine: Any, app: Any) -> tuple[str, dict[str, Any], Any]:
    tok = await token(app)
    started = (await post(app, tok)).json()
    (key,) = world.db.all(WidgetKey)
    return tok, started, key


def _seen(world: World, started: dict[str, Any], origin: str, allowed: bool) -> None:
    world.db.add(
        WidgetOriginSeen(
            widget_key_id=uuid.UUID(started["widget_key_id"]),
            origin=origin,
            corpus_id=uuid.UUID(started["kb_id"]),
            allowed=allowed,
            first_seen_at=datetime(2026, 10, 5, 9, 0, tzinfo=UTC),
            last_seen_at=datetime(2026, 10, 5, 9, 5, tzinfo=UTC),
        )
    )


async def test_a_refused_site_that_is_no_twin_is_never_offered(engine_for) -> None:
    world, engine, _ = setup()
    engine_for(engine)
    with with_app(world) as app:
        tok, started, key = await _started(world, engine, app)
        key.allowed_origins = [SITE]
        _seen(world, started, "https://evil.example", False)
        _seen(world, started, WWW, False)
        seen = (await get(app, tok)).json()["connection"]
    assert [r["origin"] for r in seen["refused"]] == [WWW]


async def test_after_the_key_allows_the_twin_the_refused_list_is_empty_and_it_is_connected(
    engine_for,
) -> None:
    world, engine, _ = setup()
    engine_for(engine)
    with with_app(world) as app:
        tok, started, key = await _started(world, engine, app)
        key.allowed_origins = [SITE]
        _seen(world, started, WWW, False)
        before = (await get(app, tok)).json()["connection"]
        key.allowed_origins = [SITE, WWW]  # the owner pressed Allow
        row = next(r for r in world.db.all(WidgetOriginSeen) if r.origin == WWW)
        row.allowed = True  # the next load, within the hour, flips the row
        after = (await get(app, tok)).json()["connection"]
    assert before["status"] == "waiting" and len(before["refused"]) == 1
    assert (after["status"], after["origin"], after["refused"]) == ("connected", WWW, [])


async def test_a_load_only_a_wildcard_let_in_is_not_a_connection(engine_for) -> None:
    world, engine, _ = setup()
    engine_for(engine)
    with with_app(world) as app:
        tok, started, key = await _started(world, engine, app)
        key.allowed_origins = ["*"]
        _seen(world, started, "https://random.example", True)
        light = (await get(app, tok)).json()["connection"]
        home_connected = await service.site_connected(
            FakeSession(world.db), uuid.UUID(started["kb_id"])
        )
    assert light["status"] == "waiting"
    assert home_connected is False


async def test_a_wildcard_key_gets_the_literal_sites_and_learns(engine_for) -> None:
    world, engine, _ = setup()
    engine_for(engine)
    corpus = world.db.add(
        Corpus(
            name="Main",
            tenant_id=world.shree.id,
            qdrant_collection="corpus_main",
            api_key_hash="",
            is_system=False,
            created_at=datetime.now(UTC),
        )
    )
    key = world.db.add(
        WidgetKey(
            corpus_id=corpus.id,
            name="Quick test",
            public_key="pk_quicktest0001",
            allowed_origins=["*"],
            is_active=True,
        )
    )
    with with_app(world) as app:
        res = await post(app, await token(app))
    assert res.status_code == 200, res.text
    assert key.allowed_origins == ["*", SITE, WWW]
    assert len(world.db.all(WebsiteSource)) == 1


async def test_a_configured_business_name_survives_the_wizard(engine_for) -> None:
    world, engine, _ = setup()
    engine_for(engine)
    corpus = world.db.add(
        Corpus(
            name="Main",
            tenant_id=world.shree.id,
            qdrant_collection="corpus_main",
            api_key_hash="",
            is_system=False,
            created_at=datetime.now(UTC),
        )
    )
    engine.published[corpus.id] = {
        "version": 4,
        "config": {"identity": {"business_name": "Shree Dental Clinic, Pune"}},
        "draft_revision": 0,
    }
    with with_app(world) as app:
        await post(app, await token(app))
    assert (
        engine.published[corpus.id]["config"]["identity"]["business_name"]
        == "Shree Dental Clinic, Pune"
    )


async def test_a_knowledge_base_moved_to_another_workspace_is_not_shown_or_reused(
    engine_for,
) -> None:
    world, engine, _ = setup()
    engine_for(engine)
    with with_app(world) as app:
        tok, started, _key = await _started(world, engine, app)
        moved = next(c for c in world.db.all(Corpus) if str(c.id) == started["kb_id"])
        moved.tenant_id = uuid.uuid4()  # an admin moved it away
        _seen(world, started, WWW, False)
        seen = (await get(app, tok)).json()
        again = (await post(app, tok)).json()
    assert seen["kb_id"] is None and seen["learning"] is None and seen["snippet"] is None
    assert seen["connection"]["status"] == "waiting" and seen["connection"]["refused"] == []
    assert again["kb_id"] != started["kb_id"]
