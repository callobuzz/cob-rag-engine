"""A pure reference model of one wallet, built only from the pure billing functions
(`ledger.check_sign`, `ledger.replay`, `lots.consume`, `lots.expire_amount`,
`posting.post_batch`). The property tests drive it with random sequences; the PostgreSQL test
runs the same sequences through the real code and compares. Reused by BIL6 and BIL7."""

import random
import uuid
from dataclasses import dataclass

from callobuzz_rag.billing.ledger import EntryType, IdempotencyConflict, check_sign, replay
from callobuzz_rag.billing.lots import Lot, consume, expire_amount
from callobuzz_rag.billing.money import MICRO_PER_MINOR
from callobuzz_rag.billing.posting import post_batch

OPS = ("top_up", "usage", "subscription", "included", "expire", "adjust", "refund", "post")


@dataclass
class ModelEntry:
    seq: int
    type: EntryType
    amount: int
    balance_after: int
    key: str


class WalletModel:
    def __init__(self, currency: str = "INR") -> None:
        self.currency = currency
        self.entries: list[ModelEntry] = []
        self.lots: list[Lot] = []
        self.charged_micro = 0  # every usage charge ever made
        self.unposted_micro = 0
        self.residue = 0
        self.posted_usage_minor = 0  # sum of usage_charge entry amounts (negative)
        self._n = 0
        self._requests: set[uuid.UUID] = set()  # the unique `usage_charges.request_id`

    @property
    def balance(self) -> int:
        return self.entries[-1].balance_after if self.entries else 0

    @property
    def live_credit(self) -> int:
        return sum(lot.remaining for lot in self.lots)

    def _append(
        self, entry_type: EntryType, amount: int, key: str, *, lot_expiry: int | None = None
    ) -> None:
        for existing in self.entries:
            if existing.key == key:
                if existing.type != entry_type or existing.amount != amount:
                    raise IdempotencyConflict(key)
                return
        check_sign(entry_type, amount)
        seq = len(self.entries) + 1
        after = replay([amount], self.balance)[0]
        self.entries.append(ModelEntry(seq, entry_type, amount, after, key))
        if lot_expiry is not None:
            self.lots.append(Lot(f"lot-{seq}", amount, amount, lot_expiry))
        if entry_type is EntryType.USAGE_CHARGE:
            self.lots, _ = consume(self.lots, -amount)

    def _key(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}-{self._n}"

    def top_up(self, amount: int, key: str | None = None) -> None:
        self._append(EntryType.TOP_UP, amount, key or self._key("t"))

    def charge(self, micro: int) -> None:
        self.charged_micro += micro
        self.unposted_micro += micro

    def record(self, request_id: uuid.UUID, micro: int) -> str:
        """The metering call: `charging.charge_usage` inserts one `usage_charges` row per request
        id (`ON CONFLICT (request_id) DO NOTHING`), so a repeat of a known id changes nothing and
        reports `duplicate`; a new id is charged and reports `charged`."""
        if request_id in self._requests:
            return "duplicate"
        self._requests.add(request_id)
        self.charge(micro)
        return "charged"

    def post(self) -> None:
        minor, rest = post_batch([self.unposted_micro], self.residue)
        self.posted_usage_minor -= minor
        self.residue = rest
        self.unposted_micro = 0
        if minor:
            self._append(EntryType.USAGE_CHARGE, -minor, self._key("u"))

    def apply(self, op: str, rng: random.Random) -> None:
        amount = rng.randint(1, 10**7)
        if op == "top_up":
            self.top_up(amount)
        elif op == "usage":
            self.charge(rng.randint(0, 10**6))
        elif op == "subscription":
            self._append(EntryType.SUBSCRIPTION_CHARGE, -amount, self._key("s"))
        elif op == "included":
            self._append(
                EntryType.INCLUDED_CREDIT, amount, self._key("i"), lot_expiry=rng.randint(1, 5)
            )
        elif op == "expire":
            live = [lot for lot in self.lots if lot.remaining > 0]
            if not live:
                return
            lot = rng.choice(live)
            gone = expire_amount(lot.remaining, self.balance)
            self.lots = [
                Lot(x.id, x.amount, 0, x.expires_at) if x.id == lot.id else x for x in self.lots
            ]
            if gone:
                self._append(EntryType.EXPIRY, -gone, self._key("e"))
        elif op == "adjust":
            self._append(EntryType.ADJUSTMENT, rng.choice((-1, 1)) * amount, self._key("a"))
        elif op == "refund":
            refundable = self.balance - self.live_credit - self.unposted_micro // MICRO_PER_MINOR
            if refundable > 0:
                self._append(EntryType.REFUND, -rng.randint(1, refundable), self._key("r"))
        elif op == "post":
            self.post()
        else:  # pragma: no cover
            raise ValueError(op)


def random_wallet(seed: int) -> WalletModel:
    """A wallet that starts with a top-up of exactly 5_000 under key `seed-0`, then 1..40
    random operations."""
    rng = random.Random(seed)
    wallet = WalletModel("INR")
    wallet.top_up(5_000, key="seed-0")
    for _ in range(rng.randint(1, 40)):
        wallet.apply(rng.choice(OPS), rng)
    return wallet
