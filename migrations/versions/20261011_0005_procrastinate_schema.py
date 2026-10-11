"""procrastinate schema

The task queue's own schema (D28, D78), vendored from the locked Procrastinate version so a
fresh database always gets the same objects. Upgrading Procrastinate means adding a revision
that runs the SQL migrations it ships for the versions in between.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-11 05:10:00
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op
from sqlalchemy.util import await_only

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = Path(__file__).parent.parent / "sql" / "procrastinate_3.10.0_schema.sql"


def _run_script(sql: str) -> None:
    """Run a multi-statement script: asyncpg's simple-query `execute` (SQLAlchemy would
    prepare it, which allows only one statement)."""
    driver = op.get_bind().connection.dbapi_connection.driver_connection  # type: ignore[union-attr]
    await_only(driver.execute(sql))


def upgrade() -> None:
    _run_script(SCHEMA.read_text())


def downgrade() -> None:
    _run_script(
        """
        DO $$
        DECLARE r record;
        BEGIN
            FOR r IN SELECT tablename FROM pg_tables
                     WHERE schemaname = current_schema() AND tablename LIKE 'procrastinate%' LOOP
                EXECUTE format('DROP TABLE IF EXISTS %I CASCADE', r.tablename);
            END LOOP;
            FOR r IN SELECT p.oid::regprocedure AS sig FROM pg_proc p
                     JOIN pg_namespace n ON n.oid = p.pronamespace
                     WHERE n.nspname = current_schema() AND p.proname LIKE 'procrastinate%' LOOP
                EXECUTE format('DROP FUNCTION IF EXISTS %s CASCADE', r.sig);
            END LOOP;
            FOR r IN SELECT t.typname FROM pg_type t
                     JOIN pg_namespace n ON n.oid = t.typnamespace
                     WHERE n.nspname = current_schema() AND t.typname LIKE 'procrastinate%'
                       AND t.typtype IN ('e', 'c') LOOP
                EXECUTE format('DROP TYPE IF EXISTS %I CASCADE', r.typname);
            END LOOP;
        END $$;
        """
    )
