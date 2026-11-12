"""Runtime engine settings the operator edits: sign-up mode and branding.

Exactly one row (`id = 1`, enforced by a check constraint). `value` holds the
serialized `accounts.settings_store.EngineSettings`; a key missing from it means
the default, so adding a setting later needs no data migration. Infrastructure
stays in env (`RAG_*`); this is only what the operator changes at runtime.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, SmallInteger, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base


class EngineSettingsRow(Base):
    __tablename__ = "engine_settings"
    __table_args__ = (CheckConstraint("id = 1", name="ck_engine_settings_single_row"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    value: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    # Who last changed it: "admin key" or the super admin's e-mail.
    updated_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    # True once the operator deliberately deleted their own workspace: bootstrap then no
    # longer recreates it at every start (`--restore-operator-workspace` clears it).
    operator_workspace_removed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
