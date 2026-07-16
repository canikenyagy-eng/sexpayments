"""Shared inspector-based guards for Alembic migrations.

Every CREATE/ADD/ALTER step in a migration should be wrapped in a guard
so the migration is re-runnable on a partially-applied schema. This is
especially important when a later migration accidentally overlaps with
an earlier one. Wrapping with these
helpers makes those overlaps no-ops instead of crashes.

"""
import sqlalchemy as sa


def has_table(bind, name: str) -> bool:
    return name in sa.inspect(bind).get_table_names()


def has_column(bind, table: str, column: str) -> bool:
    if not has_table(bind, table):
        return False
    return any(c["name"] == column for c in sa.inspect(bind).get_columns(table))


def has_index(bind, table: str, name: str) -> bool:
    if not has_table(bind, table):
        return False
    return any(i["name"] == name for i in sa.inspect(bind).get_indexes(table))


def has_unique_constraint(bind, table: str, name: str) -> bool:
    if not has_table(bind, table):
        return False
    return any(
        c["name"] == name for c in sa.inspect(bind).get_unique_constraints(table)
    )


def has_foreign_key(bind, table: str, name: str) -> bool:
    if not has_table(bind, table):
        return False
    return any(c["name"] == name for c in sa.inspect(bind).get_foreign_keys(table))


def has_enum_value(bind, enum_name: str, value: str) -> bool:
    """Check whether ``value`` already exists in the PostgreSQL ENUM type.

    Returns False if the enum type itself doesn't exist — caller must
    create the type separately if needed (or use ``sa.Enum(..., create_type=True)``).
    """
    row = bind.execute(
        sa.text(
            """
            SELECT 1
            FROM pg_enum e
            JOIN pg_type t ON t.oid = e.enumtypid
            WHERE t.typname = :enum AND e.enumlabel = :val
            """
        ),
        {"enum": enum_name, "val": value},
    ).first()
    return row is not None


def enum_exists(bind, enum_name: str) -> bool:
    row = bind.execute(
        sa.text("SELECT 1 FROM pg_type WHERE typname = :n"),
        {"n": enum_name},
    ).first()
    return row is not None
