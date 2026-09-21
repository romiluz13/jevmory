---
description: Show what jevmory remembers about this project, and audit the memory file with receipts
---

Report this project's jevmory memory. The `jevmory` command is on PATH
(the plugin's `bin/`), and it never touches the network.

1. Run `jevmory status` and summarize for the user: events ingested,
   facts by status, open asks, last grading run.
2. If a `jevmory.md` memory file exists at the project root, run
   `jevmory audit jevmory.md` (grading is opt-in; if the project has no
   grading marker, report the file's contents as-is instead).
3. If status shows open asks (contradictions needing a human call),
   list them as `fact_id: claim` and ask the user to decide with
   `jevmory resolve <fact_id> --keep-new|--keep-old`.

Present facts as verbatim quotes with their confidence and vintage;
never paraphrase a remembered fact as if you witnessed it.
