"""LP4 T1: connect "Ask it yourself" to an operator knowledge base."""

import uuid
from typing import Any

import pytest

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.cob.tools.http import InternalResponse
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.widget_key import WidgetKey
from callobuzz_rag.landing import assistant_link, store
from tests.unit.accounts_fakes import FakeDB

ORIGIN = "https://www.sharmadental.in"


class Recorder:
    def __init__(self, response: InternalResponse | None = None) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.response = response

    async def request(self, method: str, path: str, *, json: Any = None, **_: Any) -> Any:
        self.calls.append((method, path, json))
        return self.response or InternalResponse(status=201, body={"id": str(uuid.uuid4())})


def _world() -> tuple[FakeDB, Corpus, Corpus]:
    db = FakeDB()
    operator = db.add(Tenant(name="Op", is_operator=True))
    other = db.add(Tenant(name="Customer", is_operator=False))
    mine = db.add(
        Corpus(name="Ours", tenant_id=operator.id, qdrant_collection="a", api_key_hash="")
    )
    theirs = db.add(
        Corpus(name="Theirs", tenant_id=other.id, qdrant_collection="b", api_key_hash="")
    )
    return db, mine, theirs


def _key(db: FakeDB, corpus: Corpus, origins: list[str], *, active: bool = True, name: str = "K"):
    return db.add(
        WidgetKey(
            corpus_id=corpus.id,
            name=name,
            public_key=f"pk_{uuid.uuid4().hex[:10]}",
            allowed_origins=origins,
            is_active=active,
        )
    )


async def test_reuses_an_active_key_that_already_allows_the_site() -> None:
    db, mine, _ = _world()
    key = _key(db, mine, [ORIGIN + "/"], name="Main site")
    rec = Recorder()
    session = db.session()
    result = await assistant_link.connect(
        session, client=rec, corpus_id=mine.id, origin=ORIGIN, updated_by="a@b.in"
    )
    assert rec.calls == [] and result.created is False
    site = await store.ensure_site(session)
    assert site.ask_widget_key_id == key.id and site.ask_corpus_id == mine.id


async def test_creates_a_dedicated_key_and_never_edits_other_keys() -> None:
    db, mine, _ = _world()
    other = _key(db, mine, ["https://clinic-old.in"], name="Old site")
    rec = Recorder()
    result = await assistant_link.connect(
        db.session(), client=rec, corpus_id=mine.id, origin=ORIGIN, updated_by="a"
    )
    assert rec.calls == [
        (
            "POST",
            f"/api/v1/corpora/{mine.id}/widget-keys",
            {"name": "Public website", "allowed_origins": [ORIGIN]},
        )
    ]
    assert result.created and other.allowed_origins == ["https://clinic-old.in"]


async def test_an_inactive_key_is_not_reused() -> None:
    db, mine, _ = _world()
    _key(db, mine, [ORIGIN], active=False)
    rec = Recorder()
    await assistant_link.connect(
        db.session(), client=rec, corpus_id=mine.id, origin=ORIGIN, updated_by="a"
    )
    assert len(rec.calls) == 1


async def test_a_customer_kb_is_refused() -> None:
    db, _, theirs = _world()
    with pytest.raises(APIError) as err:
        await assistant_link.connect(
            db.session(), client=Recorder(), corpus_id=theirs.id, origin=ORIGIN, updated_by="a"
        )
    assert err.value.code == "landing_not_operator_kb"


async def test_no_address_means_no_origin() -> None:
    db, mine, _ = _world()
    with pytest.raises(APIError) as err:
        await assistant_link.connect(
            db.session(), client=Recorder(), corpus_id=mine.id, origin=None, updated_by="a"
        )
    assert err.value.code == "landing_needs_address"


async def test_the_inner_route_error_passes_through() -> None:
    db, mine, _ = _world()
    refusing = Recorder(
        InternalResponse(
            status=409,
            body={
                "detail": "Your plan allows no more keys.",
                "code": "widget_key_limit_reached",
                "limit": 1,
                "used": 1,
            },
        )
    )
    with pytest.raises(APIError) as err:
        await assistant_link.connect(
            db.session(), client=refusing, corpus_id=mine.id, origin=ORIGIN, updated_by="a"
        )
    assert err.value.status_code == 409 and err.value.code == "widget_key_limit_reached"
    assert err.value.extra == {"limit": 1, "used": 1}


async def test_disconnect_clears_the_link_and_keeps_the_key() -> None:
    db, mine, _ = _world()
    key = _key(db, mine, [ORIGIN])
    session = db.session()
    await assistant_link.connect(
        session, client=Recorder(), corpus_id=mine.id, origin=ORIGIN, updated_by="a"
    )
    site = await assistant_link.disconnect(session, updated_by="a")
    assert site.ask_widget_key_id is None and site.ask_corpus_id is None
    assert key in db.all(WidgetKey) and key.is_active
