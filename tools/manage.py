"""
Managing trades that are already open.

Nothing runs between messages. There is no background process, no watcher,
no alert. This file is what gets called WHEN THE USER CHECKS IN, and that
is the honest shape of it — David must never claim to be monitoring.

The backtester measured break-even management as worth +0.211R per trade
with it versus +0.180R without, so the edge is real but small. It is only
captured if someone actually moves the stop.

One structural rule, enforced in code rather than instruction:

    A STOP CAN ONLY EVER MOVE IN THE DIRECTION OF PROFIT.

Widening a stop to avoid a hit is the single most expensive habit in
retail trading and the framework forbids it outright. move_stop refuses
it. There is no override, no parameter, no flag.
"""

import os
from datetime import datetime, timezone

import MetaTrader5 as mt5

from tools.sizing import _resolve, _pip_size, _value_per_lot_per_pip
from tools.journal import _load as _journal_load, update_trade

# Framework: move to break-even around 20 pips in profit.
BE_TRIGGER_PIPS = 20.0
# Lock a pip beyond entry so a break-even stop is actually free after spread.
BE_BUFFER_PIPS = 1.0


def _pos(ticket):
    found = mt5.positions_get(ticket=int(ticket))
    if not found:
        return None
    return found[0]


def _journal_id_for_ticket(ticket):
    """Find the journal entry logged with this broker ticket."""
    for e in reversed(_journal_load()):
        if str(e.get("ticket", "")).strip() == str(ticket).strip():
            return e.get("id")
    return None


def _modify_sl(symbol, ticket, new_sl, tp):
    req = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": symbol,
        "position": int(ticket),
        "sl": float(new_sl),
        "tp": float(tp or 0.0),
    }
    res = mt5.order_send(req)
    if res is None:
        return False, f"order_send returned None ({mt5.last_error()})"
    if res.retcode == mt5.TRADE_RETCODE_DONE:
        return True, f"retcode {res.retcode}"
    return False, f"retcode {res.retcode} — {res.comment}"


# ----------------------------------------------------------------------
# 1. report
# ----------------------------------------------------------------------

def review_open_trades() -> str:
    """Report every open position: how far it has run in pips and in R, where
    its stop sits, and whether a break-even move is due.

    Call this whenever the user asks how a trade is doing, what their open
    positions look like, or whether anything needs attention. It reads the
    broker directly, so the numbers are the broker's, not a recollection.

    You are NOT monitoring anything. This is a snapshot taken when called.
    Never say you will watch a trade, alert the user, or move a stop later —
    nothing runs between messages. If something needs doing, say so now."""
    if not mt5.initialize():
        return f"Could not connect to MT5 terminal ({mt5.last_error()}). Is it running?"
    try:
        acct = mt5.account_info()
        if acct is None:
            return "Could not read account info from MT5."
        ccy = acct.currency

        positions = mt5.positions_get() or []
        orders = mt5.orders_get() or []

        if not positions and not orders:
            return (f"Nothing open and nothing pending.\n"
                    f"Balance {ccy}{float(acct.balance):,.2f} | "
                    f"equity {ccy}{float(acct.equity):,.2f}")

        lines = [f"OPEN TRADES — snapshot at "
                 f"{datetime.now(timezone.utc).strftime('%H:%M UTC')}",
                 f"Balance {ccy}{float(acct.balance):,.2f} | "
                 f"equity {ccy}{float(acct.equity):,.2f}",
                 ""]

        actions = []

        for p in positions:
            info = mt5.symbol_info(p.symbol)
            if info is None:
                lines.append(f"  {p.symbol} ticket {p.ticket}: no broker spec, "
                             f"cannot assess")
                continue
            pip = _pip_size(info)
            buy = p.type == mt5.ORDER_TYPE_BUY
            direction = "BUY" if buy else "SELL"

            moved = ((p.price_current - p.price_open) if buy
                     else (p.price_open - p.price_current)) / pip

            lines.append(f"  {p.symbol}  {direction}  {p.volume:g} lots  "
                         f"ticket {p.ticket}")
            lines.append(f"    entry {p.price_open}  now {p.price_current}  "
                         f"{moved:+.1f} pips  P/L {ccy}{float(p.profit):,.2f}")

            if p.sl:
                stop_pips = abs(p.price_open - p.sl) / pip
                r_now = moved / stop_pips if stop_pips else 0
                at_be = (p.sl >= p.price_open) if buy else (p.sl <= p.price_open)
                pv, _e = _value_per_lot_per_pip(p.symbol, info)
                risk_left = (stop_pips * pv * p.volume) if pv else None

                lines.append(f"    stop {p.sl}  ({stop_pips:.1f} pips, "
                             f"{r_now:+.2f}R so far)"
                             + ("  [at break-even or better]" if at_be else ""))
                if risk_left is not None and not at_be:
                    lines.append(f"    still risking {ccy}{risk_left:,.2f} "
                                 f"({risk_left / float(acct.balance) * 100:.2f}%)")

                if not at_be and moved >= BE_TRIGGER_PIPS:
                    actions.append(
                        f"ticket {p.ticket} ({p.symbol}) is {moved:.1f} pips up "
                        f"— break-even move is due (trigger {BE_TRIGGER_PIPS:.0f})")
            else:
                lines.append(f"    *** NO STOP LOSS — risk unbounded ***")
                actions.append(f"ticket {p.ticket} ({p.symbol}) has NO STOP. "
                               f"Set one now.")

            if p.tp:
                lines.append(f"    target {p.tp}")
            lines.append("")

        for o in orders:
            lines.append(f"  PENDING  {o.symbol}  {o.volume_current:g} lots  "
                         f"ticket {o.ticket}")
            lines.append(f"    waiting at {o.price_open}  "
                         f"SL {o.sl or '-'}  TP {o.tp or '-'}")
            lines.append("")

        if actions:
            lines.append("  DUE NOW:")
            for a in actions:
                lines.append(f"    - {a}")
            lines.append("")

        lines.append("  This is a snapshot. Nothing is watching these trades "
                     "between messages.")
        return "\n".join(lines)
    finally:
        mt5.shutdown()


