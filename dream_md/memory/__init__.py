"""Memory sub-domain (docs/DOMAIN.md): the store.

SQLite schema, connections, and (from M3) FTS5 lookup, Fact lifecycle,
and dedupe/conflict bookkeeping. This package owns ALL DDL for the
per-project store (review R7): no other module may define tables.
"""

from dream_md.memory.schema import (
    SCHEMA_VERSION,
    SchemaVersionError,
    connect,
    create_all,
    current_version,
    migrate,
    table_exists,
)

__all__ = [
    "SCHEMA_VERSION",
    "SchemaVersionError",
    "connect",
    "create_all",
    "current_version",
    "migrate",
    "table_exists",
]
