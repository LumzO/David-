"""
Trade journal.

Fields added for the demo measurement phase (Oct 2026):
  lots, stop_pips, risk_pct, risk_money, ticket,
  followed_plan, emotion_on_entry, money_result

Why stop_pips and risk_pct matter: on a 306 balance with a 0.01 lot
minimum, risk per trade is QUANTISED, not constant. A 20-pip stop lands
at ~0.5% of the account, a 45-pip stop at ~1.1%, an 81-pip stop at 2%.
Without those two fields a pips-based win rate looks clean while the
money curve says something different, and there is no way to tell which
trades were carrying twice the weight of the others.

Every field is read with .get() so the entries already in the file —
which do not have these keys — keep working.
"""

import json
import os
from datetime import datetime, timezone

# Pin the journal to the David root rather than the current working
# directory, so running from tools/ or from the UI folder cannot silently
# start a second, empty journal. If an older journal already exists in the
# cwd, that one wins — we do not orphan existing entries.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT_FILE = os.path.join(_ROOT, "david_journal.json")
_CWD_FILE = os.path.abspath("david_journal.json")

if os.path.exists(_CWD_FILE) and not os.path.exists(_ROOT_FILE):
    JOURNAL_FILE = _CWD_FILE
else:
    JOURNAL_FILE = _ROOT_FILE


