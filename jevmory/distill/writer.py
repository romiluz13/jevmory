"""jevmory.md writer (PLAN M5): render the memory artifact, guard its sentinel.

The artifact is the ONLY thing agents read; the store is the source of
truth. Rendering is pure: facts in, markdown out, no store access, so
a rendered file is byte-identical for the same facts + ``now``.

Layout (PLAN "jevmory.md output format"):

- the sentinel comment line first — the guard's version-independent
  core is ``"generated file, do not edit"``, so a file written by an
  older jevmory version is still recognized as ours;
- one section per category, pinned order (``none``-categorized facts
  stay in the store and receipts, but carry no section — nothing about
  them is silently altered);
- within a section: significance descending, then confidence, then id
  (stable ordering for byte-identical renders);
- per fact a receipt line: category, significance word, confidence,
  "seen
  in N sessions" (``sessions_by_fact``: distinct observing sessions —
  NOT ``support_count``, which counts events and overclaims whenever
  one session observes a claim across several turns; review S6), the
  vintage marker (v3): "said <Mon DD>" plus "verified <Mon DD>" when
  stage 1 of the audit confirmed it verbatim, or "unverified" when it
  never has been — never a guessed date, never silence — and a purely
  visual ``stale`` badge past ``STALENESS_BADGE_DAYS`` (no decay, ever);
- open asks as "Questions for you" with the resolve command.

``write_jevmory`` is the sentinel guard: a path that exists without
the sentinel is someone's file — refuse unless ``force`` (PLAN #3).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence

from jevmory import __version__
from jevmory.judgment.questions import (
    CATEGORY_OPTIONS,
    SIGNIFICANCE_LEVELS,
)
from jevmory.memory.facts import Fact
from jevmory.thresholds import STALENESS_BADGE_DAYS

# Category section order: the pinned vocabulary minus 'none'.
CATEGORY_ORDER = tuple(
    category for category in CATEGORY_OPTIONS if category != "none"
)

SENTINEL = (
    f"<!-- jevmory v{__version__} sentinel — generated file, do not edit; "
    "regenerate with `jevmory distill` -->"
)
# Version-independent core: recognizes files written by ANY jevmory version.
SENTINEL_CORE = "generated file, do not edit"

_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


class SentinelError(Exception):
    """Refusing to overwrite a file that is not jevmory's own."""


@dataclass(frozen=True)
class AskPair:
    """One open ask for the "Questions for you" section."""

    fact: Fact  # the challenger, held in the ask state
    partner_id: int | None
    partner_claim: str | None  # the incumbent's claim, if the link exists


def render_jevmory(
    facts: Sequence[Fact],
    asks: Sequence[AskPair] = (),
    *,
    now: str | None = None,
    sessions_by_fact: Mapping[int, int] | None = None,
) -> str:
    """Render the jevmory.md artifact. Pure; see module docstring.

    ``sessions_by_fact`` maps fact id to its distinct observing session
    count — the honest "seen in N sessions" number. Without it (pure
    callers with no store), the receipt falls back to occurrence
    counting ("seen N times"), never guessing a session count from
    event counts.
    """
    now_dt = _parse_ts(now) if now else None
    sessions = dict(sessions_by_fact) if sessions_by_fact else {}
    lines: list[str] = [SENTINEL, "# jevmory.md", ""]

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
            lines.extend(_fact_block(fact, now_dt, sessions.get(fact.id)))
        lines.append("")

    if asks:
        lines.append("## Questions for you")
        for ask in asks:
            lines.append(_ask_line(ask))
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def write_jevmory(
    path: str | os.PathLike[str], text: str, *, force: bool = False
) -> None:
    """Write the artifact, refusing to clobber a non-jevmory file.

    A missing path is fine (first generation); an existing path carrying
    the sentinel is regenerated; an existing path WITHOUT it raises
    ``SentinelError`` unless ``force``.
    """
    target = Path(path)
    if target.exists():
        existing = target.read_text(encoding="utf-8")
        if SENTINEL_CORE not in existing and not force:
            raise SentinelError(
                f"{target} exists and has no jevmory sentinel; pass --force "
                "to overwrite it"
            )
    target.write_text(text, encoding="utf-8")


def _sorted_group(group: Sequence[Fact]) -> list[Fact]:
    """Significance desc, then confidence desc, then id (stable order)."""
    return sorted(group, key=lambda f: (-f.significance, -f.confidence, f.id))


def _fact_block(
    fact: Fact, now_dt: datetime | None, sessions: int | None
) -> list[str]:
    """The two lines of one fact entry (plus the blank separator)."""
    word = _significance_word(fact.significance)
    # Dogfood round 1 (F4): plain calibrated confidence only — the
    # noul-recovery formula was developer math in a user artifact.
    receipt = (
        f"`{fact.category} · {word}` — confidence **{fact.confidence:.2f}** "
        f"· {_seen_phrase(fact, sessions)} · {_vintage_phrase(fact, now_dt)}"
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
        f" `jevmory resolve {ask.fact.id} --keep-new|--keep-old`"
    )
    return line


def _significance_word(significance: float) -> str:
    """Nearest level word (half-to-even on .5 ties; clamped to the scale)."""
    index = round(significance)
    index = max(0, min(index, len(SIGNIFICANCE_LEVELS) - 1))
    return SIGNIFICANCE_LEVELS[index]


def _seen_phrase(fact: Fact, sessions: int | None) -> str:
    """The "seen" clause: exact session count when known, else occurrences.

    ``sessions`` is the distinct observing session count (review S6);
    without it the receipt counts occurrences and says so — it never
    dresses an event count up as a session count.
    """
    if sessions is None:
        noun = "time" if fact.support_count == 1 else "times"
        return f"seen {fact.support_count} {noun}"
    noun = "session" if sessions == 1 else "sessions"
    return f"seen in {sessions} {noun}"


def _vintage_phrase(fact: Fact, now_dt: datetime | None) -> str:
    """Said-then vs verified-now (schema v3 vintage markers).

    ``said`` is when the project last supported the claim
    (``last_supported_at`` falling back to ``created_at``). The audit
    trail reads honestly in both directions: a verified fact carries
    its check date, an unaudited one says "unverified" out loud —
    never a guessed date, never silence. The purely visual ``stale``
    badge still rides the SAID date (no decay, ever).
    """
    seen = _parse_ts(fact.last_supported_at or fact.created_at)
    text = f"said {seen:%b} {seen.day}"
    if now_dt is not None and (now_dt - seen).days > STALENESS_BADGE_DAYS:
        text += " · stale"  # purely visual (PLAN "No decay in v1")
    if fact.verified_at:
        verified = _parse_ts(fact.verified_at)
        text += f" · verified {verified:%b} {verified.day}"
    else:
        text += " · unverified"
    return text


def _parse_ts(stamp: str) -> datetime:
    return datetime.strptime(stamp, _TS_FMT)


def _display(claim: str) -> str:
    """Newlines would break the one-line markdown entries; store stays raw."""
    return (
        claim.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    )
