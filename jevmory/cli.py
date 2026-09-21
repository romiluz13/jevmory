"""jevmory command line entry point: the M6 wiring surface.

    jevmory init [--enable-grading] [--project DIR]
    jevmory ingest --transcript PATH | --scan [--project DIR] [--test]
    jevmory distill   [--project DIR] [--offline] [--force]
                      [--backend typesafe|kev]
    jevmory audit   [MEMORY.md] [--project DIR] [--json|--md] [--offline]
                    [--backend typesafe|kev]
    jevmory resolve <fact_id> --keep-new|--keep-old [--project DIR]
    jevmory status  [--project DIR]
    jevmory install --agent claude|codex [--project DIR] [--yes]

Privacy by architecture (PLAN #4) is enforced here, at the boundary:

- ingest / scan / status / install / hooks are local-only, always;
- grading — distill or audit with a LIVE client — requires a
  per-project opt-in marker (`init --enable-grading`). The default
  backend (TypeSafe Jev, api.typesafe.ai) also requires
  ``$TYPESAFE_API_KEY``;
- ``--backend kev`` points grading at a LOCAL wire-compatible server
  (jaredpalmer/kev, ``$JEVMORY_KEV_ENDPOINT``, default
  ``http://127.0.0.1:8009``) — no key, no external egress; the marker
  is still required, and receipts are experimental (see
  ``jevmory/judgment/client.py``);
- ``--offline`` swaps in FakeJev (simulated, decisive) so the whole
  pipeline runs with zero network and no marker: the opt-in gate
  guards API egress, not local computation.

Exit codes: 0 success (including "nothing to do"), 1 clean failure
(bad args print usage via argparse and exit 2). The hook entry point
(``python3 -m jevmory.hook``) is NOT this module: it never exits
nonzero, whatever happens.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from jevmory import __version__
from jevmory.audit.engine import run_audit
from jevmory.audit.report import render_json, render_md, render_terminal
from jevmory.distill.engine import GradingNotEnabledError, run_distill
from jevmory.distill.resolve import KEEP_NEW, KEEP_OLD, resolve
from jevmory.distill.writer import (
    SENTINEL_CORE,
    SentinelError,
    render_jevmory,
    write_jevmory,
)
from jevmory.hook import (
    extract_payload,
    global_log_path,
    log_path,
    run_hook,
)
from jevmory.ingestion.claude import parse_claude_transcript
from jevmory.ingestion.codex import parse_codex_transcript
from jevmory.ingestion.droid import parse_droid_transcript
from jevmory.ingestion.eventlog import (
    EventLog,
    default_home,
    optin_path,
    project_slug,
    store_path,
)
from jevmory.ingestion.extract import candidates_from_statements
from jevmory.ingestion.redact import redactions_in
from jevmory.ingestion.scan import detect_source, discover_transcripts
from jevmory.judgment.client import (
    DEFAULT_ENDPOINT,
    KEV_ENDPOINT,
    KEV_MODEL,
    JevClient,
)
from jevmory.judgment.errors import JevError
from jevmory.judgment.fake import MODE_NORMAL, FakeJev
from jevmory.memory.facts import ask_facts
from jevmory.memory.schema import connect, migrate

# Payload keys the hook recognizes (kept in sync with jevmory.hook).
HOOK_CWD_KEYS = ("cwd", "project_dir", "working_directory", "workspace", "workspace_root")
HOOK_PATH_KEYS = (
    "transcript_path",
    "transcript",
    "path",
    "session_file",
    "rollout_path",
    "file",
)
HOOK_SESSION_KEYS = ("session_id", "session", "sessionId")

_ENABLE_GRADING_HINT = (
    "grading is off for this project — events queue locally forever.\n"
    "  enable:  jevmory init --enable-grading\n"
    "  stay local (no API ever):  jevmory distill --offline"
)
_KEY_HINT = (
    "$TYPESAFE_API_KEY is not set — grading needs it, or use --offline,\n"
    "  or --backend kev (a local wire-compatible server, no key needed)"
)

_BACKENDS = ("typesafe", "kev")


def _backend_choice(raw: str | None) -> str:
    """Resolve the grading backend: --backend wins, else $JEVMORY_BACKEND."""
    backend = raw or os.environ.get("JEVMORY_BACKEND", "typesafe")
    if backend not in _BACKENDS:
        raise ValueError(
            f"unknown backend {backend!r} (use {' or '.join(_BACKENDS)}, "
            "or set $JEVMORY_BACKEND)"
        )
    return backend


def _live_client(backend: str = "typesafe"):
    """The live grading client for a backend, or None when unavailable.

    typesafe: JevClient from $TYPESAFE_API_KEY (None if unset).
    kev: a local wire-compatible server (jaredpalmer/kev) — no key, no
    external egress; the endpoint comes from $JEVMORY_KEV_ENDPOINT
    (default http://127.0.0.1:8009). The opt-in marker is still
    required: grading is grading, wherever the model runs.
    """
    if backend == "kev":
        endpoint = os.environ.get("JEVMORY_KEV_ENDPOINT") or KEV_ENDPOINT
        return JevClient(
            "local", endpoint=endpoint, model=KEV_MODEL
        ), endpoint
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        return None, DEFAULT_ENDPOINT
    return JevClient(key), DEFAULT_ENDPOINT


def _fail(message: str) -> int:
    print(f"jevmory: {message}", file=sys.stderr)
    return 1


def _project_dir(raw: str | None) -> str:
    """Resolve the target project: --project wins, else the cwd."""

    return os.path.realpath(os.path.expanduser(raw or os.getcwd()))


def _open_store(project_dir: str):
    path = store_path(project_dir)
    conn = connect(path)
    migrate(conn)
    return conn


def _offline_client() -> FakeJev:
    # Normal mode: decisive answers, so the offline pipeline produces a
    # real jevmory.md and a real report (the demo of shapes, not of doubt).
    return FakeJev(mode=MODE_NORMAL)


def _parse_transcript(path: str, agent: str | None):
    """Parse one transcript; agent overrides source detection."""

    source = agent or detect_source(path)
    if source == "claude":
        return parse_claude_transcript(path), "claude"
    if source == "codex":
        return parse_codex_transcript(path), "codex"
    if source == "droid":
        return parse_droid_transcript(path), "droid"
    raise ValueError(f"unknown agent {source!r} (use claude, codex or droid)")


# --- init ----------------------------------------------------------------------


def _cmd_init(args, stdin_text: str | None) -> int:
    project = _project_dir(args.project)
    slug = project_slug(project)
    marker = optin_path(project)
    store = store_path(project)

    print(f"project:  {project}")
    print(f"store:    {store}")
    print(f"grading:  {'ENABLED (marker present)' if marker.exists() else 'off'}")

    if args.enable_grading:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
        print(f"marker:   {marker} (created)")
        print(
            "you have opted in: `jevmory distill` and `jevmory audit` may send\n"
            "  redacted candidate text + surrounding context to the TypeSafe Jev\n"
            "  API (api.typesafe.ai) for grading. Remove the marker file any time\n"
            "  to stop all egress; queued events are kept."
        )
    else:
        print(f"marker:   {marker} (absent)")
        print(_ENABLE_GRADING_HINT)

    print(
        "ingest:   jevmory ingest --scan   (finds this project's transcripts\n"
        "          under ~/.claude/projects, ~/.codex/sessions and\n"
        "          ~/.factory/sessions — local only)\n"
        "hooks:    jevmory install --agent claude|codex   (prints config)"
    )
    return 0


# --- ingest --------------------------------------------------------------------


def _cmd_ingest(args, stdin_text: str | None) -> int:
    if args.test:
        return _ingest_test(args, stdin_text)
    if args.scan:
        return _ingest_scan(args)
    if args.transcript:
        return _ingest_one(args)
    return _fail(
        "nothing to ingest: pass --transcript PATH, --scan, or --test "
        "(payload verifier)"
    )


def _ingest_one(args) -> int:
    path = args.transcript
    if not os.path.isfile(path):
        return _fail(f"transcript not found: {path}")
    try:
        parsed, source = _parse_transcript(path, args.agent)
    except ValueError as exc:
        return _fail(str(exc))
    project = args.project and _project_dir(args.project)
    attribution = project or parsed.cwd
    if not attribution:
        return _fail(
            "transcript records no working directory; pass --project DIR"
        )
    result = EventLog.for_project(attribution).append(parsed)
    print(f"source:      {source}")
    print(f"session:     {parsed.session_id or '-'}")
    print(f"project:     {attribution}  (store {project_slug(attribution)}.db)")
    print(f"statements:  {len(parsed.statements)}")
    print(f"inserted:    {result.inserted}  (duplicates skipped: {result.ignored})")
    return 0


def _ingest_scan(args) -> int:
    project = _project_dir(args.project)
    try:
        found = discover_transcripts(project)
    except OSError as exc:
        return _fail(f"scan failed: {exc}")
    if not found:
        print(
            f"no transcripts found for {project}\n"
            "looked in (first 64KB of each *.jsonl, matching the project path):\n"
            f"  {default_home() / '.claude' / 'projects'}\n"
            f"  {default_home() / '.codex' / 'sessions'}\n"
            f"  {default_home() / '.factory' / 'sessions'}"
        )
        return 0
    total_inserted = 0
    total_ignored = 0
    for item in found:
        try:
            parser = {
                "claude": parse_claude_transcript,
                "codex": parse_codex_transcript,
                "droid": parse_droid_transcript,
            }[item.source]
            parsed = parser(item.path)
            result = EventLog.for_project(project).append(parsed)
        except (OSError, ValueError) as exc:
            print(f"  skip {item.path}: {exc}")
            continue
        total_inserted += result.inserted
        total_ignored += result.ignored
        print(
            f"  {item.source:6} {result.inserted:3} new / "
            f"{result.ignored:3} dup  {item.path}"
        )
    print(
        f"transcripts: {len(found)}   inserted: {total_inserted}   "
        f"duplicates: {total_ignored}"
    )
    print("next: jevmory distill --offline   (local simulation, no API)")
    return 0


def _ingest_test(args, stdin_text: str | None) -> int:
    """--test: verify a hook payload / transcript and write NOTHING."""

    if args.transcript:
        return _verify_transcript(args.transcript, args.agent)
    text = stdin_text if stdin_text is not None else _read_stdin()
    if not text:
        return _fail("--test needs a hook payload on stdin or --transcript PATH")
    payload = extract_payload([], text)
    if payload is None:
        return _fail("stdin is not a JSON object — hooks would skip this safely")
    print("payload: OK (JSON object)")
    for label, keys in (
        ("project dir", HOOK_CWD_KEYS),
        ("transcript", HOOK_PATH_KEYS),
        ("session", HOOK_SESSION_KEYS),
    ):
        hit = next((k for k in keys if payload.get(k)), None)
        print(f"  {label:12} {'payload.' + hit if hit else '-'}")
    transcript = next(
        (str(payload[k]) for k in HOOK_PATH_KEYS if payload.get(k)), None
    )
    if transcript and os.path.isfile(transcript):
        return _verify_transcript(transcript, args.agent)
    if transcript:
        return _fail(f"payload transcript does not exist: {transcript}")
    print("  (no transcript in payload — the hook would log a skip; ingest via --scan)")
    return 0


def _verify_transcript(path: str, agent: str | None) -> int:
    try:
        parsed, source = _parse_transcript(path, agent)
    except (OSError, ValueError) as exc:
        return _fail(f"cannot verify {path}: {exc}")
    roles: dict[str, int] = {}
    for statement in parsed.statements:
        roles[statement.role] = roles.get(statement.role, 0) + 1
    candidates = candidates_from_statements(parsed.statements)
    secrets = sum(redactions_in(s.text) for s in parsed.statements)
    attribution = parsed.cwd or "(none — pass --project at ingest)"
    print(f"transcript: OK ({source})")
    print(f"  session:     {parsed.session_id or '-'}")
    print(f"  cwd:         {parsed.cwd or '-'}")
    print(f"  project:     {attribution}")
    print(f"  statements:  {len(parsed.statements)}  {dict(sorted(roles.items()))}")
    print(f"  candidates:  {len(candidates)} (would queue for grading)")
    print(f"  redactions:  {secrets} secret-shaped spans WOULD be redacted at ingest")
    print("  writes:      none (--test never touches a store)")
    return 0


def _read_stdin() -> str:
    try:
        if not sys.stdin.isatty():
            return sys.stdin.read()
    except (OSError, ValueError):
        pass
    return ""


# --- distill ---------------------------------------------------------------------


def _cmd_distill(args, stdin_text: str | None) -> int:
    project = _project_dir(args.project)
    slug = project_slug(project)
    conn = _open_store(project)

    if args.offline:
        client = _offline_client()
        print("offline: simulated grading (FakeJev) — nothing leaves this machine")
    else:
        try:
            backend = _backend_choice(getattr(args, "backend", None))
        except ValueError as exc:
            return _fail(str(exc))
        client, endpoint = _live_client(backend)
        if client is None:
            return _fail(_KEY_HINT)
        if backend == "kev":
            print(
                f"backend: kev — local server {endpoint} "
                "(no key; experimental receipts)"
            )
    try:
        report = run_distill(
            conn,
            project=slug,
            client=client,
            project_dir=project,
            project_context={"name": Path(project).name},
            enforce_optin=args.offline is False,
        )
    except GradingNotEnabledError:
        return _fail(_ENABLE_GRADING_HINT)
    except JevError as exc:
        return _fail(
            f"grading failed: {exc}\n  events stay queued; retry later — "
            "nothing is lost"
        )

    artifact = Path(project) / "jevmory.md"
    try:
        write_jevmory(
            artifact,
            render_jevmory(
                report.facts,
                report.ask_pairs,
                sessions_by_fact=dict(report.sessions_by_fact),
            ),
            force=args.force,
        )
    except SentinelError:
        return _fail(
            f"{artifact} exists without a jevmory sentinel; pass --force to "
            "overwrite it"
        )

    if report.run_id is None:
        print("nothing to grade: no pending events and no open asks")
    print(f"run:          #{report.run_id}" if report.run_id is not None else "run:          -")
    print(f"events:       {report.events_graded} graded, {report.events_deferred} deferred")
    print(f"candidates:   {report.candidates_graded} groups ({report.occurrences} occurrences)")
    print(f"facts:        {len(report.facts_added)} added, {report.duplicates} duplicates")
    if report.superseded:
        print(f"superseded:   {list(report.superseded)}")
    if report.asks:
        print(f"asks:         {list(report.asks)} — resolve with jevmory resolve <id>")
    if report.near_misses:
        print(f"near-misses:  {report.near_misses} (durability 0.5–gate; surfaced, never silent)")
    print(f"api:          {report.api_calls} calls, {report.usage_tokens} tokens (simulated)" if args.offline else f"api:          {report.api_calls} calls, {report.usage_tokens} tokens")
    print(f"jevmory.md:     {artifact}")
    return 0


# --- audit ---------------------------------------------------------------------


def _cmd_audit(args, stdin_text: str | None) -> int:
    project = _project_dir(args.project)
    slug = project_slug(project)
    memory_path = Path(args.memory_file)
    if not memory_path.is_file():
        return _fail(
            f"memory file not found: {memory_path} "
            "(positional argument, e.g. jevmory audit MEMORY.md)"
        )
    text = memory_path.read_text(encoding="utf-8")

    if args.offline:
        client = _offline_client()
        print("offline: simulated grading (FakeJev) — nothing leaves this machine")
    else:
        if not optin_path(project).exists():
            return _fail(_ENABLE_GRADING_HINT)
        try:
            backend = _backend_choice(getattr(args, "backend", None))
        except ValueError as exc:
            return _fail(str(exc))
        client, endpoint = _live_client(backend)
        if client is None:
            return _fail(_KEY_HINT)
        if backend == "kev":
            print(
                f"backend: kev — local server {endpoint} "
                "(no key; experimental receipts)"
            )

    conn = _open_store(project)
    try:
        report = run_audit(
            text,
            conn,
            project=slug,
            client=client,
            memory_file=str(memory_path),
            project_context={"name": Path(project).name},
        )
    except JevError as exc:
        return _fail(f"audit failed: {exc}")

    if args.json:
        print(render_json(report))
    elif args.md:
        print(render_md(report))
    else:
        print(render_terminal(report))
        print(
            "receipts: every number above is reproducible from the judgments "
            "table (--json for the full trail)"
        )
    return 0


# --- resolve -------------------------------------------------------------------


def _cmd_resolve(args, stdin_text: str | None) -> int:
    if (args.keep_new + args.keep_old) != 1:
        return _fail("pick exactly one of --keep-new / --keep-old")
    project = _project_dir(args.project)
    conn = _open_store(project)
    choice = KEEP_NEW if args.keep_new else KEEP_OLD
    try:
        fact = resolve(conn, args.fact_id, choice)
    except ValueError as exc:
        return _fail(str(exc))
    verb = "kept (challenger active, incumbent superseded)" if choice == KEEP_NEW else "resolved keep-old (challenger retired, incumbent untouched)"
    print(f"ask {args.fact_id}: {verb}")
    print(f"  claim:    {fact.claim}")
    print(f"  status:   {fact.status}")
    print("next: jevmory distill   (jevmory.md regenerates with the resolution)")
    return 0


# --- status --------------------------------------------------------------------


def _cmd_status(args, stdin_text: str | None) -> int:
    project = _project_dir(args.project)
    slug = project_slug(project)
    store = store_path(project)
    marker = optin_path(project)
    artifact = Path(project) / "jevmory.md"

    print(f"project:      {project}")
    print(f"store:        {store}")

    if not store.exists():
        print("events:       none yet (no store)")
        print("first step:   jevmory ingest --scan")
        if not marker.exists():
            print("grading:      " + _ENABLE_GRADING_HINT.splitlines()[0])
            print("              jevmory init --enable-grading   jevmory distill --offline")
        return 0

    conn = _open_store(project)
    total, pending = conn.execute(
        "SELECT COUNT(*), SUM(graded_at IS NULL) FROM events WHERE project = ?",
        (slug,),
    ).fetchone()
    print(f"events:       {total} total, {pending or 0} pending grading")
    for status, count in conn.execute(
        "SELECT status, COUNT(*) FROM facts WHERE project = ? GROUP BY status",
        (slug,),
    ):
        print(f"facts:        {count:3} {status}")

    row = conn.execute(
        "SELECT kind, started_at, stats, error FROM runs "
        "WHERE project = ? ORDER BY id DESC LIMIT 1",
        (slug,),
    ).fetchone()
    if row:
        kind, started, stats_json, error = row
        stats = json.loads(stats_json) if stats_json else {}
        if error:
            print(f"last run:     {kind} at {started} — ERROR {error}")
        else:
            print(
                f"last run:     {kind} at {started} "
                f"({stats.get('api_calls', 0)} calls, {stats.get('usage', 0)} tokens)"
            )
        if stats.get("near_misses"):
            print(f"near-misses:  {stats['near_misses']} (durability 0.5–gate; never silent)")
        if stats.get("events_deferred"):
            print(f"deferred:     {stats['events_deferred']} events over the candidate cap")

    for ask in ask_facts(conn, slug):
        print(f"open ask:     #{ask.id} {ask.claim}")
        if ask.ask_seen_count:
            print(f"              seen in {ask.ask_seen_count} distill(s); expires keep-old at 3")
    if marker.exists():
        print("grading:      enabled (opt-in marker present)")
    else:
        print("grading:      off — events queue locally forever, by design")
        print("              jevmory init --enable-grading   jevmory distill --offline")

    if artifact.exists():
        healthy = SENTINEL_CORE in artifact.read_text(encoding="utf-8")
        print(f"jevmory.md:     {artifact} ({'sentinel ok' if healthy else 'NO SENTINEL — not ours'})")
    else:
        print(f"jevmory.md:     not written yet (jevmory distill)")
    _hook_status(project)
    print(
        "privacy:      ingest/scan/status/hooks are always local. Grading sends\n"
        "              redacted candidate text + ≤800-char context to the TypeSafe\n"
        "              Jev API, only when the marker exists and a key is set."
    )
    return 0


def _hook_status(project: str) -> None:
    """Last hook ingest / error from the JSONL status log, if any."""

    entries: list[dict[str, Any]] = []
    for path in (log_path(project), global_log_path()):
        try:
            if path.exists():
                for line in path.read_text(encoding="utf-8").splitlines():
                    try:
                        entries.append(json.loads(line))
                    except ValueError:
                        continue
        except OSError:
            continue
    ingest = next(
        (e for e in reversed(entries) if e.get("event") == "ingest"), None
    )
    error = next(
        (
            e
            for e in reversed(entries)
            if e.get("event") in ("ingest_error", "hook_error")
        ),
        None,
    )
    if ingest:
        print(
            f"last ingest:  {ingest.get('ts')} — {ingest.get('inserted')} events "
            f"from {ingest.get('source')} session {ingest.get('session_id') or '-'}"
        )
    else:
        print("last ingest:  none (hooks not installed or no session ended yet)")
    if error:
        print(f"last error:   {error.get('ts')} — {error.get('error')}")


# --- install -------------------------------------------------------------------


def _claude_settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def _codex_config_path() -> Path:
    return Path.home() / ".codex" / "config.toml"


def _claude_hook_json() -> dict[str, Any]:
    return {
        "hooks": {
            "SessionEnd": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "python3 -m jevmory.hook",
                        }
                    ]
                }
            ]
        }
    }


def _codex_notify_line() -> str:
    return 'notify = ["python3", "-m", "jevmory.hook", "turn-ended"]'


def _cmd_install(args, stdin_text: str | None) -> int:
    project = _project_dir(args.project)
    print(f"installing for project: {project}")
    print(f"  (store {project_slug(project)}.db — attribution follows each "
          "session's own cwd; hooks never guess)")
    if args.agent == "claude":
        return _install_claude(args)
    return _install_codex(args)


def _install_claude(args) -> int:
    snippet = json.dumps(_claude_hook_json(), indent=2) + "\n"
    target = _claude_settings_path()
    print(f"\nadd to {target} (\"hooks\" merges with any you already have):")
    print(snippet, end="")
    print(
        "what it does: Claude Code pipes the SessionEnd payload (session_id,\n"
        "transcript_path, cwd) to the hook on stdin; jevmory ingests that\n"
        "transcript into this project's local store and always exits 0."
    )
    if not args.yes:
        print("(print-only: jevmory never patches agent config without --yes)")
        return 0
    return _patch_claude_settings()


def _patch_claude_settings() -> int:
    target = _claude_settings_path()
    try:
        if target.exists():
            data = json.loads(target.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return _fail(f"{target} is not a JSON object; merge by hand")
        else:
            data = {}
        hooks = data.get("hooks")
        if hooks is None:
            hooks = {}
        if not isinstance(hooks, dict):
            return _fail(f"{target}: \"hooks\" is not an object; merge by hand")
        session_end = hooks.get("SessionEnd")
        if session_end is None:
            session_end = []
        if not isinstance(session_end, list):
            return _fail(f"{target}: hooks.SessionEnd is not a list; merge by hand")
        if any(
            "jevmory.hook" in json.dumps(entry) for entry in session_end
        ):
            print(f"already installed: {target}")
            return 0
        session_end.extend(_claude_hook_json()["hooks"]["SessionEnd"])
        hooks["SessionEnd"] = session_end
        data["hooks"] = hooks
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError) as exc:
        return _fail(f"cannot patch {target}: {exc}; merge by hand")
    print(f"patched: {target}")
    return 0


def _install_codex(args) -> int:
    target = _codex_config_path()
    print(f"\nadd to {target}:")
    print("[features]")
    print("hooks = true")
    print()
    print(_codex_notify_line())
    print(
        "what it does: Codex runs the notify command with the turn payload as\n"
        "argv after each turn; jevmory scans argv (and stdin) for the payload,\n"
        "ingests the transcript it names into this project's local store, and\n"
        "always exits 0. If a payload shape ever changes, `jevmory ingest\n"
        "--scan` is the guaranteed fallback."
    )
    if not args.yes:
        print("(print-only: jevmory never patches agent config without --yes)")
        return 0
    return _patch_codex_config()


def _patch_codex_config() -> int:
    """Surgical, line-based patch: set/insert notify, ensure hooks=true.

    Refuses nothing a hand edit wouldn't fix; worst case the user
    merges the printed snippet by hand (the default mode).
    """

    target = _codex_config_path()
    notify = _codex_notify_line()
    try:
        lines = (
            target.read_text(encoding="utf-8").splitlines()
            if target.exists()
            else []
        )
        # notify: TOML top-level keys must precede [sections] — both the
        # search for an existing assignment AND the insertion point stay
        # above the first section header. A `notify =` inside a [section]
        # is somebody else's setting: never rewritten, never hijacked
        # (T5); instead a fresh top-level notify is inserted.
        first_section = next(
            (i for i, l in enumerate(lines) if re.match(r"\s*\[", l)), None
        )
        top_level = first_section if first_section is not None else len(lines)
        notify_at = next(
            (
                i
                for i in range(top_level)
                if re.match(r"\s*notify\s*=", lines[i])
            ),
            None,
        )
        if notify_at is not None:
            lines[notify_at] = notify
        else:
            lines[top_level:top_level] = [notify, ""]

        # [features] hooks = true
        features_at = next(
            (i for i, l in enumerate(lines) if re.match(r"\[features\]\s*$", l)),
            None,
        )
        if features_at is None:
            if lines and lines[-1].strip():
                lines.append("")
            lines.extend(["[features]", "hooks = true"])
        else:
            end = next(
                (
                    i
                    for i in range(features_at + 1, len(lines))
                    if re.match(r"\s*\[", lines[i])
                ),
                len(lines),
            )
            hooks_at = next(
                (
                    i
                    for i in range(features_at + 1, end)
                    if re.match(r"\s*hooks\s*=", lines[i])
                ),
                None,
            )
            if hooks_at is not None:
                lines[hooks_at] = "hooks = true"
            else:
                lines[features_at + 1 : features_at + 1] = ["hooks = true"]

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        return _fail(f"cannot patch {target}: {exc}; merge by hand")
    print(f"patched: {target}")
    return 0


# --- hook passthrough ----------------------------------------------------------

def _cmd_hook(args, stdin_text: str | None) -> int:
    """CLI alias for the module entry: same never-exit-nonzero contract.

    When main() is called without captured stdin text, read the real
    stdin here — a piped payload must reach the hook exactly as it does
    through ``python3 -m jevmory.hook`` (T3: the alias used to pass
    None through and silently skip).
    """

    text = stdin_text if stdin_text is not None else _read_stdin()
    return run_hook(getattr(args, "hook_args", None) or [], text)


# --- parser --------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jevmory",
        description=(
            "Coding-agent memory with receipts: Jev-graded verbatim facts "
            "from session transcripts."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"jevmory {__version__}"
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("init", help="per-project setup; grading opt-in marker")
    p.add_argument("--enable-grading", action="store_true")
    p.add_argument("--project", metavar="DIR")
    p.set_defaults(func=_cmd_init)

    p = sub.add_parser(
        "ingest", help="transcript -> local store (never the network)"
    )
    p.add_argument("--transcript", metavar="PATH")
    p.add_argument(
        "--scan",
        action="store_true",
        help="discover this project's transcripts under the agent roots",
    )
    p.add_argument(
        "--test",
        action="store_true",
        help="verify a hook payload (stdin) or transcript; write nothing",
    )
    p.add_argument("--agent", choices=("claude", "codex", "droid"))
    p.add_argument("--project", metavar="DIR")
    p.set_defaults(func=_cmd_ingest)

    p = sub.add_parser("distill", help="grade + consolidate; writes jevmory.md")
    p.add_argument("--project", metavar="DIR")
    p.add_argument(
        "--offline",
        action="store_true",
        help="simulated grading (FakeJev): no API, no marker needed",
    )
    p.add_argument(
        "--backend",
        choices=_BACKENDS,
        help="grading backend (default: $JEVMORY_BACKEND or typesafe; "
        "kev = local server, no key)",
    )
    p.add_argument(
        "--force", action="store_true", help="overwrite a foreign jevmory.md"
    )
    p.set_defaults(func=_cmd_distill)

    p = sub.add_parser("audit", help="grade a memory file against the store")
    p.add_argument("memory_file", nargs="?", default="MEMORY.md")
    p.add_argument("--project", metavar="DIR")
    fmt = p.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true", help="machine-readable report")
    fmt.add_argument("--md", action="store_true", help="full markdown table report")
    p.add_argument(
        "--offline",
        action="store_true",
        help="simulated grading (FakeJev): no API, no marker needed",
    )
    p.add_argument(
        "--backend",
        choices=_BACKENDS,
        help="grading backend (default: $JEVMORY_BACKEND or typesafe; "
        "kev = local server, no key)",
    )
    p.set_defaults(func=_cmd_audit)

    p = sub.add_parser("resolve", help="close an ask by human decision")
    p.add_argument("fact_id", type=int)
    group = p.add_mutually_exclusive_group()
    group.add_argument("--keep-new", action="store_true")
    group.add_argument("--keep-old", action="store_true")
    p.add_argument("--project", metavar="DIR")
    p.set_defaults(func=_cmd_resolve)

    p = sub.add_parser(
        "status", help="store stats, pending, asks, last ingest/error, paths"
    )
    p.add_argument("--project", metavar="DIR")
    p.set_defaults(func=_cmd_status)

    p = sub.add_parser(
        "install", help="print agent hook config (patches only with --yes)"
    )
    p.add_argument("--agent", choices=("claude", "codex"), required=True)
    p.add_argument("--project", metavar="DIR")
    p.add_argument(
        "--yes", action="store_true", help="patch the agent config file"
    )
    p.set_defaults(func=_cmd_install)

    p = sub.add_parser(
        "hook",
        help=argparse.SUPPRESS,  # module entry alias; never exits nonzero
    )
    p.add_argument("hook_args", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    p.set_defaults(func=_cmd_hook)

    return parser


def main(argv: list[str] | None = None, stdin_text: str | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    handler = args.func
    return handler(args, stdin_text)


if __name__ == "__main__":
    raise SystemExit(main())
