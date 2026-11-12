"""A preset never silently turns on the local CPU re-ranker (seconds per question)."""

from collections.abc import Iterator
from unittest.mock import patch

import pytest

from callobuzz_rag.core import retrieval_presets as presets
from callobuzz_rag.core.config import Settings


@pytest.fixture
def cpu_only() -> Iterator[None]:
    with patch.object(presets, "_gpu_available", return_value=False):
        yield


@pytest.fixture
def gpu() -> Iterator[None]:
    with patch.object(presets, "_gpu_available", return_value=True):
        yield


def _settings(**over: object) -> Settings:
    return Settings(_env_file=None, **over)  # type: ignore[arg-type]


class TestOnACpuMachine:
    @pytest.mark.usefixtures("cpu_only")
    @pytest.mark.parametrize("name", ["economy", "standard", "premium"])
    def test_no_preset_turns_the_local_reranker_on(self, name: str) -> None:
        assert presets.expand_preset(name, _settings())["reranking_enabled"] is False

    @pytest.mark.usefixtures("cpu_only")
    def test_everything_else_in_the_preset_is_untouched(self) -> None:
        wanted = dict(presets.PRESETS["standard"])
        got = presets.expand_preset("standard", _settings())
        wanted["reranking_enabled"] = False
        assert got == wanted

    @pytest.mark.usefixtures("cpu_only")
    def test_no_settings_at_all_is_the_same_as_defaults(self) -> None:
        assert presets.expand_preset("premium")["reranking_enabled"] is False

    @pytest.mark.usefixtures("cpu_only")
    def test_explicitly_enabled_on_the_engine_means_on(self) -> None:
        settings = _settings(reranking_enabled=True)
        assert presets.expand_preset("standard", settings)["reranking_enabled"] is True

    @pytest.mark.usefixtures("cpu_only")
    def test_an_api_reranker_configured_means_on(self) -> None:
        settings = _settings(rerank_provider="llm")
        assert presets.expand_preset("standard", settings)["reranking_enabled"] is True

    @pytest.mark.usefixtures("cpu_only")
    def test_economy_stays_off_even_when_enabled_on_the_engine(self) -> None:
        # The economy preset asks for no re-ranking; the engine switch only unlocks the
        # presets that want it.
        settings = _settings(reranking_enabled=True)
        assert presets.expand_preset("economy", settings)["reranking_enabled"] is False


class TestOnAGpuMachine:
    @pytest.mark.usefixtures("gpu")
    def test_presets_keep_their_reranker(self) -> None:
        settings = _settings()
        assert presets.expand_preset("standard", settings)["reranking_enabled"] is True
        assert presets.expand_preset("premium", settings)["reranking_enabled"] is True
        assert presets.expand_preset("economy", settings)["reranking_enabled"] is False


class TestListing:
    @pytest.mark.usefixtures("cpu_only")
    def test_the_listing_shows_what_a_choice_would_store(self) -> None:
        listed = presets.effective_presets(_settings())
        assert listed["standard"]["reranking_enabled"] is False
        assert set(listed) == {"economy", "standard", "premium"}

    @pytest.mark.usefixtures("cpu_only")
    def test_the_static_table_is_not_mutated(self) -> None:
        presets.effective_presets(_settings())
        assert presets.PRESETS["standard"]["reranking_enabled"] is True

    @pytest.mark.usefixtures("gpu")
    def test_listing_on_a_gpu(self) -> None:
        assert presets.effective_presets(_settings())["premium"]["reranking_enabled"] is True


class TestProbeOffTheEventLoop:
    async def test_the_probe_runs_in_a_worker_thread_and_fills_the_cache(self) -> None:
        import threading

        seen: list[int] = []

        def fake() -> bool:
            seen.append(threading.get_ident())
            return True

        presets._gpu_available.cache_clear()
        with patch.object(presets, "_gpu_available", fake):
            assert await presets.probe_gpu() is True
        assert seen
        assert seen[0] != threading.get_ident()
        presets._gpu_available.cache_clear()


class TestGpuDetection:
    def test_no_torch_means_no_gpu(self) -> None:
        presets._gpu_available.cache_clear()
        with patch.dict("sys.modules", {"torch": None}):
            assert presets._gpu_available() is False
        presets._gpu_available.cache_clear()

    def test_cuda_reported_by_torch(self) -> None:
        import types

        fake = types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: True))
        presets._gpu_available.cache_clear()
        with patch.dict("sys.modules", {"torch": fake}):
            assert presets._gpu_available() is True
        presets._gpu_available.cache_clear()

    def test_a_broken_probe_means_no_gpu(self) -> None:
        import types

        def boom() -> bool:
            raise RuntimeError("driver mismatch")

        fake = types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=boom))
        presets._gpu_available.cache_clear()
        with patch.dict("sys.modules", {"torch": fake}):
            assert presets._gpu_available() is False
        presets._gpu_available.cache_clear()


@pytest.mark.usefixtures("cpu_only")
class TestRoutes:
    async def test_the_presets_endpoint_reports_effective_values(self) -> None:
        from tests.unit.scope_harness import call, scoped_app

        with scoped_app() as (app, _):
            res = await call(app, "admin", "GET", "/api/v1/models/presets")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["presets"]["standard"]["reranking_enabled"] is False
        assert body["presets"]["economy"]["reranking_enabled"] is False

    def test_create_stores_the_effective_value(self) -> None:
        from callobuzz_rag.api.routes.corpus import _resolve_retrieval_config_input
        from callobuzz_rag.api.schemas.corpus import RetrievalConfigInput

        stored = _resolve_retrieval_config_input(
            RetrievalConfigInput(preset="standard"), _settings()
        )
        assert stored is not None and stored["reranking_enabled"] is False

    def test_an_explicit_field_beats_the_preset(self) -> None:
        from callobuzz_rag.api.routes.corpus import _resolve_retrieval_config_input
        from callobuzz_rag.api.schemas.corpus import RetrievalConfigInput

        stored = _resolve_retrieval_config_input(
            RetrievalConfigInput(preset="standard", reranking_enabled=True), _settings()
        )
        assert stored is not None and stored["reranking_enabled"] is True

    def test_update_stores_the_effective_value(self) -> None:
        from callobuzz_rag.api.routes.corpus import _retrieval_config_changes
        from callobuzz_rag.api.schemas.corpus import RetrievalConfigInput

        stored = _retrieval_config_changes(
            RetrievalConfigInput(preset="premium"), None, _settings()
        )
        assert stored is not None and stored["reranking_enabled"] is False


def test_the_dashboard_copy_says_when_the_reranker_applies() -> None:
    """The preset cards are what an operator reads: they must not promise a re-ranker the
    engine will not run."""
    import pathlib

    strings = (
        pathlib.Path(__file__).resolve().parents[2]
        / "services"
        / "dashboard"
        / "src"
        / "lib"
        / "strings"
        / "kbs.ts"
    ).read_text(encoding="utf-8")
    assert "Balanced: rephrasings, re-ranker and variety on." not in strings
    assert "GPU" in strings.split("standard:")[1].split("premium:")[0]
