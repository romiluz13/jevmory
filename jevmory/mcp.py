"""Agent-agnostic MCP server: read-only memory tools over stdio (stdlib only).

One server, every agent. Claude Code loads it automatically from the
plugin's ``.mcp.json``; Codex CLI and Cursor add it as a plain stdio
MCP server (``python3 -m jevmory.mcp`` or ``jevmory mcp``). Cursor has
no hooks, so this is its only door into a jevmory store; Claude and
Codex get live recall/search on top of their hook-driven ingest.

Design stances (same spine as the rest of jevmory):

- **Read-only and local, always.** The tools read this project's
  SQLite store and never touch the network — grading (distill/audit)
  stays in the CLI where the opt-in gate lives. An MCP client cannot
  make jevmory spend or egress anything.
- **Stdlib only** (mission invariant): the wire protocol is
  line-delimited JSON-RPC 2.0, implemented by hand — no sdk import.
  Logs go to stderr; stdout is protocol only.
- **Project resolution:** the ``project`` tool argument (absolute
  path) wins, then ``$JEVMORY_PROJECT``, then the server process cwd —
  the directory the agent launched it from.

Protocol: MCP over stdio — one JSON-RPC message per line. Methods:
``initialize`` (echoes the client's protocolVersion), ``ping``,
``tools/list``, ``tools/call``; notifications (no ``id``) get no
response; unknown methods get ``-32601``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, TextIO

from . import __version__
from .ingestion.eventlog import project_slug, store_path
from .memory.facts import (
    contradicts_partners,
    get_fact,
    retrieve_similar,
)
from .memory.schema import connect, migrate

# Fallback when the client doesn't name a protocol version (MCP has
# used date strings since 2025-06-18; echoing the client's ask is the
# compatible move either way).
PROTOCOL_VERSION = "2024-11-05"

RECALL_DEFAULT_LIMIT = 5
CLAIM_WIDTH = 240

JSONRPC_PARSE_ERROR = -32700
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_REQUEST = -32600


# --- project + store -----------------------------------------------------------


def resolve_project(raw: str | None) -> str:
    """project arg > $JEVMORY_PROJECT > process cwd (realpath, expanded)."""

    candidate = raw or os.environ.get("JEVMORY_PROJECT") or os.getcwd()
    return os.path.realpath(os.path.expanduser(str(candidate)))


def _open_store(project_dir: str):
    """Read-only connection to the project's store, or None if absent.

    Never migrates and never creates: an MCP client pointing at a
    project with no history gets "no store yet", not a fresh db file.
    """

    path = store_path(project_dir)
    if not path.exists():
        return None
    conn = connect(path)
    migrate(conn)  # forward-compat read: old stores upgrade in place
    return conn


def _vintage(fact) -> str:
    return f"verified {fact.verified_at[:10]}" if fact.verified_at else "unverified"


def _claim_line(fact) -> str:
    claim = fact.claim if len(fact.claim) <= CLAIM_WIDTH else (
        fact.claim[: CLAIM_WIDTH - 1].rstrip() + "…"
    )
    return (
        f"- {claim}  [conf {fact.confidence:.2f} · {fact.category} "
        f"· support {fact.support_count} · said {fact.created_at[:10]} "
        f"· {_vintage(fact)}]"
    )


# --- tools ---------------------------------------------------------------------


def tool_status(args: dict[str, Any]) -> str:
    project = resolve_project(args.get("project"))
    slug = project_slug(project)
    store = store_path(project)
    if not store.exists():
        return (
            f"no store yet for {Path(project).name} — sessions ingested by "
            "the jevmory hooks land here; `jevmory ingest --scan` is the "
            "manual fallback"
        )
    conn = _open_store(project)
    assert conn is not None
    try:
        total, pending = conn.execute(
            "SELECT COUNT(*), SUM(graded_at IS NULL) FROM events WHERE project = ?",
            (slug,),
        ).fetchone()
        lines = [
            f"project: {project}",
            f"store:   {store}",
            f"events:  {total} total, {pending or 0} pending grading",
        ]
        for status, count in conn.execute(
            "SELECT status, COUNT(*) FROM facts WHERE project = ? GROUP BY status",
            (slug,),
        ):
            lines.append(f"facts:   {count:3} {status}")
        row = conn.execute(
            "SELECT kind, started_at, stats, error FROM runs "
            "WHERE project = ? ORDER BY id DESC LIMIT 1",
            (slug,),
        ).fetchone()
        if row:
            kind, started, stats_json, error = row
            stats = json.loads(stats_json) if stats_json else {}
            if error:
                lines.append(f"last run: {kind} at {started} — ERROR {error}")
            else:
                lines.append(
                    f"last run: {kind} at {started} "
                    f"({stats.get('api_calls', 0)} calls, "
                    f"{stats.get('usage', 0)} tokens)"
                )
        return "\n".join(lines)
    finally:
        conn.close()


def tool_recall(args: dict[str, Any]) -> str:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ValueError("query is required (the thing to remember)")
    raw_limit = args.get("limit")
    if raw_limit is None:
        raw_limit = RECALL_DEFAULT_LIMIT
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        raise ValueError("limit must be an integer") from None
    if not 1 <= limit <= 25:
        raise ValueError("limit must be between 1 and 25")
    project = resolve_project(args.get("project"))
    conn = _open_store(project)
    if conn is None:
        return (
            f"no store yet for {Path(project).name} — nothing remembered; "
            "sessions ingested by the jevmory hooks build this store"
        )
    try:
        facts = retrieve_similar(conn, query, k=max(10, limit * 2), keep=limit)
    finally:
        conn.close()
    if not facts:
        return (
            f"nothing similar remembered for {Path(project).name} "
            f"(query: {query!r})"
        )
    return "\n".join(
        [f"remembered facts similar to {query!r} (verbatim quotes):"]
        + [_claim_line(fact) for fact in facts]
    )


def tool_fact(args: dict[str, Any]) -> str:
    try:
        fact_id = int(args["id"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("id is required (the fact's numeric id)") from None
    project = resolve_project(args.get("project"))
    conn = _open_store(project)
    if conn is None:
        return f"no store yet for {Path(project).name}"
    try:
        fact = get_fact(conn, fact_id)
        if fact is None:
            return f"no fact {fact_id} in this project's store"
        lines = [
            f"fact {fact.id}  [{fact.status}]",
            f"  claim:    {fact.claim}",
            f"  category: {fact.category}   significance {fact.significance:.2f}",
            f"  conf {fact.confidence:.2f} · support {fact.support_count} "
            f"· said {fact.created_at[:10]} · {_vintage(fact)}",
        ]
        if fact.context:
            context = fact.context if len(fact.context) <= CLAIM_WIDTH else (
                fact.context[: CLAIM_WIDTH - 1].rstrip() + "…"
            )
            lines.append(f"  context: {context}")
        for partner in contradicts_partners(conn, fact.id):
            lines.append(
                f"  contradicts fact {partner.id}: {partner.claim}"
            )
        return "\n".join(lines)
    finally:
        conn.close()


TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "status",
        "description": (
            "jevmory memory status for the project: store location, "
            "ingested events, facts by status, last grading run. "
            "Read-only and local."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": (
                        "Absolute project path (default: this server's cwd)"
                    ),
                }
            },
        },
    },
    {
        "name": "recall",
        "description": (
            "Search the project's memory: verbatim facts similar to a "
            "query, confidence-ordered, with vintage receipts. Use this "
            "before re-deriving project conventions. Read-only and local."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to remember (words from the topic)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max facts to return (1-25, default 5)",
                },
                "project": {
                    "type": "string",
                    "description": (
                        "Absolute project path (default: this server's cwd)"
                    ),
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "fact",
        "description": (
            "One remembered fact in full: claim, category, confidence, "
            "support, vintage, and the facts it contradicts. "
            "Read-only and local."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "integer",
                    "description": "The fact's numeric id (from recall/status)",
                },
                "project": {
                    "type": "string",
                    "description": (
                        "Absolute project path (default: this server's cwd)"
                    ),
                },
            },
            "required": ["id"],
        },
    },
)

_TOOL_FUNCS = {
    "status": tool_status,
    "recall": tool_recall,
    "fact": tool_fact,
}

_INSTRUCTIONS = (
    "jevmory is this project's local memory: verbatim quotes from past "
    "sessions, graded by calibrated confidence, with receipts. Call "
    "`recall` before re-deriving project conventions or asking the user "
    "again; call `status` to see what has been ingested; call `fact` to "
    "inspect one memory in full. All tools are read-only and fully local."
)


# --- protocol ------------------------------------------------------------------


def _result_content(text: str, *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": text}],
        "isError": is_error,
    }


def dispatch(method: str, params: dict[str, Any]) -> dict[str, Any] | None:
    """One JSON-RPC method -> result object, or None for notifications.

    Raises LookupError for unknown methods (the caller turns that into
    -32601) and ValueError for malformed requests (-32600).
    """

    if method == "initialize":
        requested = params.get("protocolVersion")
        return {
            "protocolVersion": requested or PROTOCOL_VERSION,
            "capabilities": {},
            "serverInfo": {"name": "jevmory", "version": __version__},
            "instructions": _INSTRUCTIONS,
        }
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [dict(tool) for tool in TOOLS]}
    if method == "tools/call":
        name = params.get("name")
        if name not in _TOOL_FUNCS:
            raise ValueError(f"unknown tool {name!r}")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        try:
            text = _TOOL_FUNCS[name](arguments)
        except ValueError as exc:  # bad arguments: tool-level error result
            return _result_content(f"error: {exc}", is_error=True)
        return _result_content(text)
    raise LookupError(method)


def serve(reader: TextIO, writer: TextIO) -> None:
    """Line-delimited JSON-RPC loop. Never raises; logs to stderr."""

    for line in reader:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            _write(
                writer,
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": JSONRPC_PARSE_ERROR,
                        "message": "parse error",
                    },
                },
            )
            continue
        if not isinstance(message, dict) or "method" not in message:
            _write(
                writer,
                {
                    "jsonrpc": "2.0",
                    "id": message.get("id") if isinstance(message, dict) else None,
                    "error": {
                        "code": JSONRPC_INVALID_REQUEST,
                        "message": "not a JSON-RPC request",
                    },
                },
            )
            continue
        method = message["method"]
        request_id = message.get("id")
        params = message.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        try:
            result = dispatch(method, params)
        except LookupError:
            result = None
            _write(
                writer,
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": JSONRPC_METHOD_NOT_FOUND,
                        "message": f"method not found: {method}",
                    },
                },
            )
            continue
        except ValueError as exc:
            _write(
                writer,
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": JSONRPC_INVALID_REQUEST, "message": str(exc)},
                },
            )
            continue
        except Exception as exc:  # noqa: BLE001 - protocol survives tool bugs
            print(f"jevmory-mcp: {method} failed: {exc}", file=sys.stderr)
            if request_id is not None:
                _write(
                    writer,
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32603, "message": "internal error"},
                    },
                )
            continue
        if request_id is None:
            continue  # notification: no response, by protocol
        _write(writer, {"jsonrpc": "2.0", "id": request_id, "result": result})


def _write(writer: TextIO, payload: dict[str, Any]) -> None:
    writer.write(json.dumps(payload, separators=(",", ":")) + "\n")
    writer.flush()


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - stdio wiring
    try:
        serve(sys.stdin, sys.stdout)
    except BrokenPipeError:  # client went away: exit quietly
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
