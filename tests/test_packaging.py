"""M0 gates: packaging metadata, stdlib-only enforcement, CLI entry point."""

from __future__ import annotations

import ast
import contextlib
import io
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = REPO_ROOT / "jevmory"

try:
    import tomllib

    HAS_TOMLLIB = True
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    HAS_TOMLLIB = False


class TestPackaging(unittest.TestCase):
    @unittest.skipUnless(HAS_TOMLLIB, "tomllib not available on this Python")
    def test_pyproject_metadata(self):
        data = tomllib.loads(
            (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        project = data["project"]
        self.assertEqual(project["name"], "jevmory")
        self.assertEqual(project["version"], "0.3.0")
        self.assertEqual(
            project["scripts"]["jevmory"], "jevmory.cli:main"
        )
        # Zero runtime dependencies is a mission invariant.
        self.assertEqual(project.get("dependencies", []), [])

    def test_stdlib_only(self):
        """Every import under jevmory/ must be stdlib or the package itself."""
        allowed = set(sys.stdlib_module_names) | {"jevmory"}
        offenders = []
        for py in sorted(PACKAGE_DIR.rglob("*.py")):
            tree = ast.parse(
                py.read_text(encoding="utf-8"), filename=str(py)
            )
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split(".")[0]
                        if root not in allowed:
                            offenders.append(f"{py.name}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    if node.level:  # relative import within the package
                        continue
                    if node.module is None:
                        continue
                    root = node.module.split(".")[0]
                    if root not in allowed:
                        offenders.append(f"{py.name}: from {node.module}")
        self.assertEqual(offenders, [])


class TestPlugin(unittest.TestCase):
    """The repo ships as a Claude Code plugin: the marketplace and the
    plugin share the repo root, so their manifests must agree."""

    def setUp(self):
        self.manifest = json.loads(
            (REPO_ROOT / ".claude-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
        self.marketplace = json.loads(
            (REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(
                encoding="utf-8"
            )
        )

    def test_versions_agree_with_the_package(self):
        from jevmory import __version__

        self.assertEqual(self.manifest["version"], __version__)
        entry = self.marketplace["plugins"][0]
        self.assertEqual(entry["version"], __version__)

    def test_marketplace_lists_the_root_plugin(self):
        entry = self.marketplace["plugins"][0]
        self.assertEqual(entry["name"], self.manifest["name"])
        self.assertEqual(entry["source"], "./")

    def test_hooks_wire_real_modules_and_events(self):
        hooks = json.loads(
            (REPO_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")
        )["hooks"]
        self.assertEqual(
            set(hooks), {"SessionStart", "SessionEnd"}
        )
        for event, entries in hooks.items():
            for entry in entries:
                for hook in entry["hooks"]:
                    self.assertEqual(hook["type"], "command")
                    self.assertIn("${CLAUDE_PLUGIN_ROOT}", hook["command"])
                    module = hook["command"].split()[-1]
                    path = REPO_ROOT / Path(module.replace(".", "/") + ".py")
                    self.assertTrue(
                        path.exists(), f"hook module missing: {module}"
                    )
        commands = " ".join(
            hook["command"]
            for entries in hooks.values()
            for entry in entries
            for hook in entry["hooks"]
        )
        self.assertIn("jevmory.recall", commands)
        self.assertIn("jevmory.hook", commands)

    def test_mcp_config_runs_the_vendored_server(self):
        servers = json.loads(
            (REPO_ROOT / ".mcp.json").read_text(encoding="utf-8")
        )["mcpServers"]
        self.assertEqual(list(servers), ["jevmory"])
        config = servers["jevmory"]
        self.assertEqual(config["command"], "python3")
        self.assertEqual(config["args"], ["-m", "jevmory.mcp"])
        self.assertEqual(
            config["env"]["PYTHONPATH"], "${CLAUDE_PLUGIN_ROOT}"
        )

    def test_bin_wrapper_is_executable(self):
        wrapper = REPO_ROOT / "bin" / "jevmory"
        self.assertTrue(wrapper.exists())
        self.assertTrue(
            wrapper.stat().st_mode & 0o111, "bin/jevmory must be +x"
        )


class TestCli(unittest.TestCase):
    def test_version_flag(self):
        from jevmory import __version__
        from jevmory.cli import main

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with self.assertRaises(SystemExit) as ctx:
                main(["--version"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn(__version__, buf.getvalue())

    def test_no_args_prints_help_and_returns_zero(self):
        from jevmory.cli import main

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main([])
        self.assertEqual(rc, 0)
        self.assertIn("jevmory", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
