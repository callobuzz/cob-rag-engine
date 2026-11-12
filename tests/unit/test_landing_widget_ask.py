"""LP2 T5: "Ask it yourself" opens the deployer's real widget on click, prefilled, never sent."""

import re
import uuid
from pathlib import Path
from typing import Any

import pytest

from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.widget_key import WidgetKey
from callobuzz_rag.landing import service
from tests.unit.accounts_fakes import FakeDB
from tests.unit.landing_fakes import content_with
from tests.unit.landing_home_fakes import parse, render_home

ORIGIN = "https://www.sharmadental.in"
WIDGET_JS = Path(__file__).parents[2] / "src/callobuzz_rag/widget/static/widget.js"


def _ask_on(data: dict[str, Any]) -> None:
    data["ask"]["enabled"] = True


def _buttons(html: str) -> list[Any]:
    return [n for n in parse(html).find_all("button") if "data-ask" in n.attrs]


def test_the_button_appears_only_with_a_ready_key_and_the_ask_switch() -> None:
    on = render_home(content_with(_ask_on), ask_public_key="pk_demo")
    buttons = _buttons(on)
    assert len(buttons) == 1
    assert buttons[0].attrs["data-ask-key"] == "pk_demo"
    assert buttons[0].attrs["data-ask-text"] == "Fees kitni hai? Sunday ko khula hai?"
    assert buttons[0].all_text() == "Ask it yourself"
    # no key (not connected, inactive, origin missing: the environment holds None) -> no button
    assert _buttons(render_home(content_with(_ask_on), ask_public_key=None)) == []
    # switched off in the content -> no button even with a key
    assert _buttons(render_home(ask_public_key="pk_demo")) == []


def test_the_page_holds_no_key_material_except_the_public_key() -> None:
    html = render_home(content_with(_ask_on), ask_public_key="pk_demo")
    assert not re.search(r"\b(ck|tk)_[A-Za-z0-9]", html)
    assert "pk_demo" in html


def test_the_page_loads_nothing_from_the_widget_before_a_click() -> None:
    html = render_home(content_with(_ask_on), ask_public_key="pk_demo")
    assert "/widget.js" not in html and "/api/" not in html


def _kb(db: FakeDB, *, origins: list[str], active: bool = True) -> WidgetKey:
    operator = db.add(Tenant(name="Op", is_operator=True))
    corpus = db.add(
        Corpus(name="Clinic", tenant_id=operator.id, qdrant_collection="c", api_key_hash="")
    )
    return db.add(
        WidgetKey(
            corpus_id=corpus.id,
            name="Site",
            public_key="pk_demo",
            allowed_origins=origins,
            is_active=active,
        )
    )


@pytest.mark.parametrize(
    ("origins", "active", "expected"),
    [
        ([ORIGIN], True, "ready"),
        ([ORIGIN], False, "key_inactive"),
        (["https://other.in"], True, "origin_missing"),
    ],
)
async def test_only_an_active_key_that_allows_the_landing_origin_is_ready(
    origins: list[str], active: bool, expected: str
) -> None:
    db = FakeDB()
    key = _kb(db, origins=origins, active=active)
    status, found = await service.ask_key(db.session(), key.id, service.origin_of(ORIGIN))
    assert status == expected and (found is not None) == (expected == "ready")


async def test_a_deleted_key_means_not_connected() -> None:
    db = FakeDB()
    status, found = await service.ask_key(db.session(), uuid.uuid4(), ORIGIN)
    assert (status, found) == ("not_connected", None)


def test_the_widget_prefills_the_box_once_and_never_sends_it() -> None:
    source = WIDGET_JS.read_text(encoding="utf-8")
    block = source.split("// LP2 landing")[1].split("// /LP2 landing")[0]
    assert 'getAttribute("data-ask")' in block and ".slice(0, 200)" in block
    assert "el.input.value = ask" in block
    assert "submit(" not in block and "fetch(" not in block
    assert "__cobRagWidgetOpen" in block
    assert "data-ask " in source.split("*/")[0]  # documented in the header comment
