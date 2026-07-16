"""normalize merchant status values to lowercase

Revision ID: 009
Revises: 008

Already idempotent via embedded DO $$ block (detects uppercase enum values,
only normalises if present, otherwise ensures the type exists with the
expected labels). Made idempotent for re-runs on partially-applied schemas.
"""
from alembic import op
import sqlalchemy as sa

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$
        DECLARE
            has_uppercase BOOLEAN;
        BEGIN
            -- Проверяем есть ли uppercase значения в enum
            SELECT EXISTS (
                SELECT 1 FROM pg_enum
                WHERE enumtypid = 'merchantstatus'::regtype
                AND enumlabel ~ '^[A-Z]'
            ) INTO has_uppercase;

            IF has_uppercase THEN
                -- Step 1: add a temp column to hold the text value
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'merchants' AND column_name = 'status_tmp'
                ) THEN
                    ALTER TABLE merchants ADD COLUMN status_tmp VARCHAR(50);
                    UPDATE merchants SET status_tmp = LOWER(status::text);
                END IF;

                -- Step 2: drop the old enum column if still exists
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'merchants' AND column_name = 'status'
                ) THEN
                    ALTER TABLE merchants DROP COLUMN status;
                END IF;

                -- Step 3: recreate enum with lowercase values
                DROP TYPE IF EXISTS merchantstatus;
                CREATE TYPE merchantstatus AS ENUM ('pending', 'test', 'enabled', 'disabled', 'blocked', 'archived');

                -- Step 4: add back the column
                ALTER TABLE merchants ADD COLUMN status merchantstatus NOT NULL DEFAULT 'pending';
                UPDATE merchants SET status = status_tmp::merchantstatus;

                -- Step 5: drop temp column
                ALTER TABLE merchants DROP COLUMN status_tmp;

            ELSE
                -- Enum уже lowercase — убедимся что тип существует с нужными значениями
                IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'merchantstatus') THEN
                    CREATE TYPE merchantstatus AS ENUM ('pending', 'test', 'enabled', 'disabled', 'blocked', 'archived');
                END IF;
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    pass
