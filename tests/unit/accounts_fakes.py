"""In-memory stand-ins for the account tests: a tiny database and a Redis.

`FakeDB` keeps real model instances (User, TenantMember, Tenant, UserSession, EmailCode,
AuditLog, Corpus, Invitation) and answers the SQLAlchemy statements the accounts code issues by
evaluating their WHERE clauses in Python: column-vs-value comparisons joined by AND, `IN`,
`IS`, `LIKE 'prefix%'`, ordering and limits; inner joins of plain tables; UPDATE; and
DELETE with RETURNING and the schema's cascades (a tenant takes its memberships, a user
its memberships, sessions and codes; a tenant that still owns corpora is refused, as
RESTRICT would). The middleware's one four-way join is answered by `_session_row`. It is
not a database — the integration tests run the same flows on PostgreSQL.

`FakeRedis` implements the few commands `AccountRateLimiter` uses, with a clock the
test can move.
"""

import operator
import re
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import (
    BinaryExpression,
    BindParameter,
    BooleanClauseList,
    Cast,
    False_,
    Grouping,
    Null,
    True_,
    UnaryExpression,
)
from sqlalchemy.sql.selectable import Join

from callobuzz_rag.db.models.add_on import AddOn, AddOnGrant  # BIL5
from callobuzz_rag.db.models.api_key import ApiKey
from callobuzz_rag.db.models.assistant_config import AssistantConfigVersion
from callobuzz_rag.db.models.assistant_setup import AssistantSetupSession
from callobuzz_rag.db.models.assistant_turn import AssistantTurn  # COB7
from callobuzz_rag.db.models.audit_log import AuditLog
from callobuzz_rag.db.models.billing_account import BillingAccount  # BIL2
from callobuzz_rag.db.models.billing_notice import BillingNotice  # TV3
from callobuzz_rag.db.models.billing_reconciliation import BillingReconciliation  # BIL3
from callobuzz_rag.db.models.billing_statement import BillingStatement  # BIL6
from callobuzz_rag.db.models.channel import (  # TV6
    ChannelConnection,
    ChannelEvent,
    ChannelThread,
)
from callobuzz_rag.db.models.cob_action_proposal import CobActionProposal  # COB5
from callobuzz_rag.db.models.cob_status import CobStatus
from callobuzz_rag.db.models.conversation import Conversation  # COB2
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.document import Document  # TV6
from callobuzz_rag.db.models.document_version import DocumentVersion  # TV6
from callobuzz_rag.db.models.email_code import EmailCode
from callobuzz_rag.db.models.engine_settings import EngineSettingsRow
from callobuzz_rag.db.models.fair_use import FairUseAlert, FairUseCounter  # PIP3
from callobuzz_rag.db.models.handoff import Handoff
from callobuzz_rag.db.models.invitation import Invitation
from callobuzz_rag.db.models.knowledge_article import KnowledgeArticle  # BIZ1
from callobuzz_rag.db.models.landing import LandingAsset, LandingSite, LandingVersion  # LP1 landing
from callobuzz_rag.db.models.lead import Lead
from callobuzz_rag.db.models.lead_consent import LeadConsent
from callobuzz_rag.db.models.legal import LegalAcceptance, LegalDocument  # P3 legal
from callobuzz_rag.db.models.message import Message  # COB2
from callobuzz_rag.db.models.onboarding import OnboardingState, WidgetOriginSeen  # BIZ4
from callobuzz_rag.db.models.payment import (  # P1 payments
    BillingProfile,
    Payment,
    PaymentEvent,
    PaymentOrder,
    PaymentProviderSetting,
    PaymentRefund,
)
from callobuzz_rag.db.models.pipeline_profile import (  # PIP1
    PipelineProfile,
    TenantPipelineSettings,
)
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.plan_pricing import PlanPricing  # BIL2
from callobuzz_rag.db.models.provider_balance_setting import ProviderBalanceSetting  # BIL1
from callobuzz_rag.db.models.public_asset import PublicAsset  # BIZ4
from callobuzz_rag.db.models.service_order import ServiceOrder  # P5 plans
from callobuzz_rag.db.models.support import SupportMessage, SupportRequest  # TV3
from callobuzz_rag.db.models.tax_document import TaxDocument, TaxDocumentCounter  # P2 invoices
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.tenant_member import TenantMember
from callobuzz_rag.db.models.user import User
from callobuzz_rag.db.models.user_session import UserSession
from callobuzz_rag.db.models.value import ValueReport, ValueSettings  # TV4
from callobuzz_rag.db.models.wallet_ledger import WalletLedger  # BIL3
from callobuzz_rag.db.models.webhook_config import WebhookConfig
from callobuzz_rag.db.models.website import (  # BIZ1
    WebsitePage,
    WebsiteRun,
    WebsiteSettings,
    WebsiteSource,
)

