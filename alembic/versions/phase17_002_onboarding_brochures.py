"""Onboarding gets a brochure step: four steps instead of three (TV5, phase17_002).

Revision ID: phase17_002
Revises: phase17_001
Create Date: 2026-10-05

The first-run wizard was website (1), install (2), try (3). "Add your brochures, price lists and
menus" now sits between the website and the install, so the steps are website (1), brochures (2),
install (3), try (4). An owner who was on "install" must still be on "install" after the upgrade,
so every stored step from 2 up moves one place on; step 1 stays. The check allows 1 to 4.

This ships together with the wizard code that reads the new numbering (TV5 Task 4), so no
version of the engine ever reads the steps with the wrong meaning.

Downgrade folds the steps back: brochures (2) and install (3) both become install (2), try (4)
becomes try (3). A workspace on brochures simply lands on install, which is where it would be
without the step.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "phase17_002"
down_revision: str | None = "phase17_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECK = "ck_onboarding_states_step"


def upgrade() -> None:
    op.drop_constraint(_CHECK, "onboarding_states", type_="check")
    op.execute("UPDATE onboarding_states SET step = step + 1 WHERE step >= 2")
    op.create_check_constraint(_CHECK, "onboarding_states", "step between 1 and 4")


def downgrade() -> None:
    op.drop_constraint(_CHECK, "onboarding_states", type_="check")
    op.execute("UPDATE onboarding_states SET step = step - 1 WHERE step >= 3")
    op.create_check_constraint(_CHECK, "onboarding_states", "step between 1 and 3")
