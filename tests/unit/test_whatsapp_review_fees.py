"""TV6 review: Meta fees are counted per category and the free allowance is read under a lock."""

import uuid
from datetime import UTC, datetime
from unittest.mock import patch

from callobuzz_rag.channels.whatsapp import fees
from tests.unit.test_whatsapp_fees import RATES, FeeRig
from tests.unit.whatsapp_world import WhatsAppWorld


async def test_the_charge_records_its_category() -> None:
    rig = FeeRig(WhatsAppWorld(), meta_billing="deployer", fees_mode="pass_through", rates=RATES)
    await rig.send("wamid.A", category="utility")
    await rig.send("wamid.B", category="service")
    assert [c["model"] for c in rig.charges] == ["utility", "service"]


async def test_only_service_rows_count_toward_the_free_allowance() -> None:
    captured = []

    class Session:
        async def execute(self, stmt):
            captured.append(stmt)

            class R:
                def scalar_one(self):
                    return 0

            return R()

    await fees.used_this_month(Session(), uuid.uuid4(), datetime(2026, 10, 5, tzinfo=UTC))  # type: ignore[arg-type]
    sql = str(captured[0].compile(compile_kwargs={"literal_binds": True}))
    assert "usage_charges.model = 'service'" in sql and "whatsapp_fee" in sql


async def test_the_allowance_is_read_after_the_number_is_locked() -> None:
    rig = FeeRig(
        WhatsAppWorld(),
        meta_billing="deployer",
        fees_mode="pass_through",
        rates={"rates_minor": {"INR": {"service": 12}}, "free_service_per_month": 1},
    )
    events: list[str] = []
    real = rig.world.db.session()

    class Spy:
        def __getattr__(self, name):
            return getattr(real, name)

        async def execute(self, stmt, *a, **k):
            if getattr(stmt, "_for_update_arg", None) is not None:
                events.append("lock")
            return await real.execute(stmt, *a, **k)

    async def used(session, corpus_id, moment):
        events.append("count")
        return 0

    with (
        patch.object(fees, "charge_pass_through", rig.fake_charge),
        patch.object(fees, "used_this_month", used),
    ):
        await fees.charge_send(Spy(), rig.context(), "wamid.L1", "service")  # type: ignore[arg-type]
    assert events == ["lock", "count"]
