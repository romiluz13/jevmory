"""dream-md command line entry point.

Milestone M0 ships only the console entry point (--version, help).
Real subcommands (ingest / dream / audit / status / install) are wired
in milestone M6 per docs/PLAN.md.
"""

from __future__ import annotations

import argparse

from dream_md import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dream-md",
        description=(
            "Coding-agent memory with receipts: Jev-graded verbatim "
            "facts from session transcripts."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"dream-md {__version__}"
    )
    parser.parse_args(argv)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
