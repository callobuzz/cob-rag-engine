"""LP1 T4: the per-process published-site cache."""

from collections import Counter
from typing import Any

from callobuzz_rag.db.models.landing import LandingSite, LandingVersion
from callobuzz_rag.landing.cache import PublishedCache
from callobuzz_rag.landing.schema import default_content
from callobuzz_rag.landing.store import content_hash, theme_of
from tests.unit.accounts_fakes import FakeDB


class _Counting:
    """A session factory that counts the tables it reads."""

    def __init__(self, db: FakeDB) -> None:
        self.db = db
        self.reads: Counter[str] = Counter()

    def __call__(self) -> Any:
        counting = self

        class _Ctx:
            async def __aenter__(self) -> Any:
                session = counting.db.session()
                original = session.execute

                async def execute(stmt: Any, *a: Any, **k: Any) -> Any:
                    counting.reads[stmt.get_final_froms()[0].name] += 1
                    return await original(stmt, *a, **k)

                session.execute = execute
                return session

            async def __aexit__(self, *exc: Any) -> None:
                return None

        return _Ctx()


def _version(db: FakeDB, number: int) -> None:
    content = default_content()
    theme = theme_of(content).to_json()
    dump = content.model_dump(mode="json")
    db.add(
        LandingVersion(
            version=number,
            content=dump,
            theme=theme,
            theme_css=":root{}",
            content_hash=content_hash(dump, theme),
            published_by="admin key",
        )
    )


def _site(db: FakeDB, *, enabled: bool, version: int | None) -> LandingSite:
    content = default_content()
    return db.add(
        LandingSite(
            id=1,
            enabled=enabled,
            draft=content.model_dump(mode="json"),
            draft_revision=1,
            draft_theme=theme_of(content).to_json(),
            published_version=version,
            address="https://www.sharmadental.in",
        )
    )


async def test_it_reads_the_site_row_at_most_every_ten_seconds() -> None:
    db = FakeDB()
    _site(db, enabled=True, version=1)
    _version(db, 1)
    factory = _Counting(db)
    cache = PublishedCache()
    first = await cache.get(factory, 0.0)
    assert first is not None and first.version == 1
    for t in (1.0, 5.0, 9.9):
        assert await cache.get(factory, t) is not None
    assert factory.reads["landing_sites"] == 1
    await cache.get(factory, 10.0)
    assert factory.reads["landing_sites"] == 2
    assert factory.reads["landing_versions"] == 1  # the same version is never loaded again


async def test_a_new_published_version_loads_the_version_row_once() -> None:
    db = FakeDB()
    site = _site(db, enabled=True, version=1)
    _version(db, 1)
    _version(db, 2)
    factory = _Counting(db)
    cache = PublishedCache()
    assert (await cache.get(factory, 0.0)).version == 1  # type: ignore[union-attr]
    site.published_version = 2
    assert (await cache.get(factory, 5.0)).version == 1  # type: ignore[union-attr]  # not yet
    assert (await cache.get(factory, 10.5)).version == 2  # type: ignore[union-attr]
    assert (await cache.get(factory, 25.0)).version == 2  # type: ignore[union-attr]
    assert factory.reads["landing_versions"] == 2


async def test_invalidate_shows_a_publish_at_once() -> None:
    db = FakeDB()
    site = _site(db, enabled=True, version=1)
    _version(db, 1)
    _version(db, 2)
    factory = _Counting(db)
    cache = PublishedCache()
    await cache.get(factory, 0.0)
    site.published_version = 2
    cache.invalidate()
    assert (await cache.get(factory, 1.0)).version == 2  # type: ignore[union-attr]


async def test_off_means_none_even_with_a_published_version() -> None:
    db = FakeDB()
    site = _site(db, enabled=False, version=1)
    _version(db, 1)
    cache = PublishedCache()
    factory = _Counting(db)
    assert await cache.get(factory, 0.0) is None
    assert factory.reads["landing_versions"] == 0
    site.enabled = True
    cache.invalidate()
    assert await cache.get(factory, 0.0) is not None


async def test_no_site_row_is_off() -> None:
    cache = PublishedCache()
    assert await cache.get(_Counting(FakeDB()), 0.0) is None


async def test_site_settings_follow_the_row_not_the_frozen_version() -> None:
    db = FakeDB()
    site = _site(db, enabled=True, version=1)
    _version(db, 1)
    cache = PublishedCache()
    factory = _Counting(db)
    assert (await cache.get(factory, 0.0)).address == "https://www.sharmadental.in"  # type: ignore[union-attr]
    site.address = "https://sharmadental.in"
    assert (await cache.get(factory, 11.0)).address == "https://sharmadental.in"  # type: ignore[union-attr]


async def test_landing_is_on_only_for_an_enabled_published_site() -> None:
    from callobuzz_rag.landing import cache

    db = FakeDB()
    site = _site(db, enabled=True, version=1)
    _version(db, 1)
    cache.PUBLISHED.invalidate()
    assert await cache.landing_is_on(db.factory()) is True
    site.enabled = False
    cache.PUBLISHED.invalidate()
    assert await cache.landing_is_on(db.factory()) is False


async def test_landing_is_on_never_raises() -> None:
    from callobuzz_rag.landing import cache

    class Broken:
        def __call__(self) -> Any:
            raise RuntimeError("database down")

    cache.PUBLISHED.invalidate()
    assert await cache.landing_is_on(Broken()) is False