MODELS = {
    m.__tablename__: m
    for m in (
        User,
        TenantMember,
        Tenant,
        UserSession,
        EmailCode,
        AuditLog,
        Corpus,
        Invitation,
        Plan,
        PlanPricing,  # BIL2
        ValueSettings,  # TV4
        ValueReport,  # TV4
        ServiceOrder,  # P5 plans
        SupportRequest,  # TV3
        SupportMessage,  # TV3
        BillingNotice,  # TV3
        BillingAccount,  # BIL2
        WalletLedger,  # BIL3
        BillingReconciliation,  # BIL3
        BillingStatement,  # BIL6
        AddOn,  # BIL5
        AddOnGrant,  # BIL5
        ProviderBalanceSetting,  # BIL1
        # P1 payments
        BillingProfile,
        Payment,
        PaymentEvent,
        PaymentOrder,
        PaymentProviderSetting,
        PaymentRefund,
        # /P1 payments
        TaxDocument,  # P2 invoices
        TaxDocumentCounter,  # P2 invoices
        EngineSettingsRow,
        ApiKey,
        AssistantConfigVersion,
        AssistantSetupSession,
        WebhookConfig,
        Lead,
        LeadConsent,
        Handoff,
        CobStatus,  # COB1
        CobActionProposal,  # COB5
        Conversation,  # COB2
        Message,  # COB2
        AssistantTurn,  # COB7
        WebsiteSettings,  # BIZ1
        WebsiteSource,  # BIZ1
        WebsitePage,  # BIZ1
        WebsiteRun,  # BIZ1
        KnowledgeArticle,  # BIZ1
        OnboardingState,  # BIZ4
        WidgetOriginSeen,  # BIZ4
        PublicAsset,  # BIZ4
        PipelineProfile,  # PIP1
        TenantPipelineSettings,  # PIP1
        FairUseCounter,  # PIP3
        FairUseAlert,  # PIP3
        ChannelConnection,  # TV6
        ChannelThread,  # TV6
        ChannelEvent,  # TV6
        Document,  # TV6
        DocumentVersion,  # TV6
        LegalDocument,  # P3 legal
        LegalAcceptance,  # P3 legal
        LandingSite,  # LP1 landing
        LandingVersion,  # LP1 landing
        LandingAsset,  # LP1 landing
    )
}


class RestrictViolationError(Exception):
    """What PostgreSQL's ON DELETE RESTRICT would raise."""


def _join_steps(from_obj: Any) -> list[tuple[Any, Any, bool]]:
    """`(table, ON clause, outer?)` for each table of a left-deep join, the first with no ON."""
    if isinstance(from_obj, Join):
        steps = _join_steps(from_obj.left)
        (table, _, _), *rest = _join_steps(from_obj.right)
        return [*steps, (table, from_obj.onclause, bool(from_obj.isouter)), *rest]
    return [(from_obj, None, False)]


_OPS = {
    operators.eq: operator.eq,
    operators.ne: operator.ne,
    operators.gt: operator.gt,
    operators.ge: operator.ge,
    operators.lt: operator.lt,
    operators.le: operator.le,
    operators.is_: lambda a, b: a is b,
    operators.is_not: lambda a, b: a is not b,
    operators.in_op: lambda a, b: a in b,
    operators.is_distinct_from: lambda a, b: a != b,  # COB1: NULL-safe, like SQL's
}


_ORDERING = (operators.gt, operators.ge, operators.lt, operators.le)


def _value(element: Any) -> Any:
    if isinstance(element, BindParameter):
        value = element.effective_value
        return list(value) if element.expanding else value
    if isinstance(element, Null):
        return None
    if isinstance(element, True_):
        return True
    if isinstance(element, False_):
        return False
    raise NotImplementedError(f"unsupported operand {element!r}")


def _column_key(element: Any) -> tuple[str, str]:
    return element.table.name, element.key


