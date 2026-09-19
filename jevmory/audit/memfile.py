"""External memory-file line parser (PLAN M4: "memory-file line parser").

Agents and humans keep project memory in markdown files — CLAUDE.md,
AGENTS.md, MEMORY.md. The audit (Phase C) judges those lines against
evidence from the store, so the parser must decide, deterministically,
which lines are auditable memory claims.

Pinned stances (deliberate, each pinned by a test):

- **One file line = one auditable line.** No paragraph folding, no
  continuation merging: receipts point at real file line numbers
  (``line:42``), and every disposition maps to exactly one line the
  human can find and fix. Memory files are line-oriented; a rare
  two-line prose claim is audited as two lines.
- **Auditable** = list items (``-``/``*``/``+``/``1.``/``1)`` at any
  indent) AND plain non-blank prose lines. Both shapes occur in real
  memory files.
- **Skipped**: blank lines, ATX headings (``#``…), HTML comments,
  fenced code blocks (content and markers), horizontal rules, and
  table rows (``|``-delimited) — structure, not claims.
- **Section context**: the most recent ATX heading is carried on each
  line (``## Tooling`` etc.) so reports can group by section; the
  heading itself is not a claim.
- **Markup stripping**: emphasis (``**``/``__``/``*``/``_``), inline
  code backticks, and links (``[text](url)`` -> ``text``) are removed
  from the claim text Jev grades — markup is presentation, not claim.
  The raw line is kept verbatim for receipts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ATX heading: 1-6 '#' then a space (or end of line).
_HEADING_RE = re.compile(r"^ {0,3}#{1,6}(?:\s|$)")
# Horizontal rule: 3+ of -, * or _ with optional spaces between.
_HR_RE = re.compile(r"^ {0,3}(?:[-*_][ \t]*){3,}$")
# List item marker: -, *, + or digits followed by . or ) then space.
_LIST_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])(\s+)(.*)$")
# Table row: starts and ends with | (with optional leading whitespace).
_TABLE_RE = re.compile(r"^\s*\|.*\|\s*$")
# Fenced code block marker: 3+ backticks or tildes.
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")

# Inline markup stripping (applied in this order):
_COMMENT_RE = re.compile(r"<!--.*?-->")  # HTML comments (sentinels)
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")  # [text](url) -> text
# 2+ stars so ***text*** (bold+italic) resolves in one match
_BOLD_RE = re.compile(r"\*{2,}(.+?)\*{2,}|__(.+?)__")
_ITALIC_RE = re.compile(r"\*(.+?)\*|_(.+?)_")  # *text*/_text_
_CODE_RE = re.compile(r"``(.*?)``|`(.*?)`")  # ``code``/`code`
_SPACE_RE = re.compile(r" {2,}")  # gaps left by removed markup


@dataclass(frozen=True)
class MemoryLine:
    """One auditable line of a memory file.

    ``number`` is the 1-based FILE line number (receipt identity);
    ``raw`` is the verbatim line; ``text`` is the cleaned claim Jev
    grades; ``section`` is the enclosing ATX heading text (or None).
    """

    number: int
    raw: str
    text: str
    section: str | None

    @property
    def id(self) -> str:
        """Stable subject id for receipts (judgments.subject_id)."""
        return f"line:{self.number}"


def strip_inline_markup(text: str) -> str:
    """Remove presentation markup from a claim (see module stances)."""
    text = _COMMENT_RE.sub("", text)  # first: sentinels/notes, any position
    text = _LINK_RE.sub(r"\1", text)
    # Bold before italic: ** would otherwise match the italic pattern.
    text = _BOLD_RE.sub(lambda m: m.group(1) or m.group(2) or "", text)
    text = _ITALIC_RE.sub(lambda m: m.group(1) or m.group(2) or "", text)
    text = _CODE_RE.sub(lambda m: m.group(1) or m.group(2) or "", text)
    text = _SPACE_RE.sub(" ", text)  # gaps left by removed markup
    return text.strip()


def parse_memory_file(text: str) -> list[MemoryLine]:
    """Parse a memory file's content into its auditable lines.

    Deterministic and total: any input yields a (possibly empty) list;
    nothing raises. Line numbers are 1-based positions in ``text``.
    """
    lines: list[MemoryLine] = []
    section: str | None = None
    in_fence = False
    fence_marker = ""
    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()

        if in_fence:
            if _is_fence_close(stripped, fence_marker):
                in_fence = False
            continue  # fence content is never a claim

        if not stripped:
            continue
        fence = _FENCE_RE.match(raw)
        if fence:
            in_fence = True
            fence_marker = fence.group(1)[0]
            continue
        if _HEADING_RE.match(raw):
            section = stripped.lstrip("#").strip() or None
            continue
        if _HR_RE.match(raw) or _TABLE_RE.match(raw):
            continue

        claim = _claim_text(raw)
        if not claim:
            continue  # a list marker with nothing after it is structure
        lines.append(
            MemoryLine(number=number, raw=raw, text=claim, section=section)
        )
    return lines


def _is_fence_close(stripped: str, marker_char: str) -> bool:
    """A line closes a fence iff it is nothing but the marker character.

    Deliberately simpler than CommonMark: `````python` inside an open
    ````` ``` ```` fence stays content (only a bare marker run closes),
    and a ``~~~`` line never closes a ```` ``` ```` fence.
    """
    return bool(stripped) and set(stripped) == {marker_char}


def _claim_text(raw: str) -> str:
    """The cleaned claim of one auditable line: marker off, markup off."""
    match = _LIST_RE.match(raw)
    body = match.group(4) if match else raw.strip()
    return strip_inline_markup(body)