def _load():
    if not os.path.exists(JOURNAL_FILE):
        return []
    try:
        with open(JOURNAL_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_all(entries):
    with open(JOURNAL_FILE, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)


def _num(v):
    """Parse '+27.5', '-21', '22' or 22.0 to a float. None if unparseable."""
    try:
        return float(str(v).replace("+", "").replace("£", "").strip())
    except Exception:
        return None


def log_trade(pair: str, verdict: str, checklist_score: str = "",
              direction: str = "", entry: str = "", stop: str = "",
              target: str = "", notes: str = "",
              lots: str = "", stop_pips: str = "", risk_pct: str = "",
              risk_money: str = "", ticket: str = "",
              followed_plan: str = "", emotion_on_entry: str = "") -> str:
    """Log a trade analysis or executed trade to the journal. Use after every
    full checklist run (verdict: 'no-trade', 'setup-watch', 'plan-issued', or
    'entered'). Include the checklist score and key reasoning in notes.

    For anything with verdict 'entered', the sizing fields are NOT optional —
    lots, stop_pips, risk_pct and risk_money come straight from
    calculate_position_size, and ticket from the order result. Risk per trade
    is quantised by the broker's 0.01 lot minimum, so without those fields the
    track record cannot be read in R multiples and the measurement is wasted.

    NEVER invent or estimate any of these numbers. If you do not have them,
    call mt5_history, mt5_account or calculate_position_size and read them.
    The broker record is the source of truth — do not ask the user for figures
    you can read yourself, and do not revise a number because the user
    pushes back. Check the broker again instead.

    followed_plan     : 'yes' or 'no' — did the execution match the plan
    emotion_on_entry  : one or two words in the user's own language
    """
    entries = _load()
    entry_obj = {
        "id": len(entries) + 1,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "pair": pair.upper().strip(),
        "verdict": verdict.lower().strip(),
        "checklist_score": checklist_score,
        "direction": direction.lower().strip(),
        "entry": entry, "stop": stop, "target": target,
        "notes": notes.strip(),
        # --- sizing / risk ---
        "lots": lots,
        "stop_pips": stop_pips,
        "risk_pct": risk_pct,
        "risk_money": risk_money,
        "ticket": ticket,
        # --- behaviour ---
        "followed_plan": followed_plan.lower().strip(),
        "emotion_on_entry": emotion_on_entry.strip(),
        # --- filled in later via update_trade ---
        "outcome": "",
        "pips_result": "",
        "money_result": "",
    }
    entries.append(entry_obj)
    _save_all(entries)

    msg = (f"Journal entry #{entry_obj['id']} logged: {pair} — {verdict} "
           f"({entry_obj['date']}).")

    # Say out loud what is missing rather than letting a half-logged
    # executed trade sit there looking complete.
    if entry_obj["verdict"] == "entered":
        missing = [k for k in ("lots", "stop_pips", "risk_pct") if not entry_obj[k]]
        if missing:
            msg += ("\n  WARNING — executed trade logged without: "
                    + ", ".join(missing)
                    + ". This entry cannot be read in R. Get the numbers from "
                      "the broker and update it.")
    return msg


def update_trade(entry_id: int, outcome: str, pips_result: str = "",
                 money_result: str = "", notes: str = "",
                 followed_plan: str = "") -> str:
    """Update a journal entry with its outcome once known.
    outcome: 'win', 'loss', 'breakeven', 'cancelled', or 'expired'.
    pips_result e.g. '+47' or '-21'. money_result in account currency, e.g.
    '-229.09' — take it from mt5_history, never from memory or the user's
    recollection.

    Read the figures from the broker before writing them. If a figure you
    already logged disagrees with the broker, the broker is right and the
    entry gets corrected — once, to the broker's number, not repeatedly to
    whatever was last said in conversation."""
    entries = _load()
    for e in entries:
        if e["id"] == entry_id:
            e["outcome"] = outcome.lower().strip()
            if pips_result:
                e["pips_result"] = pips_result
            if money_result:
                e["money_result"] = money_result
            if followed_plan:
                e["followed_plan"] = followed_plan.lower().strip()
            if notes:
                e["notes"] = (e.get("notes", "") + " | OUTCOME: " + notes).strip(" |")
            _save_all(entries)

            # report the R multiple if we can compute it
            r_txt = ""
            p, s = _num(e.get("pips_result")), _num(e.get("stop_pips"))
            if p is not None and s:
                r_txt = f"  =  {p / s:+.2f}R"
            m = f", {money_result}" if money_result else ""
            return (f"Entry #{entry_id} updated: {outcome} "
                    f"({e.get('pips_result','?')} pips{m}){r_txt}.")
    return f"No journal entry with id {entry_id}. Use review_journal to list entries."


def review_journal(pair: str = "", last_n: int = 10) -> str:
    """Review the trade journal: recent entries plus running statistics —
    verdict counts, win rate, net pips, net money, expectancy in R, and the
    spread of risk per trade. Optionally filter by pair.

    The R figures and the risk spread are the ones that matter. A win rate
    with a wide risk spread tells you nothing on its own: three 0.5% wins do
    not pay for one 2% loss."""
    entries = _load()
    if not entries:
        return f"Journal is empty — no analyses logged yet. ({JOURNAL_FILE})"

    if pair:
        p = pair.upper().strip()
        entries_f = [e for e in entries if e.get("pair") == p]
        if not entries_f:
            return f"No journal entries for {p}. Total entries overall: {len(entries)}."
    else:
        entries_f = entries

    total = len(entries_f)
    verdicts = {}
    for e in entries_f:
        v = e.get("verdict", "?")
        verdicts[v] = verdicts.get(v, 0) + 1

    resolved = [e for e in entries_f
                if e.get("outcome") in ("win", "loss", "breakeven")]
    wins = sum(1 for e in resolved if e.get("outcome") == "win")
    win_rate = (wins / len(resolved) * 100) if resolved else None

    net_pips, counted = 0.0, 0
    net_money, money_counted = 0.0, 0
    r_values = []
    risk_pcts = []
    stop_widths = []

    for e in resolved:
        p = _num(e.get("pips_result"))
        if p is not None:
            net_pips += p
            counted += 1
        m = _num(e.get("money_result"))
        if m is not None:
            net_money += m
            money_counted += 1
        s = _num(e.get("stop_pips"))
        if p is not None and s:
            r_values.append(p / s)
        if s:
            stop_widths.append(s)
        rp = _num(e.get("risk_pct"))
        if rp:
            risk_pcts.append(rp)

    lines = [f"JOURNAL REVIEW{' — ' + pair.upper() if pair else ''} ({total} entries)"]
    lines.append("Verdicts: " + ", ".join(f"{k}: {v}" for k, v in verdicts.items()))

    if resolved:
        lines.append(f"Resolved trades: {len(resolved)} | Win rate: {win_rate:.0f}% "
                     f"({wins}W/{len(resolved)-wins}L-BE)")
        if counted:
            lines.append(f"Net pips (where recorded): {net_pips:+.0f} "
                         f"over {counted} trades")
        if money_counted:
            lines.append(f"Net money (where recorded): {net_money:+.2f} "
                         f"over {money_counted} trades")

        if r_values:
            exp_r = sum(r_values) / len(r_values)
            lines.append(f"Expectancy: {exp_r:+.3f}R per trade "
                         f"(from {len(r_values)} trades with stop_pips recorded)")
            lines.append(f"  best {max(r_values):+.2f}R | worst {min(r_values):+.2f}R")
        else:
            lines.append("Expectancy in R: NOT COMPUTABLE — no trade has "
                         "stop_pips recorded. Add it going forward.")

        if risk_pcts:
            lo, hi = min(risk_pcts), max(risk_pcts)
            avg = sum(risk_pcts) / len(risk_pcts)
            lines.append(f"Risk per trade: {lo:.2f}% to {hi:.2f}% "
                         f"(avg {avg:.2f}%, n={len(risk_pcts)})")
            if hi / lo >= 2 if lo else False:
                lines.append("  NOTE — widest risk is 2x+ the narrowest. The win "
                             "rate alone is misleading; read the R column.")

        if stop_widths:
            lo, hi = min(stop_widths), max(stop_widths)
            avg = sum(stop_widths) / len(stop_widths)
            lines.append(f"Stop width: {lo:.0f} to {hi:.0f} pips (avg {avg:.0f})")
            if hi <= 45:
                lines.append("  NOTE — no stop wider than 45 pips recorded. The "
                             "lot-size floor may be filtering out wide-stop "
                             "setups, which biases the sample toward tight "
                             "structure.")

        plan_kept = sum(1 for e in resolved if e.get("followed_plan") == "yes")
        plan_broke = sum(1 for e in resolved if e.get("followed_plan") == "no")
        if plan_kept or plan_broke:
            lines.append(f"Plan followed: {plan_kept} yes / {plan_broke} no "
                         f"({len(resolved) - plan_kept - plan_broke} unrecorded)")
            if plan_broke:
                broke_r = [_num(e.get("pips_result")) / _num(e.get("stop_pips"))
                           for e in resolved
                           if e.get("followed_plan") == "no"
                           and _num(e.get("pips_result")) is not None
                           and _num(e.get("stop_pips"))]
                kept_r = [_num(e.get("pips_result")) / _num(e.get("stop_pips"))
                          for e in resolved
                          if e.get("followed_plan") == "yes"
                          and _num(e.get("pips_result")) is not None
                          and _num(e.get("stop_pips"))]
                if broke_r and kept_r:
                    lines.append(f"  followed: {sum(kept_r)/len(kept_r):+.3f}R | "
                                 f"deviated: {sum(broke_r)/len(broke_r):+.3f}R")
    else:
        lines.append("No resolved trades yet — outcomes pending.")

    lines.append("\nRecent entries:")
    for e in entries_f[-last_n:]:
        out = ""
        if e.get("outcome"):
            out = f" → {e['outcome']} {e.get('pips_result','')}p"
            p, s = _num(e.get("pips_result")), _num(e.get("stop_pips"))
            if p is not None and s:
                out += f" ({p/s:+.2f}R)"
            if e.get("money_result"):
                out += f" {e['money_result']}"

        plan = ""
        if e.get("entry"):
            plan = (f" | {e.get('direction','')} @{e['entry']} "
                    f"SL {e.get('stop','')} TP {e.get('target','')}")

        size = ""
        if e.get("lots"):
            size = f" [{e['lots']} lots"
            if e.get("risk_pct"):
                size += f", {e['risk_pct']}%"
            if e.get("stop_pips"):
                size += f", {e['stop_pips']}p stop"
            size += "]"

        lines.append(f"#{e['id']} [{e.get('date','')}] {e.get('pair','')} — "
                     f"{e.get('verdict','')} ({e.get('checklist_score','')})"
                     f"{plan}{size}{out}")
        if e.get("notes"):
            lines.append(f"    {e['notes'][:120]}")
        if e.get("emotion_on_entry"):
            lines.append(f"    felt: {e['emotion_on_entry']}")

    lines.append(f"\n({JOURNAL_FILE})")
    return "\n".join(lines)