def matches(clause: Any, rows: dict[str, Any]) -> bool:
    """Evaluate `clause` against {table name: row}."""
    if clause is None:
        return True
    if isinstance(clause, Grouping):  # parentheses, e.g. around an OR
        return matches(clause.element, rows)
    if isinstance(clause, BooleanClauseList):
        results = [matches(c, rows) for c in clause.clauses]
        return all(results) if clause.operator is operators.and_ else any(results)
    if isinstance(clause, BinaryExpression):
        column = clause.left.clause if isinstance(clause.left, Cast) else clause.left
        table, key = _column_key(column)
        row = rows.get(table)
        if row is None:
            return False
        left = getattr(row, key)
        if getattr(clause.operator, "opstring", None) == "?":  # JSONB has_key
            return left is not None and str(_value(clause.right)) in left
        if clause.operator is operators.like_op:  # only the `prefix%` form the code uses
            pattern = str(_value(clause.right))
            assert pattern.endswith("%") and "%" not in pattern[:-1], pattern
            return str(left).startswith(pattern[:-1])
        if clause.operator is operators.ilike_op:  # the `%x%` contains form, with an escape
            pattern = str(_value(clause.right))
            escape = clause.modifiers.get("escape")
            text = pattern[1:-1] if pattern.startswith("%") and pattern.endswith("%") else pattern
            if escape:
                text = re.sub(re.escape(escape) + "(.)", lambda m: m.group(1), text)
            return text.lower() in str(left).lower()
        if hasattr(clause.right, "table"):  # column = column
            rtable, rkey = _column_key(clause.right)
            right = getattr(rows[rtable], rkey)
        else:
            right = _value(clause.right)
        if clause.operator in _ORDERING and (left is None or right is None):
            return False  # SQL: NULL < x is NULL, which no WHERE keeps
        return bool(_OPS[clause.operator](left, right))
    raise NotImplementedError(f"unsupported clause {clause!r}")


class Result:
    def __init__(self, rows: list[Any], *, scalar: bool, rowcount: int = 0) -> None:
        self._rows = rows
        self._scalar = scalar
        self.rowcount = rowcount

    def scalar_one_or_none(self) -> Any:
        assert len(self._rows) <= 1, self._rows
        if not self._rows:
            return None
        return self._rows[0] if self._scalar else self._rows[0][0]

    def scalar_one(self) -> Any:
        row = self.scalar_one_or_none()
        assert row is not None
        return row

    def scalars(self) -> "Result":
        return Result(
            [r if self._scalar else r[0] for r in self._rows], scalar=True, rowcount=self.rowcount
        )

    def all(self) -> list[Any]:
        return list(self._rows)

    def tuples(self) -> "Result":
        return self

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def one(self) -> Any:
        assert len(self._rows) == 1, self._rows
        return self._rows[0]


def _sums(descriptions: list[Any], rows: list[Any]) -> list[int]:
    """`coalesce(sum(col), 0)` over `rows`, one value per described column."""
    out: list[int] = []
    for d in descriptions:
        inner = next(iter(d["expr"].clauses))  # the sum(...) call
        column = next(iter(inner.clauses))
        out.append(sum(getattr(r, column.key) for r in rows))
    return out


