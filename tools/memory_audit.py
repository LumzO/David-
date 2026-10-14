"""
Memory audit.

david_memory.json has been accumulating since June 2026 with nothing
reviewing it. Three things go wrong in a store like that:

  STALE METHOD RULES
    Notes written before the framework changed. The 1-11 score is
    retired, break-even management is retired, pip size is no longer
    inferred from the instrument name, ADR is no longer a filter. A note
    saying otherwise is a live instruction David will act on, because
    memory is injected the same way the framework is.

  DEAD TIME-BOUND ENTRIES
    Predictions about events that have already resolved, and notes that
    explicitly asked to be deleted after a date. They cost tokens on
    every call and can mislead — an old "user predicts X" reads as a
    current view.

  INSTRUCTIONS HIDING IN MEMORY
    A note phrased as "always do X" is a prompt addition that never went
    through values.py, so nothing reconciles it against the framework.
    These are the ones most likely to produce behaviour that contradicts
    the current rules.

This REPORTS. It does not delete. Deciding what to drop is the user's
call, and an audit that quietly edits the thing it audits is worse than
no audit.
"""

import json
import os
import re
from collections import Counter
from datetime import datetime, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT_FILE = os.path.join(_ROOT, "david_memory.json")
_CWD_FILE = os.path.abspath("david_memory.json")

if os.path.exists(_CWD_FILE) and not os.path.exists(_ROOT_FILE):
    MEMORY_FILE = _CWD_FILE
else:
    MEMORY_FILE = _ROOT_FILE


# Each pattern is (label, regex, why it matters). Ordered most to least
# serious. Add to these as the framework changes — this list IS the
# record of what has been retired.
STALE_RULES = [
    ("retired score",
     r"\b\d{1,2}\s*/\s*11\b|11[- ]point|checklist score|score of \d",
     "The 1-11 score is retired. It did not predict outcomes across 464 "
     "setups and a higher score was mildly worse."),
    ("break-even management",
     r"break[-\s]?even|breakeven|move (the )?stop to b/?e|trail(ing)? the stop",
     "Retired with the TP1 exit. The trade now closes at the level where "
     "the stop would have moved."),
    ("pip size by name",
     r"JPY pairs? (use|are)|if .{0,12}JPY.{0,12}then|0\.0001 (for|unless)",
     "Pip size comes from the broker's digit count via market.pip_size. "
     "Inferring it from the name gives 0.0001 on gold — 100x wrong."),
    ("ADR as a filter",
     r"ADR (met|filter|reversal filter)|range met.{0,20}(enter|trade|reversal)",
     "adr_met was removed as lookahead — it compared a setup against the "
     "full day's range, unknowable at entry."),
    ("second target / runner",
     r"TP2|second target|take part(ial)?|scale out|runner|close half",
     "At 0.01 lots a position cannot be closed in part. One exit, at TP1. "
     "TP2 survives only as the R:R selection filter."),
    ("hardcoded lot size",
     r"\b0?\.\d{1,2}\s*lots?\b|\blot size (of|is)\b",
     "Lot size comes from calculate_position_size against the live "
     "balance. A remembered number is the 1 September error."),
    ("tight Asian range required",
     r"asian range must|requires? a tight asian|tight accumulation is "
     r"(needed|required)",
     "Measured -0.129R. Mildly unhelpful, not a precondition."),
    ("buy bias",
     r"buys? (out)?perform|prefer buys|W at the low is better",
     "SELL +0.248R vs BUY +0.291R. No edge worth acting on."),
    ("20-pip pullback gate",
     r"20\+? pip pullback (is|required|gate)|pullback of (at least )?20",
     "The tested gate is TEN pips. A 20-pip requirement selects a "
     "narrower population than the one measured."),
]

# Phrasing that makes a note behave like a prompt rule rather than a fact.
INSTRUCTION_WORDS = re.compile(
    r"\balways\b|\bnever\b|\bfrom now on\b|\bgoing forward\b|"
    r"\bmake sure\b|\bremember to\b|\bdon'?t forget\b|\bevery time\b|"
    r"\bdefault to\b|\bprefer\b",
    re.I)

# Notes that asked to be removed, or that describe a dated one-off event.
SELF_DESTRUCT = re.compile(
    r"delete (this|the|after|conversation|once)|remove after|"
    r"forget (this|after)|track the outcome", re.I)

EVENT_WORDS = re.compile(
    r"\bfight\b|\bmatch\b|\bvs\.?\b|\belection\b|\bworld cup\b|"
    r"\bfinal\b|\bprediction\b|\bwill (beat|win|lose)\b", re.I)


def _load():
    if not os.path.exists(MEMORY_FILE):
        return []
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _age_days(entry):
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            d = datetime.strptime(str(entry.get("date", "")).strip(), fmt)
            return (datetime.now() - d).days
        except Exception:
            continue
    return None


