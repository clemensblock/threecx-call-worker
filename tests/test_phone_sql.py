"""Parity test: Python normalize_phone() vs Postgres normalize_de_phone().

This test requires a running PostgreSQL instance. It loads the migration SQL,
creates the normalize_de_phone function, and compares outputs for 30 inputs.

When testcontainers is available, it will use a disposable Postgres container.
Otherwise, set DATABASE_URL env var to use an existing Postgres instance.

Skip with: pytest -k "not sql_parity"
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.test_phone import PARITY_TEST_INPUTS
from worker.phone import normalize_phone

MIGRATION_FILE = Path(__file__).parent.parent / "migrations" / "001_inbound_support.sql"


def _get_function_sql() -> str:
    """Extract just the CREATE FUNCTION statement from the migration."""
    full_sql = MIGRATION_FILE.read_text()
    start = full_sql.index("CREATE OR REPLACE FUNCTION normalize_de_phone")
    end = full_sql.index("$$;", start) + 3
    return full_sql[start:end]


def _try_testcontainers():
    """Try to start a PostgreSQL testcontainer."""
    try:
        from testcontainers.postgres import PostgresContainer

        container = PostgresContainer("postgres:16-alpine")
        container.start()
        return container
    except ImportError:
        return None
    except Exception:
        return None


@pytest.fixture(scope="module")
def pg_connection():
    """Provide a psycopg2 connection to a PostgreSQL database."""
    container = None
    conn_url = os.environ.get("DATABASE_URL")

    if not conn_url:
        container = _try_testcontainers()
        if container is None:
            pytest.skip("No DATABASE_URL and testcontainers not available")
        conn_url = container.get_connection_url()

    try:
        import psycopg2

        conn = psycopg2.connect(conn_url)
        conn.autocommit = True

        with conn.cursor() as cur:
            cur.execute(_get_function_sql())

        yield conn

        conn.close()
    except ImportError:
        pytest.skip("psycopg2 not installed")
    finally:
        if container:
            container.stop()


@pytest.mark.parametrize("raw_input", PARITY_TEST_INPUTS)
def test_sql_parity(pg_connection, raw_input: str) -> None:
    """Python normalize_phone and SQL normalize_de_phone must produce identical output."""
    python_result = normalize_phone(raw_input)

    with pg_connection.cursor() as cur:
        cur.execute("SELECT normalize_de_phone(%s)", (raw_input,))
        sql_result = cur.fetchone()[0]

    assert python_result == sql_result, (
        f"Mismatch for input {raw_input!r}: Python={python_result!r}, SQL={sql_result!r}"
    )