class FakeDB:
    """Rows by model, plus a session that reads and writes them."""

    def __init__(self) -> None:
        self.rows: defaultdict[type, list[Any]] = defaultdict(list)
        self.commits = 0
        self.rollbacks = 0
        # What the phase11_006 migration seeds (accounts A6): the unlimited default plan,
        # marked built in by phase11_008 (A8).
        self.default_plan = self.add(
            Plan(name="Standard", is_default=True, is_active=True, is_builtin=True)
        )

    # ------------------------------------------------------------ helpers

    def add(self, obj: Any) -> Any:
        for column in obj.__table__.columns:
            if getattr(obj, column.key) is None:
                if column.key == "id":
                    obj.id = uuid.uuid4()
                elif column.key in ("created_at", "updated_at"):
                    setattr(obj, column.key, datetime.now(UTC))
                elif column.default is not None and column.default.is_scalar:
                    setattr(obj, column.key, column.default.arg)
                elif column.default is not None and column.default.is_callable:
                    setattr(obj, column.key, column.default.arg(None))
        if isinstance(obj, Tenant) and obj.plan_id is None:  # the INSERT-time default
            obj.plan_id = next((p.id for p in self.rows[Plan] if p.is_default), None)
        bucket = self.rows[type(obj)]
        if obj not in bucket:
            bucket.append(obj)
        return obj

    def all(self, model: type) -> list[Any]:
        return list(self.rows[model])

    def session(self) -> "FakeSession":
        return FakeSession(self)

    def factory(self) -> Any:
        """A `session_factory()` whose sessions are async context managers."""
        db = self

        class _Ctx:
            async def __aenter__(self) -> "FakeSession":
                return db.session()

            async def __aexit__(self, *exc: Any) -> None:
                return None

        return lambda: _Ctx()

    # ------------------------------------------------------------ queries

    def execute(self, stmt: Any) -> Result:
        kind = stmt.__visit_name__
        if kind == "textclause":  # raw SQL (the aged-handoff scrub): nothing to model
            return Result([], scalar=True, rowcount=0)
        if kind == "update":
            model = MODELS[stmt.table.name]
            values = {c.key if hasattr(c, "key") else c: _value(v) for c, v in stmt._values.items()}
            hit = [
                r for r in self.rows[model] if matches(stmt.whereclause, {model.__tablename__: r})
            ]
            for row in hit:
                for key, value in values.items():
                    setattr(row, key, value)
            returning = getattr(stmt, "_returning", ()) or ()
            if returning:  # COB1: UPDATE ... RETURNING
                rows = [tuple(getattr(r, c.key) for c in returning) for r in hit]
                return Result(rows, scalar=False, rowcount=len(hit))
            return Result([], scalar=True, rowcount=len(hit))
        if kind == "delete":
            return self._delete(stmt)
        if kind == "insert":  # BIL1: INSERT ... ON CONFLICT (cols) DO NOTHING
            model = MODELS[stmt.table.name]
            values = {c.key: _value(v) for c, v in stmt._values.items()}
            clause = getattr(stmt, "_post_values_clause", None)
            cols = list(getattr(clause, "inferred_target_elements", None) or [])
            if cols and any(
                all(getattr(r, str(c)) == values.get(str(c)) for c in cols)
                for r in self.rows[model]
            ):
                return Result([], scalar=True, rowcount=0)
            self.add(model(**values))
            return Result([], scalar=True, rowcount=1)
        if kind != "select":
            raise NotImplementedError(kind)

        descriptions = stmt.column_descriptions
        entities = [d["entity"] for d in descriptions]
        if entities[0] is None and descriptions[0]["name"] == "count":  # SELECT count(*) FROM t
            table = stmt.get_final_froms()[0]
            model = MODELS[table.name]
            hits = [r for r in self.rows[model] if matches(stmt.whereclause, {table.name: r})]
            if len(descriptions) > 1:  # count(*) beside coalesce(sum(col), 0) aggregates
                return Result([(len(hits), *_sums(descriptions[1:], hits))], scalar=False)
            return Result([(len(hits),)], scalar=False)
        if UserSession in entities and User in entities:
            return self._session_row(stmt)
        if entities == [TenantMember, Tenant]:
            pairs = []
            for m in self.rows[TenantMember]:
                t = next((t for t in self.rows[Tenant] if t.id == m.tenant_id), None)
                if t is not None and matches(stmt.whereclause, {"tenant_members": m, "tenants": t}):
                    pairs.append((m, t))
            pairs.sort(key=lambda p: (p[0].created_at, str(p[0].tenant_id)))
            return Result(pairs, scalar=False)

        froms = stmt.get_final_froms()
        if len(froms) == 1 and isinstance(froms[0], Join):
            return self._joined(stmt, froms[0])

        model = entities[0]
        table = model.__tablename__
        found = [r for r in self.rows[model] if matches(stmt.whereclause, {table: r})]
        for clause in reversed(list(stmt._order_by_clauses)):
            desc = isinstance(clause, UnaryExpression) and clause.modifier is operators.desc_op
            column = clause.element if isinstance(clause, UnaryExpression) else clause
            found.sort(key=lambda r, k=column.key: getattr(r, k), reverse=desc)
        offset = stmt._offset_clause
        if offset is not None:
            found = found[_value(offset) :]
        limit = stmt._limit_clause
        if limit is not None:
            found = found[: _value(limit)]
        whole_entity = descriptions[0]["expr"] is model
        if whole_entity and len(descriptions) == 1:
            return Result(found, scalar=True)
        return Result(
            [tuple(getattr(r, d["name"]) for d in descriptions) for r in found], scalar=False
        )

    def _joined(self, stmt: Any, join: Any) -> Result:
        """A join select of columns or whole entities (no ordering or limits). Inner and left
        outer joins (PIP3: a business with no `tenant_pipeline_settings` row still has a row)."""
        combos: list[dict[str, Any]] = [{}]
        for table, on, outer in _join_steps(join):
            model = MODELS[table.name]
            grown: list[dict[str, Any]] = []
            for combo in combos:
                hits = [
                    r
                    for r in self.rows[model]
                    if on is None or matches(on, {**combo, table.name: r})
                ]
                if hits:
                    grown += [{**combo, table.name: r} for r in hits]
                elif outer:
                    grown.append({**combo, table.name: None})
            combos = grown
        out = []
        for rows in combos:
            if matches(stmt.whereclause, rows):
                picked = []
                for d in stmt.column_descriptions:
                    expr, entity = d["expr"], d["entity"]
                    if expr is entity:
                        picked.append(rows[entity.__tablename__])
                    else:
                        row = rows[expr.table.name]
                        picked.append(None if row is None else getattr(row, expr.key))
                out.append(tuple(picked))
        return Result(out, scalar=False)

    def _delete(self, stmt: Any) -> Result:
        """DELETE with the cascades the real schema has, and RETURNING."""
        model = MODELS[stmt.table.name]
        hit = [r for r in self.rows[model] if matches(stmt.whereclause, {model.__tablename__: r})]
        for row in hit:
            self._remove(row)
        returning = getattr(stmt, "_returning", ()) or ()
        rows = [tuple(getattr(r, c.key) for c in returning) for r in hit]
        return Result(rows, scalar=False, rowcount=len(hit))

    def _remove(self, row: Any) -> None:
        if isinstance(row, Plan) and any(t.plan_id == row.id for t in self.rows[Tenant]):
            raise RestrictViolationError("tenants.plan_id is RESTRICT")
        if isinstance(row, Tenant):
            if any(c.tenant_id == row.id for c in self.rows[Corpus]):
                raise RestrictViolationError("corpora.tenant_id is RESTRICT")
            self.rows[TenantMember] = [m for m in self.rows[TenantMember] if m.tenant_id != row.id]
            self.rows[Invitation] = [i for i in self.rows[Invitation] if i.tenant_id != row.id]
            self.rows[ApiKey] = [k for k in self.rows[ApiKey] if k.tenant_id != row.id]
            for s in self.rows[UserSession]:
                if s.tenant_id == row.id:
                    s.tenant_id = None
        if isinstance(row, Conversation):  # COB2: messages cascade
            self.rows[Message] = [m for m in self.rows[Message] if m.conversation_id != row.id]
        if isinstance(row, Corpus):
            self.rows[ApiKey] = [k for k in self.rows[ApiKey] if k.corpus_id != row.id]
            for model in (ChannelEvent, ChannelThread, ChannelConnection):  # TV6: CASCADE
                self.rows[model] = [r for r in self.rows[model] if r.corpus_id != row.id]
        if isinstance(row, User):
            for m in (TenantMember, UserSession, EmailCode):
                self.rows[m] = [r for r in self.rows[m] if r.user_id != row.id]
            for t in self.rows[Tenant]:
                if t.created_by_user_id == row.id:
                    t.created_by_user_id = None
            for i in self.rows[Invitation]:
                if i.invited_by_user_id == row.id:
                    i.invited_by_user_id = None
                if i.accepted_by_user_id == row.id:
                    i.accepted_by_user_id = None
        self.rows[type(row)].remove(row)

    def _session_row(self, stmt: Any) -> Result:
        """The middleware's session + user + membership role + tenant join."""
        where = {c.left.key: _value(c.right) for c in stmt.whereclause.clauses}
        sid, uid = where["id"], where["user_id"]
        params = stmt.compile().params  # the ON clauses carry the token's tenant id
        tid = next((v for k, v in params.items() if k.startswith("tenant_id")), None)
        session = next(
            (s for s in self.rows[UserSession] if s.id == sid and s.user_id == uid), None
        )
        if session is None:
            return Result([], scalar=False)
        user = next(u for u in self.rows[User] if u.id == uid)
        member = next(
            (m for m in self.rows[TenantMember] if m.user_id == uid and m.tenant_id == tid), None
        )
        tenant = next((t for t in self.rows[Tenant] if t.id == tid), None)
        plan = next(
            (p for p in self.rows[Plan] if tenant is not None and p.id == tenant.plan_id), None
        )
        features = plan.dashboard_features if plan is not None else None
        return Result(
            [(session, user, member.role if member else None, tenant, features)], scalar=False
        )


