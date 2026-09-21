"""MCP server tests: dispatch, tools over a real store, and the wire loop.

All timestamps pinned; every store lives under a fresh $JEVMORY_HOME
temp dir so tests never touch the real ~/.jevmory. The wire tests run
serve() over StringIO streams — the same line-delimited JSON-RPC a
real stdio client speaks.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from jevmory import __version__
from jevmory.ingestion.eventlog import project_slug, store_path
from jevmory.mcp import PROTOCOL_VERSION, dispatch, resolve_project, serve
from jevmory.memory.facts import add_fact, add_link
from jevmory.memory.schema import connect, migrate

T0 = "2026-09-19T00:00:00Z"
T1 = "2026-09-19T00:01:00Z"


class McpTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._env = os.environ.get("JEVMORY_HOME")
        os.environ["JEVMORY_HOME"] = str(self.home)
        self.project = self.home / "proj"
        self.project.mkdir()

    def tearDown(self):
        if self._env is None:
            os.environ.pop("JEVMORY_HOME", None)
        else:
            os.environ["JEVMORY_HOME"] = self._env
        self._tmp.cleanup()

    def seed(self, claim, *, now=T0, event_ids=("ev1",)):
        conn = connect(store_path(str(self.project)))
        try:
            migrate(conn)
            return add_fact(
                conn, project=project_slug(str(self.project)), claim=claim,
                category="convention", significance=1.2, durable_noul=0.9,
                source_event_ids=event_ids, now=now,
            )
        finally:
            conn.close()


class DispatchTest(McpTestCase):
    def test_initialize_echoes_protocol_and_announces_server(self):
        result = dispatch("initialize", {"protocolVersion": "2025-06-18"})
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        self.assertEqual(
            result["serverInfo"], {"name": "jevmory", "version": __version__}
        )
        self.assertIn("recall", result["instructions"])

    def test_initialize_defaults_protocol_when_client_silent(self):
        result = dispatch("initialize", {})
        self.assertEqual(result["protocolVersion"], PROTOCOL_VERSION)

    def test_ping_returns_empty_object(self):
        self.assertEqual(dispatch("ping", {}), {})

    def test_notification_methods_return_none(self):
        self.assertIsNone(dispatch("notifications/initialized", {}))
        self.assertIsNone(dispatch("notifications/cancelled", {}))

    def test_tools_list_names_three_read_only_tools(self):
        result = dispatch("tools/list", {})
        names = [tool["name"] for tool in result["tools"]]
        self.assertEqual(names, ["status", "recall", "fact"])
        for tool in result["tools"]:
            self.assertIn("description", tool)
            self.assertIn("inputSchema", tool)
        recall_schema = result["tools"][1]["inputSchema"]
        self.assertEqual(recall_schema["required"], ["query"])

    def test_unknown_method_raises_lookup(self):
        with self.assertRaises(LookupError):
            dispatch("bogus/method", {})

    def test_unknown_tool_raises_value_error(self):
        with self.assertRaises(ValueError):
            dispatch("tools/call", {"name": "nope", "arguments": {}})


class ToolsTest(McpTestCase):
    def _call(self, name, **arguments):
        result = dispatch("tools/call", {"name": name, "arguments": arguments})
        self.assertFalse(result.get("isError", False))
        return result["content"][0]["text"]

    def test_status_reports_no_store_honestly(self):
        text = self._call("status", project=str(self.project))
        self.assertIn("no store yet for proj", text)

    def test_status_counts_events_and_facts(self):
        self.seed("We use uv, never pip.", now=T0)
        text = self._call("status", project=str(self.project))
        self.assertIn(str(self.project), text)
        self.assertIn("facts:", text)

    def test_recall_requires_query(self):
        result = dispatch("tools/call", {"name": "recall", "arguments": {}})
        self.assertTrue(result["isError"])
        self.assertIn("query is required", result["content"][0]["text"])

    def test_recall_rejects_out_of_range_limit(self):
        for bad in (0, 26, "many"):
            result = dispatch(
                "tools/call",
                {"name": "recall",
                 "arguments": {"query": "x", "limit": bad}},
            )
            self.assertTrue(result["isError"], f"limit={bad!r} not rejected")
            self.assertIn("error", result["content"][0]["text"])

    def test_recall_finds_similar_fact(self):
        self.seed("We use uv, never pip, in this repo.", now=T0)
        text = self._call("recall", query="uv pip",
                          project=str(self.project))
        self.assertIn("We use uv, never pip", text)
        self.assertIn("conf", text)

    def test_recall_no_match_is_honest(self):
        self.seed("We use uv, never pip.", now=T0)
        text = self._call("recall", query="kubernetes ingress",
                          project=str(self.project))
        self.assertIn("nothing similar remembered", text)

    def test_fact_requires_id(self):
        result = dispatch("tools/call", {"name": "fact", "arguments": {}})
        self.assertTrue(result["isError"])

    def test_fact_shows_claim_and_contradiction_partner(self):
        f1 = self.seed("The API prefix is /api/v3.", now=T0)
        f2 = self.seed("The API prefix is /api/v2.", now=T1)
        conn = connect(store_path(str(self.project)))
        try:
            add_link(conn, f1.id, f2.id, "contradicts")
        finally:
            conn.close()
        text = self._call("fact", id=f1.id, project=str(self.project))
        self.assertIn(f"fact {f1.id}", text)
        self.assertIn("/api/v3", text)
        self.assertIn(f"contradicts fact {f2.id}", text)

    def test_fact_missing_id_is_honest(self):
        self.seed("We use uv, never pip.", now=T0)
        text = self._call("fact", id=999, project=str(self.project))
        self.assertIn("no fact 999", text)


class ResolveProjectTest(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("JEVMORY_PROJECT")

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("JEVMORY_PROJECT", None)
        else:
            os.environ["JEVMORY_PROJECT"] = self._saved

    def test_argument_wins(self):
        os.environ["JEVMORY_PROJECT"] = "/tmp/from-env"
        self.assertEqual(
            resolve_project("/tmp/from-arg"), os.path.realpath("/tmp/from-arg")
        )

    def test_env_falls_back_to_cwd(self):
        os.environ["JEVMORY_PROJECT"] = os.getcwd()
        self.assertEqual(resolve_project(None), os.path.realpath(os.getcwd()))


class ServeTest(McpTestCase):
    def _serve(self, *messages):
        wire = "\n".join(
            msg if isinstance(msg, str) else json.dumps(msg) for msg in messages
        ) + "\n"
        reader = io.StringIO(wire)
        writer = io.StringIO()
        serve(reader, writer)
        return [json.loads(line) for line in writer.getvalue().splitlines()]

    def test_request_response_round_trip(self):
        replies = self._serve(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
        )
        self.assertEqual([r["id"] for r in replies], [1, 2])
        self.assertEqual(replies[0]["result"]["serverInfo"]["name"], "jevmory")
        self.assertEqual(replies[1]["result"], {})

    def test_notification_gets_no_response(self):
        replies = self._serve(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 7, "method": "ping"},
        )
        self.assertEqual([r["id"] for r in replies], [7])

    def test_parse_error_replies_with_null_id(self):
        replies = self._serve("this is not json")
        self.assertEqual(len(replies), 1)
        self.assertIsNone(replies[0]["id"])
        self.assertEqual(replies[0]["error"]["code"], -32700)

    def test_non_object_message_is_invalid_request(self):
        replies = self._serve("[1, 2, 3]")
        self.assertEqual(replies[0]["error"]["code"], -32600)

    def test_unknown_method_is_method_not_found(self):
        replies = self._serve(
            {"jsonrpc": "2.0", "id": 3, "method": "no/such"}
        )
        self.assertEqual(replies[0]["error"]["code"], -32601)

    def test_blank_lines_are_skipped(self):
        replies = self._serve(
            "", "   ",
            {"jsonrpc": "2.0", "id": 9, "method": "ping"},
        )
        self.assertEqual([r["id"] for r in replies], [9])

    def test_tool_call_over_the_wire(self):
        self.seed("We use uv, never pip.", now=T0)
        replies = self._serve(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "recall",
                        "arguments": {"query": "uv pip",
                                      "project": str(self.project)}}},
        )
        text = replies[0]["result"]["content"][0]["text"]
        self.assertIn("We use uv, never pip", text)

    def test_server_survives_a_crashing_tool(self):
        import unittest.mock as mock

        def boom(args):
            raise RuntimeError("boom")

        with mock.patch.dict("jevmory.mcp._TOOL_FUNCS", status=boom):
            replies = self._serve(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params": {"name": "status", "arguments": {}}},
                {"jsonrpc": "2.0", "id": 2, "method": "ping"},
            )
        self.assertEqual(replies[0]["error"]["code"], -32603)
        self.assertEqual(replies[1]["result"], {})


if __name__ == "__main__":
    unittest.main()
