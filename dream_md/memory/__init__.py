"""Memory sub-domain (docs/DOMAIN.md): the store.

SQLite schema, connections, and (M3) FTS5 lookup, Fact lifecycle, and
dedupe/conflict bookkeeping. This package owns ALL DDL for the
per-project store (review R7): no other module may define tables.
"""

from dream_md.memory.facts import (
    STATUS_ACTIVE,
    STATUS_ASK,
    STATUS_RETIRED,
    STATUS_SUPERSEDED,
    Fact,
    active_facts,
    add_fact,
    add_link,
    bump_ask,
    bump_support,
    claim_hash,
    claim_tokens,
    find_code_duplicate,
    finish_run,
    get_fact,
    jaccard,
    mark_ask,
    normalize_claim,
    record_judgment,
    retrieve_similar,
    retire,
    start_run,
    supersede,
)
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
    "STATUS_ACTIVE",
    "STATUS_ASK",
    "STATUS_RETIRED",
    "STATUS_SUPERSEDED",
    "Fact",
    "active_facts",
    "add_fact",
    "add_link",
    "bump_ask",
    "bump_support",
    "claim_hash",
    "claim_tokens",
    "connect",
    "create_all",
    "current_version",
    "find_code_duplicate",
    "finish_run",
    "get_fact",
    "jaccard",
    "mark_ask",
    "migrate",
    "normalize_claim",
    "record_judgment",
    "retrieve_similar",
    "retire",
    "start_run",
    "supersede",
    "table_exists",
]