# P1 payments
class _Savepoint:
    """`session.begin_nested()` in memory: nothing to roll back to, nothing to release."""

    async def __aenter__(self) -> "_Savepoint":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None


# /P1 payments


class FakeSession:
    def __init__(self, db: FakeDB) -> None:
        self.db = db

    def begin_nested(self) -> _Savepoint:  # P1 payments
        return _Savepoint()

    async def execute(self, stmt: Any, params: Any = None) -> Result:
        if params is not None:  # a raw advisory lock: nothing to take in memory
            return Result([], scalar=False)
        return self.db.execute(stmt)

    async def get(self, model: type, key: Any) -> Any:
        return next((r for r in self.db.rows[model] if getattr(r, "id", None) == key), None)

    def add(self, obj: Any) -> None:
        self.db.add(obj)

    async def delete(self, obj: Any) -> None:
        self.db._remove(obj)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.db.commits += 1

    async def rollback(self) -> None:
        self.db.rollbacks += 1

    async def refresh(self, obj: Any) -> None:
        return None

    def expunge(self, obj: Any) -> None:
        return None

    async def close(self) -> None:
        return None


class FakeRedis:
    """INCR / EXPIRE NX / TTL / SET EX / DELETE with a movable clock."""

    def __init__(self) -> None:
        self.values: dict[str, int | str] = {}
        self.expiry: dict[str, float] = {}
        self.now = 1_000_000.0
        self.down = False

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def _check(self) -> None:
        if self.down:
            raise ConnectionError("redis is down")
        for key in [k for k, t in self.expiry.items() if t <= self.now]:
            self.values.pop(key, None)
            self.expiry.pop(key, None)

    async def incr(self, key: str) -> int:
        self._check()
        self.values[key] = int(self.values.get(key, 0)) + 1
        return int(self.values[key])

    async def decr(self, key: str) -> int:  # L5a demo: a refused answer gives its count back
        self._check()
        self.values[key] = int(self.values.get(key, 0)) - 1
        return int(self.values[key])

    async def expire(self, key: str, seconds: int, nx: bool = False) -> bool:
        self._check()
        if nx and key in self.expiry:
            return False
        self.expiry[key] = self.now + seconds
        return True

    async def ttl(self, key: str) -> int:
        self._check()
        if key not in self.values:
            return -2
        if key not in self.expiry:
            return -1
        return int(self.expiry[key] - self.now)

    async def incrby(self, key: str, amount: int) -> int:  # BIZ2: push limits count URLs
        self._check()
        self.values[key] = int(self.values.get(key, 0)) + amount
        return int(self.values[key])

    async def set(
        self, key: str, value: str, ex: int | None = None, nx: bool = False
    ) -> bool | None:
        self._check()
        if nx and key in self.values:
            return None  # BIZ2: SET NX on a key that exists answers nil
        self.values[key] = value
        if ex is not None:
            self.expiry[key] = self.now + ex
        return True

    async def delete(self, *keys: str) -> int:
        self._check()
        n = 0
        for key in keys:
            n += int(self.values.pop(key, None) is not None)
            self.expiry.pop(key, None)
        return n

    # PIP1 lane
    async def mget(self, *keys: str) -> list[Any]:  # PIP3: the question windows
        self._check()
        return [self.values.get(key) for key in keys]

    async def get(self, key: str) -> Any:
        self._check()
        return self.values.get(key)

    async def eval(self, script: str, numkeys: int, *args: Any) -> int:
        """Only the two answer-lane scripts: delete / extend a key while it holds our token."""
        from callobuzz_rag.tiers.lanes import REFRESH_SCRIPT, RELEASE_SCRIPT

        self._check()
        key, token = args[0], args[1]
        if self.values.get(key) != token:
            return 0
        if script == RELEASE_SCRIPT:
            self.values.pop(key, None)
            self.expiry.pop(key, None)
            return 1
        if script == REFRESH_SCRIPT:
            self.expiry[key] = self.now + int(args[2])
            return 1
        raise NotImplementedError("FakeRedis.eval only knows the answer-lane scripts")

    # /PIP1 lane

    async def aclose(self) -> None:
        return None

    def pipeline(self) -> "_Pipeline":
        return _Pipeline(self)


class _Pipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self.redis = redis
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        def queue(*args: Any, **kwargs: Any) -> "_Pipeline":
            self.calls.append((name, args, kwargs))
            return self

        return queue

    async def execute(self) -> list[Any]:
        return [await getattr(self.redis, n)(*a, **k) for n, a, k in self.calls]


def request_stub(settings: Any, *, ip: str = "203.0.113.9", user_agent: str = "pytest") -> Any:
    """A Request-like object for the service functions."""
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=settings)),
        headers={"user-agent": user_agent},
        client=SimpleNamespace(host=ip),
        state=SimpleNamespace(),
    )