# ----------------------------------------------------------------------
# 2. move a stop — profit direction only
# ----------------------------------------------------------------------

def move_stop(ticket: int, new_stop: float) -> str:
    """Move a position's stop loss. The stop may ONLY move in the direction of
    profit — higher on a buy, lower on a sell. Moving a stop further away to
    avoid being hit is refused, with no override.

    Use this to move to break-even or to trail behind structure. For a plain
    break-even move, prefer breakeven_all, which computes the level itself."""
    if not mt5.initialize():
        return f"Could not connect to MT5 terminal ({mt5.last_error()}). Is it running?"
    try:
        p = _pos(ticket)
        if p is None:
            return f"No open position with ticket {ticket}. Check review_open_trades."

        info = mt5.symbol_info(p.symbol)
        tick = mt5.symbol_info_tick(p.symbol)
        if info is None or tick is None:
            return f"No broker data for {p.symbol}. Nothing changed."

        buy = p.type == mt5.ORDER_TYPE_BUY
        new_stop = round(float(new_stop), info.digits)
        pip = _pip_size(info)

        # --- the one rule that has no override ---
        if p.sl:
            if buy and new_stop < p.sl:
                return (f"REFUSED — that widens the stop. On a BUY the stop may "
                        f"only move UP. Current {p.sl}, requested {new_stop}.\n"
                        f"Never drag a stop to avoid a hit.")
            if not buy and new_stop > p.sl:
                return (f"REFUSED — that widens the stop. On a SELL the stop may "
                        f"only move DOWN. Current {p.sl}, requested {new_stop}.\n"
                        f"Never drag a stop to avoid a hit.")

        # --- sanity: stop must be on the losing side of current price ---
        if buy and new_stop >= tick.bid:
            return (f"REFUSED — a BUY stop at {new_stop} is at or above the "
                    f"current bid {tick.bid}. That closes the position "
                    f"immediately. Use close_trade if that is what you mean.")
        if not buy and new_stop <= tick.ask:
            return (f"REFUSED — a SELL stop at {new_stop} is at or below the "
                    f"current ask {tick.ask}. That closes the position "
                    f"immediately. Use close_trade if that is what you mean.")

        # --- broker minimum distance from market ---
        min_points = getattr(info, "trade_stops_level", 0) or 0
        if min_points:
            dist = abs((tick.bid if buy else tick.ask) - new_stop) / info.point
            if dist < min_points:
                return (f"REFUSED — {new_stop} is {dist:.0f} points from market; "
                        f"broker requires at least {min_points}.")

        ok, detail = _modify_sl(p.symbol, ticket, new_stop, p.tp)
        if not ok:
            return f"NOT CHANGED — {detail}"

        old = p.sl or 0
        locked = ((new_stop - p.price_open) if buy
                  else (p.price_open - new_stop)) / pip
        return (f"Stop moved on ticket {ticket} ({p.symbol}): "
                f"{old or 'none'} -> {new_stop}\n"
                f"  {'Locks' if locked >= 0 else 'Still risks'} "
                f"{abs(locked):.1f} pips"
                f"{' of profit' if locked >= 0 else ''}.  {detail}")
    finally:
        mt5.shutdown()


# ----------------------------------------------------------------------
# 3. break-even sweep
# ----------------------------------------------------------------------

