"""M0 gates: packaging metadata, stdlib-only enforcement, CLI entry point."""

from __future__ import annotations

import ast
import contextlib
import io
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
        self.assertEqual(project["version"], "0.1.0")
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
