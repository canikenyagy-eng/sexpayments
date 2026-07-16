"""widen platform_settings.description to Text

Revision ID: 069
Revises: 068
Create Date: 2026-07-06

``platform_settings.description`` was ``VARCHAR(512)``. The ``achievement_rules``
help text is 567 chars, so ``SettingsService.set('achievement_rules', ...)`` failed
on Postgres with "value too long for type character varying(512)" (the whole
achievements admin PATCH 500'd). SQLite ignores the length cap, so the unit/
integration suite never caught it.

Widen to ``Text`` — consistent with the already-``Text`` ``value`` column; a
free-text help field should not be length-capped. PG-only DDL; SQLite (tests)
builds the table from the model, which now declares ``Text``.
"""
import sqlalchemy as sa
from alembic import op

revision = "069"
down_revision = "068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.alter_column(
        "platform_settings",
        "description",
        existing_type=sa.String(length=512),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.alter_column(
        "platform_settings",
        "description",
        existing_type=sa.Text(),
        type_=sa.String(length=512),
        existing_nullable=True,
    )