def breakeven_all(trigger_pips: float = 0) -> str:
    """Move every eligible position to break-even. A position is eligible when
    it is at least trigger_pips in profit (default 20, the framework's number)
    and its stop is not already at or beyond entry.

    The new stop sits one pip beyond entry so it is genuinely free after
    spread. Positions not yet eligible are listed and left alone."""
    if not mt5.initialize():
        return f"Could not connect to MT5 terminal ({mt5.last_error()}). Is it running?"
    try:
        trigger = float(trigger_pips) if trigger_pips else BE_TRIGGER_PIPS
        positions = mt5.positions_get() or []
        if not positions:
            return "No open positions."

        moved, waiting, failed = [], [], []

        for p in positions:
            info = mt5.symbol_info(p.symbol)
            if info is None:
                failed.append(f"{p.symbol} ticket {p.ticket}: no broker spec")
                continue
            pip = _pip_size(info)
            buy = p.type == mt5.ORDER_TYPE_BUY
            gained = ((p.price_current - p.price_open) if buy
                      else (p.price_open - p.price_current)) / pip

            at_be = p.sl and ((p.sl >= p.price_open) if buy
                              else (p.sl <= p.price_open))
            if at_be:
                waiting.append(f"ticket {p.ticket} ({p.symbol}) already at "
                               f"break-even or better")
                continue

            if gained < trigger:
                waiting.append(f"ticket {p.ticket} ({p.symbol}) only "
                               f"{gained:+.1f} pips — needs {trigger:.0f}")
                continue

            be = round(p.price_open + (BE_BUFFER_PIPS * pip if buy
                                       else -BE_BUFFER_PIPS * pip), info.digits)
            ok, detail = _modify_sl(p.symbol, p.ticket, be, p.tp)
            if ok:
                moved.append(f"ticket {p.ticket} ({p.symbol}) stop "
                             f"{p.sl or 'none'} -> {be} "
                             f"(+{BE_BUFFER_PIPS:.0f} pip locked, "
                             f"{gained:+.1f} pips up)")
            else:
                failed.append(f"ticket {p.ticket} ({p.symbol}): {detail}")

        lines = [f"BREAK-EVEN SWEEP (trigger {trigger:.0f} pips)"]
        if moved:
            lines.append("  Moved:")
            lines += [f"    {m}" for m in moved]
        if waiting:
            lines.append("  Left alone:")
            lines += [f"    {w}" for w in waiting]
        if failed:
            lines.append("  FAILED:")
            lines += [f"    {f}" for f in failed]
        if not moved:
            lines.append("  Nothing was eligible.")
        return "\n".join(lines)
    finally:
        mt5.shutdown()


# ----------------------------------------------------------------------
# 4. close out
# ----------------------------------------------------------------------

def close_trade(ticket: int, reason: str = "") -> str:
    """Close an open position at market and update its journal entry with the
    broker's own result — pips and money read from the fill, not estimated.

    Note: at the broker's 0.01 lot minimum a position cannot be closed in
    part, so a two-target plan collapses to one exit. Say so rather than
    pretending a partial is possible.

    reason: why it was closed, for the journal."""
    if not mt5.initialize():
        return f"Could not connect to MT5 terminal ({mt5.last_error()}). Is it running?"
    try:
        p = _pos(ticket)
        if p is None:
            return f"No open position with ticket {ticket}."

        info = mt5.symbol_info(p.symbol)
        tick = mt5.symbol_info_tick(p.symbol)
        if info is None or tick is None:
            return f"No broker data for {p.symbol}. Nothing closed."

        buy = p.type == mt5.ORDER_TYPE_BUY
        pip = _pip_size(info)

        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": p.symbol,
            "volume": float(p.volume),
            "type": mt5.ORDER_TYPE_SELL if buy else mt5.ORDER_TYPE_BUY,
            "position": int(ticket),
            "price": tick.bid if buy else tick.ask,
            "deviation": 20,
            "magic": 240901,
            "comment": "David close",
            "type_time": mt5.ORDER_TIME_GTC,
        }

        res, detail = None, "no attempt"
        for filling in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK,
                        mt5.ORDER_FILLING_RETURN):
            req["type_filling"] = filling
            res = mt5.order_send(req)
            if res is None:
                detail = f"order_send returned None ({mt5.last_error()})"
                continue
            if res.retcode == mt5.TRADE_RETCODE_DONE:
                detail = f"retcode {res.retcode}"
                break
            detail = f"retcode {res.retcode} — {res.comment}"
            if res.retcode != mt5.TRADE_RETCODE_INVALID_FILL:
                break
        else:
            return f"NOT CLOSED — {detail}"

        if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
            return f"NOT CLOSED — {detail}"

        fill = getattr(res, "price", None) or req["price"]
        pips = ((fill - p.price_open) if buy else (p.price_open - fill)) / pip
        money = float(getattr(res, "profit", 0.0) or p.profit)

        out = [f"CLOSED ticket {ticket} ({p.symbol}) at {fill}",
               f"  {pips:+.1f} pips   {money:+.2f}   {detail}"]

        jid = _journal_id_for_ticket(ticket)
        if jid:
            outcome = ("win" if pips > 1 else
                       "loss" if pips < -1 else "breakeven")
            out.append("")
            out.append(update_trade(entry_id=jid, outcome=outcome,
                                    pips_result=f"{pips:+.1f}",
                                    money_result=f"{money:+.2f}",
                                    notes=reason or "closed manually"))
        else:
            out.append("")
            out.append(f"  No journal entry carries ticket {ticket}. Log it with "
                       f"log_trade, then update it — do not leave a closed trade "
                       f"unrecorded.")
        return "\n".join(out)
    finally:
        mt5.shutdown()