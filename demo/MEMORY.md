<!-- jev-md planted-error demo fixture: run_demo.py matches planted lines by substring ("bun" -> stale, "pytest" -> wrong, "retry" -> unsupported), never by line number, so this file stays free to edit. -->

# MEMORY.md

- The build runs on Bun; `bun run build` is the only supported command.
- The test suite runs with pytest.
- Failed API requests retry up to five times before giving up.
- State lives in a local SQLite store; grading needs an opt-in marker.
- Every fact in jev.md is a verbatim quote with receipts.
