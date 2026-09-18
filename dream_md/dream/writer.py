"""dream.md writer (PLAN M5): render the memory artifact, guard its sentinel.

The artifact is the ONLY thing agents read; the store is the source of
truth. Rendering is pure: facts in, markdown out, no store access, so
a rendered file is byte-identical for the same facts + ``now``.

Layout (PLAN "dream.md output format"):

- the sentinel comment line first — the guard's version-independent
  core is ``"generated file, do not edit"``, so a file written by an
  older dream-md version is still recognized as ours;
- one section per category, pinned order (``none``-categorized facts
  stay in the store and receipts, but carry no section — nothing about
  them is silently altered);
- within a section: significance descending, then confidence, then id
  (stable ordering for byte-identical renders);
- per fact a receipt line: category, significance word, confidence
  (with the one formula spelled out from the recoverable noul), "seen
  in N sessions" (support_count), "last seen <Mon DD>", and a purely
  visual ``stale`` badge past ``STALENESS_BADGE_DAYS`` (no decay, ever);
- open asks as "Questions for you" with the resolve command.

``write_dream_md`` is the sentinel guard: a path that exists without
the sentinel is someone's file — refuse unless ``force`` (PLAN #3).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence

from dream_md import __version__
from dream_md.judgment.questions import (
    CATEGORY_OPTIONS,
    SIGNIFICANCE_LEVELS,
)
from dream_md.memory.facts import Fact
from dream_md.thresholds import STALENESS_BADGE_DAYS

# Category section order: the pinned vocabulary minus 'none'.
CATEGORY_ORDER = tuple(
    category for category in CATEGORY_OPTIONS if category != "none"
)

SENTINEL = (
    f"<!-- dream-md v{__version__} sentinel — generated file, do not edit; "
    "regenerate with `dream-md dream` -->"
)
# Version-independent core: recognizes files written by ANY dream-md version.
SENTINEL_CORE = "generated file, do not edit"

_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


class SentinelError(Exception):
    """Refusing to overwrite a file that is not dream-md's own."""


@dataclass(frozen=True)
class AskPair:
    """One open ask for the "Questions for you" section."""

    fact: Fact  # the challenger, held in the ask state
    partner_id: int | None
    partner_claim: str | None  # the incumbent's claim, if the link exists


def render_dream_md(
    facts: Sequence[Fact],
    asks: Sequence[AskPair] = (),
    *,
    now: str | None = None,
) -> str:
    """Render the dream.md artifact. Pure; see module docstring."""
    now_dt = _parse_ts(now) if now else None
    lines: list[str] = [SENTINEL, "# dream.md", ""]

    grouped: dict[str, list[Fact]] = {}
    unknown: set[str] = set()
    for fact in facts:
        if fact.category == "none":
            # 'none' facts intentionally stay out of the file
            continue
        if fact.category in CATEGORY_ORDER:
            grouped.setdefault(fact.category, []).append(fact)
        else:
            # data drift: still rendered, never hidden
            unknown.add(fact.category)
            grouped.setdefault(fact.category, []).append(fact)
    for category in (*CATEGORY_ORDER, *sorted(unknown)):
        group = grouped.get(category)
        if not group:
            continue
        lines.append(f"## {category.title()}")
        for fact in _sorted_group(group):
            lines.extend(_fact_block(fact, now_dt))
        lines.append("")

    if asks:
        lines.append("## Questions for you")
        for ask in asks:
            lines.append(_ask_line(ask))
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def write_dream_md(
    path: str | os.PathLike[str], text: str, *, force: bool = False
) -> None:
    """Write the artifact, refusing to clobber a non-dream-md file.

    A missing path is fine (first generation); an existing path carrying
    the sentinel is regenerated; an existing path WITHOUT it raises
    ``SentinelError`` unless ``force``.
    """
    target = Path(path)
    if target.exists():
        existing = target.read_text(encoding="utf-8")
        if SENTINEL_CORE not in existing and not force:
            raise SentinelError(
                f"{target} exists and has no dream-md sentinel; pass --force "
                "to overwrite it"
            )
    target.write_text(text, encoding="utf-8")


def _sorted_group(group: Sequence[Fact]) -> list[Fact]:
    """Significance desc, then confidence desc, then id (stable order)."""
    return sorted(group, key=lambda f: (-f.significance, -f.confidence, f.id))


def _fact_block(fact: Fact, now_dt: datetime | None) -> list[str]:
    """The two lines of one fact entry (plus the blank separator)."""
    word = _significance_word(fact.significance)
    # The noul is recoverable from the stored confidence for facts:
    # they passed a durable gate (> 0.5), so noul = 0.5 + confidence/2.
    noul = 0.5 + fact.confidence / 2.0
    receipt = (
        f"`{fact.category} · {word}` — confidence **{fact.confidence:.2f}** "
        f"(2·|{noul:.3f}−0.5|) · seen in {_sessions(fact.support_count)} · "
        f"last seen {_last_seen(fact, now_dt)}"
    )
    return [f'- **"{_display(fact.claim)}"**', f"  {receipt}", ""]


def _ask_line(ask: AskPair) -> str:
    line = (
        f'- "{_display(ask.fact.claim)}" '
        f"(confidence {ask.fact.confidence:.2f})"
    )
    if ask.partner_claim is not None:
        line += f' vs "{_display(ask.partner_claim)}" — which is current?'
    line += (
        f" `dream-md resolve {ask.fact.id} --keep-new|--keep-old`"
    )
    return line


def _significance_word(significance: float) -> str:
    """Nearest level word (half-to-even on .5 ties; clamped to the scale)."""
    index = round(significance)
    index = max(0, min(index, len(SIGNIFICANCE_LEVELS) - 1))
    return SIGNIFICANCE_LEVELS[index]


def _sessions(support_count: int) -> str:
    if support_count == 1:
        return "1 session"
    return f"{support_count} sessions"


def _last_seen(fact: Fact, now_dt: datetime | None) -> str:
    stamp = fact.last_supported_at or fact.created_at
    seen = _parse_ts(stamp)
    text = f"{seen:%b} {seen.day}"
    if now_dt is not None and (now_dt - seen).days > STALENESS_BADGE_DAYS:
        text += " · stale"  # purely visual (PLAN "No decay in v1")
    return text


def _parse_ts(stamp: str) -> datetime:
    return datetime.strptime(stamp, _TS_FMT)


def _display(claim: str) -> str:
    """Newlines would break the one-line markdown entries; store stays raw."""
    return (
        claim.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    )