def audit_memory(stale_days: int = 90, show: int = 0) -> str:
    """Audit the memory store for notes that conflict with the current
    framework, notes about events that have already resolved, and notes
    phrased as standing instructions.

    Reports only — it never deletes. Use delete_memory on anything the
    user agrees to drop, and quote the topic and date so the right entry
    goes.

    Run this weekly. If a flagged note is still correct, say so rather
    than deleting it — the patterns are deliberately broad and will catch
    things that are fine.

    stale_days : flag entries older than this for review (default 90)
    show       : also print the N oldest entries in full (default 0)
    """
    mem = _load()
    if not mem:
        return f"Memory is empty or unreadable. ({MEMORY_FILE})"

    flagged = {}          # label -> list of (index, entry, why)
    instructions = []
    resolved = []
    stale = []

    for i, e in enumerate(mem):
        note = str(e.get("note", ""))
        topic = str(e.get("topic", ""))
        blob = f"{topic} {note}"

        for label, pattern, why in STALE_RULES:
            if re.search(pattern, blob, re.I):
                flagged.setdefault(label, []).append((i, e, why))

        if INSTRUCTION_WORDS.search(note):
            instructions.append((i, e))

        if SELF_DESTRUCT.search(note) or EVENT_WORDS.search(blob):
            age = _age_days(e)
            if age is None or age > 14:
                resolved.append((i, e, age))

        age = _age_days(e)
        if age is not None and age > stale_days:
            stale.append((i, e, age))

    def line(i, e):
        note = str(e.get("note", "")).replace("\n", " ")
        return (f"    [{i}] {e.get('date','?')}  {e.get('topic','?')}\n"
                f"         {note[:150]}{'...' if len(note) > 150 else ''}")

    out = [f"MEMORY AUDIT — {len(mem)} entries",
           f"({MEMORY_FILE})",
           ""]

    # ---- 1. conflicts with the current framework ----
    if flagged:
        total = sum(len(v) for v in flagged.values())
        out.append(f"CONFLICTS WITH THE CURRENT FRAMEWORK  ({total} hits)")
        out.append("  These contradict rules that have since changed. Memory "
                   "is injected like the framework is, so a stale rule here "
                   "is a live instruction.")
        out.append("")
        for label, hits in flagged.items():
            out.append(f"  {label.upper()}  ({len(hits)})")
            out.append(f"    why: {hits[0][2]}")
            for i, e, _ in hits:
                out.append(line(i, e))
            out.append("")
    else:
        out.append("CONFLICTS WITH THE CURRENT FRAMEWORK: none found.")
        out.append("")

    # ---- 2. standing instructions ----
    if instructions:
        out.append(f"PHRASED AS STANDING INSTRUCTIONS  ({len(instructions)})")
        out.append("  These behave like prompt rules but never went through "
                   "values.py, so nothing reconciles them against the "
                   "framework. Decide for each: promote it to values.py, or "
                   "delete it.")
        out.append("")
        for i, e in instructions:
            out.append(line(i, e))
        out.append("")

    # ---- 3. resolved one-offs ----
    if resolved:
        out.append(f"LIKELY RESOLVED OR SELF-EXPIRING  ({len(resolved)})")
        out.append("  Predictions about events that have passed, or notes "
                   "that asked to be deleted after a date. An old 'user "
                   "predicts X' reads as a current view.")
        out.append("")
        for i, e, age in resolved:
            out.append(line(i, e) + (f"\n         [{age} days old]"
                                     if age is not None else ""))
        out.append("")

    # ---- 4. duplicate topics ----
    topics = Counter(str(e.get("topic", "")).lower() for e in mem)
    dupes = {t: n for t, n in topics.items() if n > 2 and t}
    if dupes:
        out.append(f"REPEATED TOPICS  ({len(dupes)})")
        out.append("  More than two notes under one topic usually means the "
                   "later ones supersede the earlier. Keep the newest.")
        for t, n in sorted(dupes.items(), key=lambda kv: -kv[1]):
            idxs = [str(i) for i, e in enumerate(mem)
                    if str(e.get("topic", "")).lower() == t]
            out.append(f"    {t}  x{n}   entries {', '.join(idxs)}")
        out.append("")

    # ---- 5. age ----
    if stale:
        oldest = max(a for _, _, a in stale if a is not None)
        out.append(f"OLDER THAN {stale_days} DAYS  ({len(stale)}, oldest "
                   f"{oldest} days)")
        out.append("  Not automatically wrong — just unreviewed. Skim for "
                   "anything that is no longer true.")
        if show:
            for i, e, age in sorted(stale, key=lambda x: -(x[2] or 0))[:show]:
                out.append(line(i, e) + f"\n         [{age} days old]")
        out.append("")

    out.append("WHAT TO DO:")
    out.append("  Work through the conflicts first — those change behaviour.")
    out.append("  Then the standing instructions: each one is either worth "
               "promoting into values.py, or worth deleting. Leaving it in "
               "memory is the one option that guarantees nobody reconciles "
               "it.")
    out.append("  Nothing here has been deleted. Use delete_memory, quoting "
               "the topic, once the user agrees.")
    return "\n".join(out)