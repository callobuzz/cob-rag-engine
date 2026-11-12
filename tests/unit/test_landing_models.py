"""LP1 T1: the landing tables are registered and carry the pinned constraints."""

from typing import Any

from callobuzz_rag.db.models import LandingAsset, LandingSite, LandingVersion
from tests.unit.accounts_fakes import MODELS


def _check_text(model: Any, name: str) -> str:
    check = next(c for c in model.__table_args__ if getattr(c, "name", "") == name)
    return str(check.sqltext)


def test_models_are_registered() -> None:
    assert MODELS["landing_sites"] is LandingSite
    assert MODELS["landing_versions"] is LandingVersion
    assert MODELS["landing_assets"] is LandingAsset


def test_there_is_exactly_one_site_row() -> None:
    assert _check_text(LandingSite, "ck_landing_sites_single").replace(" ", "") == "id=1"
    assert LandingSite.__table__.c.enabled.server_default is not None


def test_versions_are_unique_per_number() -> None:
    assert LandingVersion.__table__.c.version.unique


def test_asset_checks_list_exactly_the_pinned_values() -> None:
    kinds = _check_text(LandingAsset, "ck_landing_assets_kind")
    for kind in ("logo", "poster", "video", "og"):
        assert f"'{kind}'" in kinds
    assert kinds.count("'") == 8
    types = _check_text(LandingAsset, "ck_landing_assets_content_type")
    for ctype in ("image/png", "image/webp", "video/mp4", "video/webm"):
        assert f"'{ctype}'" in types
    assert types.count("'") == 8


def test_ask_links_are_set_null() -> None:
    table = LandingSite.__table__
    (corpus_fk,) = table.c.ask_corpus_id.foreign_keys
    (key_fk,) = table.c.ask_widget_key_id.foreign_keys
    assert corpus_fk.column.table.name == "corpora" and corpus_fk.ondelete == "SET NULL"
    assert key_fk.column.table.name == "widget_keys" and key_fk.ondelete == "SET NULL"
