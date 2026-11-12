"""Website learning: the policy part (Task 5). The run tests are added with Task 6."""

import uuid

import pytest

from callobuzz_rag.core.config import Settings
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.website import WebsiteSettings, WebsiteSource
from callobuzz_rag.website.policy import website_policy
from tests.unit.accounts_fakes import FakeDB


def settings(**kw: object) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


class Setup:
    def __init__(self, **plan: object) -> None:
        self.db = FakeDB()
        self.plan = self.db.add(Plan(name="Pune Pro", is_active=True, **plan))
        self.tenant = self.db.add(Tenant(name="Shree Dental Clinic", plan_id=self.plan.id))
        self.corpus = self.db.add(
            Corpus(
                name="Shree Dental",
                tenant_id=self.tenant.id,
                qdrant_collection="corpus_aaaa0001",
                api_key_hash="",
                is_system=False,
            )
        )

    def source(self, scope: str, learned: int, corpus: Corpus | None = None) -> WebsiteSource:
        c = corpus or self.corpus
        return self.db.add(
            WebsiteSource(
                corpus_id=c.id,
                tenant_id=c.tenant_id,
                origin=f"https://{scope}",
                scope=scope,
                pages_learned=learned,
            )
        )


async def policy(s: Setup, cfg: Settings | None = None, **kw: object):
    return await website_policy(s.db.session(), s.corpus, cfg or settings(), **kw)  # type: ignore[arg-type]


async def test_default_is_enabled_with_the_engine_ceiling() -> None:
    p = await policy(Setup(website_learning_enabled=True, website_pages_max=None))
    assert (p.enabled, p.reason, p.pages_cap) == (True, None, 2000)


async def test_engine_switch_off() -> None:
    p = await policy(Setup(website_learning_enabled=True), settings(website_learning_enabled=False))
    assert (p.enabled, p.reason) == (False, "engine")


async def test_plan_switch_off() -> None:
    p = await policy(Setup(website_learning_enabled=False))
    assert (p.enabled, p.reason) == (False, "plan")


async def test_knowledge_base_switch_off() -> None:
    s = Setup(website_learning_enabled=True)
    s.db.add(WebsiteSettings(corpus_id=s.corpus.id, tenant_id=s.tenant.id, enabled=False))
    p = await policy(s)
    assert (p.enabled, p.reason) == (False, "kb")


async def test_the_system_knowledge_base_never_learns() -> None:
    s = Setup(website_learning_enabled=True)
    s.corpus.is_system = True
    p = await policy(s)
    assert (p.enabled, p.reason) == (False, "system")


async def test_cap_is_min_of_plan_and_engine_minus_other_sites() -> None:
    s = Setup(website_learning_enabled=True, website_pages_max=50)
    s.source("shreedental.in", 7)  # the site being learned: not counted against itself
    s.source("shreedental.co.in", 12)
    other_kb = s.db.add(
        Corpus(
            name="Branch",
            tenant_id=uuid.uuid4(),
            qdrant_collection="corpus_aaaa0002",
            api_key_hash="",
        )
    )
    s.source("branch.example.in", 99, corpus=other_kb)  # another workspace: never counted
    mine = next(x for x in s.db.all(WebsiteSource) if x.scope == "shreedental.in")
    p = await policy(s, exclude_source_id=mine.id)
    assert p.pages_cap == 50 - 12


@pytest.mark.parametrize(
    "plan_cap,engine,expected",
    [(None, 2000, 2000), (5000, 300, 300), (40, 2000, 40), (10, 2000, 10)],
)
async def test_engine_ceiling_always_applies(
    plan_cap: int | None, engine: int, expected: int
) -> None:
    s = Setup(website_learning_enabled=True, website_pages_max=plan_cap)
    p = await policy(s, settings(website_max_pages=engine))
    assert p.pages_cap == expected


async def test_cap_never_goes_negative() -> None:
    s = Setup(website_learning_enabled=True, website_pages_max=10)
    s.source("a.example.in", 25)
    assert (await policy(s)).pages_cap == 0
