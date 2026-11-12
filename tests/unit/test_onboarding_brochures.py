"""TV5: the wizard's brochure step (four steps, `files` summary)."""

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from callobuzz_rag.api.schemas.onboarding import FilesOut
from callobuzz_rag.onboarding import service
from callobuzz_rag.onboarding.service import files_summary
from tests.unit.test_auth_routes import req
from tests.unit.test_onboarding_api import engine_for, get, post, setup, states, token  # noqa: F401
from tests.unit.test_website_api import with_app

pytestmark = pytest.mark.usefixtures("unlimited_plan")


async def test_four_steps_are_allowed_and_five_is_not(engine_for) -> None:  # noqa: F811
    world, engine, _ = setup()
    engine_for(engine)
    with with_app(world) as app:
        tok = await token(app)
        await post(app, tok)
        four = await req(app, "PATCH", "/api/v1/onboarding", token=tok, json={"step": 4})
        five = await req(app, "PATCH", "/api/v1/onboarding", token=tok, json={"step": 5})
        zero = await req(app, "PATCH", "/api/v1/onboarding", token=tok, json={"step": 0})
    assert four.status_code == 200 and four.json()["step"] == 4
    assert five.status_code == 422 and zero.status_code == 422


async def test_finishing_the_website_step_lands_on_brochures(engine_for) -> None:  # noqa: F811
    world, engine, _ = setup()
    engine_for(engine)
    with with_app(world) as app:
        started = await post(app, await token(app))
    assert started.json()["step"] == 2  # brochures; install is step 3 now
    assert states(world)[0].step == 2


async def test_skip_for_now_moves_to_install(engine_for) -> None:  # noqa: F811
    world, engine, _ = setup()
    engine_for(engine)
    with with_app(world) as app:
        tok = await token(app)
        await post(app, tok)
        skipped = await req(app, "PATCH", "/api/v1/onboarding", token=tok, json={"step": 3})
    assert skipped.json()["step"] == 3


async def test_the_state_carries_a_files_summary(engine_for, monkeypatch) -> None:  # noqa: F811
    world, engine, _ = setup()
    engine_for(engine)

    async def two_of_three(_session: Any, kb_id: Any) -> FilesOut:
        return FilesOut(count=3, learned=2) if kb_id else FilesOut(count=0, learned=0)

    monkeypatch.setattr(service, "files_summary", two_of_three)
    with with_app(world) as app:
        tok = await token(app)
        before = (await get(app, tok)).json()
        await post(app, tok)
        after = (await get(app, tok)).json()
    assert before["files"] == {"count": 0, "learned": 0}
    assert after["files"] == {"count": 3, "learned": 2}


class _Rows:
    def __init__(self, values: list[Any]) -> None:
        self._values = values

    def scalars(self) -> "_Rows":
        return self

    def all(self) -> list[Any]:
        return self._values

    def scalar_one(self) -> Any:
        return self._values[0]


class _Session:
    """Answers the two queries `files_summary` makes, and records them."""

    def __init__(self, ids: list[uuid.UUID], learned: int) -> None:
        self.statements: list[str] = []
        self._answers = [_Rows(ids), _Rows([learned])]

    async def execute(self, stmt: Any) -> _Rows:
        self.statements.append(str(stmt))
        return self._answers.pop(0)


async def test_files_are_uploaded_documents_with_a_learned_count() -> None:
    session = _Session([uuid.uuid4(), uuid.uuid4(), uuid.uuid4()], learned=2)
    got = await files_summary(session, uuid.uuid4())  # type: ignore[arg-type]
    assert got == FilesOut(count=3, learned=2)
    assert len(session.statements) == 2
    assert "raw_storage_key IS NOT NULL" in session.statements[0]  # an uploaded file, not a page
    assert "'active'" in session.statements[1] or "status" in session.statements[1]


async def test_no_files_means_one_query_and_zeroes() -> None:
    session = _Session([], learned=0)
    assert await files_summary(session, uuid.uuid4()) == FilesOut(count=0, learned=0)  # type: ignore[arg-type]
    assert len(session.statements) == 1


async def test_no_knowledge_base_means_no_query() -> None:
    session = _Session([], learned=0)
    assert await files_summary(session, None) == FilesOut(count=0, learned=0)  # type: ignore[arg-type]
    assert session.statements == []


async def test_a_database_problem_never_fails_the_state() -> None:
    class Broken:
        async def execute(self, _stmt: Any) -> Any:
            raise RuntimeError("down")

    assert await files_summary(Broken(), uuid.uuid4()) == FilesOut(count=0, learned=0)  # type: ignore[arg-type]


def test_files_out_is_a_plain_pair() -> None:
    assert SimpleNamespace(**FilesOut(count=1, learned=1).model_dump()).count == 1
