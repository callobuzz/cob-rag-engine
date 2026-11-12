"""Fixtures shared by the unit tests."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.conversation import director as director_mod
from tests.unit.cob_harness import cob  # noqa: F401  (COB2: the COB chat world)


@pytest.fixture
def director_io() -> Any:
    """The director's two writes (the stored turn and the metering row), as mocks."""
    with (
        patch.object(director_mod, "persist_turn", AsyncMock(return_value=(None, None))) as p,
        patch.object(director_mod, "record_usage_new_session", AsyncMock()) as m,
    ):
        yield SimpleNamespace(persist=p, meter=m)


# PIP1 profile: the pipeline-profile lookup is one SQL join that the in-memory fakes most unit
# tests use cannot answer, so by default every answering surface is handed the Standard profile
# (a no-op overlay). A test that wants another profile sets `tenant_profile.profile`; the tiers
# and pipeline test modules below run the real lookup.
_REAL_PROFILE_MODULES = (
    "test_tiers_",
    "test_profile_everywhere",
    "test_pipelines_",
    "test_scope_pipeline",
    "test_director_busy",
)


class ProfileSwitch:
    """What `plan_profile` returns in a unit test (default: the no-op Standard)."""

    def __init__(self) -> None:
        from callobuzz_rag.tiers.profiles import noop_profile

        self.profile = noop_profile()
        self.calls = 0


@pytest.fixture(autouse=True)
def tenant_profile(request: pytest.FixtureRequest) -> Any:
    from callobuzz_rag.tiers import profiles

    switch = ProfileSwitch()
    name = request.module.__name__.rsplit(".", 1)[-1]
    if name.startswith(_REAL_PROFILE_MODULES):
        yield switch
        return

    async def fake_plan_profile(session: Any, tenant_id: Any, *, settings: Any = None) -> Any:
        switch.calls += 1
        return switch.profile

    with patch.object(profiles, "plan_profile", fake_plan_profile):
        yield switch


# PIP3 fair use: the same idea for the business's fair-use limits. By default every unit test
# sees a business with no limits (so the gate is cap + billing + profile only); a test that wants
# limits sets `fair_use.effective`. The fair-use modules below run the real lookup against the
# in-memory database.
_REAL_FAIR_USE_MODULES = (
    "test_fairuse_limits",
    "test_fairuse_usage",
    "test_fair_use_routes",
    "test_scope_pipeline",
)


class FairUseSwitch:
    """What `effective_fair_use` returns in a unit test (default: no limits, not exempt)."""

    def __init__(self) -> None:
        from callobuzz_rag.fairuse.limits import EffectiveFairUse, FairUseLimits, FairUsePolicy

        self.effective = EffectiveFairUse(
            limits=FairUseLimits(), policy=FairUsePolicy(), exempt=False, overridden=frozenset()
        )


@pytest.fixture(autouse=True)
def fair_use(request: pytest.FixtureRequest) -> Any:
    switch = FairUseSwitch()
    name = request.module.__name__.rsplit(".", 1)[-1]
    if name.startswith(_REAL_FAIR_USE_MODULES):
        yield switch
        return

    # Every module that binds `effective_fair_use` by name must be imported BEFORE the patch, or
    # its first import (a route test's `create_app()`) would keep the fake for the whole session.
    import callobuzz_rag.api.app
    import callobuzz_rag.fairuse.views  # noqa: F401

    async def fake_effective(session: Any, tenant_id: Any) -> Any:
        return switch.effective

    with (
        patch("callobuzz_rag.fairuse.gate.effective_fair_use", fake_effective),
        patch("callobuzz_rag.fairuse.limits.effective_fair_use", fake_effective),
    ):
        yield switch
