"""TV6 review fixes on the worker side: the stall sweep, retries, STOP, tenant status."""

from datetime import timedelta

import pytest

from callobuzz_rag.channels.whatsapp.inbound import claim_next
from callobuzz_rag.worker import whatsapp_tasks
from tests.unit.whatsapp_fixtures import inbound_text
from tests.unit.whatsapp_rig import NOW, rig_for
from tests.unit.whatsapp_world import WhatsAppWorld

pytestmark = pytest.mark.assistant_on


@pytest.fixture
def world() -> WhatsAppWorld:
    return WhatsAppWorld()


# ----------------------------------------------------------- 2: the sweep counts from the claim


async def test_a_late_claimed_event_is_not_stalled_while_in_flight(world) -> None:
    with rig_for(world) as rig:
        (thread_id,) = await rig.deliver(inbound_text(wamid="wamid.A"))
        claimed = await claim_next(rig.deps(), thread_id)
    assert claimed is not None
    (event,) = world.events("wamid.A")
    assert event.status == "processing" and event.claimed_at == NOW
    event.received_at = NOW - timedelta(minutes=30)  # waited a long time, claimed just now
    out = await whatsapp_tasks.sweep_with(world.db.factory(), NOW + timedelta(minutes=1))
    assert event.status == "processing" and out["stalled"] == 0
    out = await whatsapp_tasks.sweep_with(world.db.factory(), NOW + timedelta(minutes=11))
    assert event.status == "failed" and event.error_code == "stalled" and out["stalled"] == 1
