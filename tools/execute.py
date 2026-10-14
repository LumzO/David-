"""
Semi-automatic execution on the MT5 demo.

Design
------
Two steps, deliberately.

  place_trade()    validates everything, stages the order to a file, and
                   prints a 4-digit confirmation code TO THE TERMINAL.
                   The code is NOT in the string returned to David, so it
                   never enters his context and he cannot complete his own
                   order. He can only tell the user that an order is staged.

  confirm_trade()  takes that code, re-validates, sends the order, and
                   writes the journal entry.

Why not a confirm="YES" parameter: David can emit the string "YES". A
behavioural guardrail is not a guardrail. A token he cannot see is.

Everything that decides money is read from the broker, never inferred
from the instrument name. Sizing is re-derived here from tools.sizing
rather than trusted from the argument, so a lot size that the gate would
not have produced cannot be executed.
"""

import json
import os
import random
from datetime import datetime, timezone

import MetaTrader5 as mt5

# One source of truth for sizing. Importing the private helpers is
# deliberate — duplicating the pip/tick-value logic here is exactly how
# the 1 September error happened.
from tools.sizing import (_resolve, _pip_size, _value_per_lot_per_pip,
                          _load_config, _blocked, _refusal_text)
from tools.journal import log_trade

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAGED_FILE = os.path.join(_ROOT, "staged_order.json")

# Staged orders go stale. A code from an hour ago refers to a price that
# no longer exists.
STAGE_TTL_SECONDS = 300          # 5 minutes

# Max spread before refusing, by instrument precision. Same table as
# mt5_data.py — read from digits, not from the name.
def _spread_limit(info):
    if info.digits <= 2:
        return 40
    if info.digits == 3:
        return 3
    return 5


def _now():
    return datetime.now(timezone.utc)


def _clear_stage():
    try:
        if os.path.exists(STAGED_FILE):
            os.remove(STAGED_FILE)
    except Exception:
        pass


# ----------------------------------------------------------------------
# validation shared by both steps
# ----------------------------------------------------------------------

def _validate(pair, direction, entry, stop, tp1, tp2, lots):
    """Return (ok, payload_or_error_string).

    payload carries everything confirm_trade needs, so the second step
    re-validates against the same rules rather than trusting the file.
    """
    cfg = _load_config()
    ceiling = float(cfg["max_risk_pct"])

    if _blocked(pair):
        return False, _refusal_text(pair)

    direction = str(direction).lower().strip()
    if direction not in ("buy", "sell"):
        return False, f"REFUSED — direction must be 'buy' or 'sell', got '{direction}'."

    symbol, info = _resolve(pair)
    if symbol is None:
        return False, info

    acct = mt5.account_info()
    if acct is None:
        return False, "Could not read account info from MT5."
    balance = float(acct.balance)
    ccy = acct.currency

    digits = info.digits
    entry = round(float(entry), digits)
    stop = round(float(stop), digits)
    tp1 = round(float(tp1), digits) if tp1 else 0.0
    tp2 = round(float(tp2), digits) if tp2 else 0.0

    # --- geometry: the sign-confusion guard ---
    if direction == "sell":
        if stop <= entry:
            return False, (f"REFUSED — on a SELL the stop must be ABOVE entry. "
                           f"Got entry {entry}, stop {stop}. Check the direction.")
        if tp1 and tp1 >= entry:
            return False, (f"REFUSED — on a SELL the target must be BELOW entry. "
                           f"Got entry {entry}, TP1 {tp1}.")
        if tp2 and tp2 >= tp1:
            return False, f"REFUSED — on a SELL, TP2 ({tp2}) must be below TP1 ({tp1})."
    else:
        if stop >= entry:
            return False, (f"REFUSED — on a BUY the stop must be BELOW entry. "
                           f"Got entry {entry}, stop {stop}. Check the direction.")
        if tp1 and tp1 <= entry:
            return False, (f"REFUSED — on a BUY the target must be ABOVE entry. "
                           f"Got entry {entry}, TP1 {tp1}.")
        if tp2 and tp2 <= tp1:
            return False, f"REFUSED — on a BUY, TP2 ({tp2}) must be above TP1 ({tp1})."

    # --- spread ---
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return False, f"No tick data for {symbol}. Cannot price the order."
    pip = _pip_size(info)
    spread_pips = (tick.ask - tick.bid) / pip
    limit = _spread_limit(info)
    if spread_pips > limit:
        return False, (f"REFUSED — spread is {spread_pips:.1f} pips on {symbol}, "
                       f"limit {limit}. Wait for it to normalise.")

    # Entry side vs market: the method enters on reversal, not breakout.
    # A sell below market becomes a SELL STOP, a buy above market a BUY STOP
    # — both are momentum entries, which this framework treats as traps.
    ref = tick.bid if direction == "sell" else tick.ask
    if direction == "sell" and entry < ref:
        return False, (f"REFUSED — a SELL at {entry} with market at {ref} would "
                       f"be a sell STOP, i.e. selling into a breakdown. The "
                       f"method sells at the high of day, above market. If you "
                       f"meant to sell a breakout, that is a different system "
                       f"and it is not backtested.")
    
    if direction == "buy" and entry > ref:
        return False, (f"REFUSED — a BUY at {entry} with market at {ref} would "
                       f"be a buy STOP, i.e. buying into a breakout. The method "
                       f"buys at the low of day, below market. If you meant to "
                       f"buy a breakout, that is a different system and it is "
                       f"not backtested.")

    # --- stop distance vs broker minimum ---
    stop_pips = abs(entry - stop) / pip
    min_stop_points = getattr(info, "trade_stops_level", 0) or 0
    min_stop_pips = min_stop_points * info.point / pip
    if stop_pips < max(5.0, min_stop_pips):
        return False, (f"REFUSED — stop is {stop_pips:.1f} pips. Minimum is "
                       f"{max(5.0, min_stop_pips):.1f} pips "
                       f"(broker requires {min_stop_pips:.1f}).")

    # --- re-derive the correct lot size; do not trust the argument ---
    per_lot_per_pip, err = _value_per_lot_per_pip(symbol, info)
    if err:
        return False, "REFUSED — " + err

    risk_money_target = balance * float(cfg["default_risk_pct"]) / 100.0
    risk_per_lot = per_lot_per_pip * stop_pips
    step = info.volume_step or 0.01
    vol_min = info.volume_min or 0.01
    correct = round(int((risk_money_target / risk_per_lot) / step) * step, 8)

    if correct < vol_min:
        return False, (f"REFUSED — correct size is {correct:g} lots, below the "
                       f"broker minimum of {vol_min}. Trading the minimum would "
                       f"risk {ccy}{risk_per_lot * vol_min:,.2f} "
                       f"({risk_per_lot * vol_min / balance * 100:.2f}%). "
                       f"Tighten the stop or skip the trade.")

    lots = float(lots) if lots else correct
    if abs(lots - correct) > step / 2:
        return False, (f"REFUSED — {lots:g} lots is not the sized position. "
                       f"calculate_position_size gives {correct:g} lots for a "
                       f"{stop_pips:.1f} pip stop at {cfg['default_risk_pct']}% "
                       f"of {ccy}{balance:,.2f}. Execution does not accept a "
                       f"size the gate would not have produced.")

    risk_money = lots * risk_per_lot
    risk_pct = risk_money / balance * 100.0

    if risk_pct > ceiling + 1e-9:
        return False, (f"REFUSED — this trade risks {risk_pct:.2f}%, over the "
                       f"{ceiling}% ceiling.")

    # --- aggregate exposure including what is already open ---
    open_risk = 0.0
    unbounded = []
    for p in (mt5.positions_get() or []):
        pinfo = mt5.symbol_info(p.symbol)
        if pinfo is None:
            continue
        if not p.sl:
            unbounded.append(f"{p.symbol} ticket {p.ticket}")
            continue
        ppip = _pip_size(pinfo)
        pv, _e = _value_per_lot_per_pip(p.symbol, pinfo)
        if pv:
            open_risk += abs(float(p.price_open) - float(p.sl)) / ppip * pv * p.volume

    if unbounded:
        return False, ("REFUSED — there are open positions with no stop loss: "
                       + ", ".join(unbounded)
                       + ". Risk is unbounded until those are protected. Fix "
                         "those before adding another.")

    total_pct = (open_risk + risk_money) / balance * 100.0
    if total_pct > ceiling + 1e-9:
        return False, (f"REFUSED — open positions already risk "
                       f"{open_risk / balance * 100:.2f}%. Adding "
                       f"{risk_pct:.2f}% takes total exposure to "
                       f"{total_pct:.2f}%, over the {ceiling}% ceiling.")

    return True, {
        "symbol": symbol, "pair": pair, "direction": direction,
        "entry": entry, "stop": stop, "tp1": tp1, "tp2": tp2,
        "lots": lots, "digits": digits, "pip": pip,
        "stop_pips": round(stop_pips, 1),
        "risk_money": round(risk_money, 2),
        "risk_pct": round(risk_pct, 2),
        "per_pip": round(per_lot_per_pip * lots, 2),
        "balance": round(balance, 2), "currency": ccy,
        "spread_pips": round(spread_pips, 1),
        "bid": tick.bid, "ask": tick.ask,
        "total_pct": round(total_pct, 2),
        "ceiling": ceiling,
    }


# ----------------------------------------------------------------------
# step 1 — stage
# ----------------------------------------------------------------------

def place_trade(pair: str, direction: str, entry: float, stop: float,
                tp1: float, tp2: float = 0, lots: float = 0,
                order_type: str = "pending", expiry_hours: float = 12,
                notes: str = "") -> str:
    """STAGE a trade on the demo account. This does NOT send the order.

    It validates direction geometry, spread, stop distance, lot size against
    calculate_position_size, and total open exposure against the ceiling.
    If anything fails it refuses and nothing is staged.

    On success it writes the order to staged_order.json and prints a 4-digit
    confirmation code TO THE USER'S TERMINAL. You do not receive that code and
    cannot see it. Tell the user the order is staged and ask them to read the
    code from their terminal and give it to you, then call confirm_trade with
    it. Do not guess a code. Do not claim the order was placed — it was not.

    pair        : e.g. "EUR/USD"
    direction   : "buy" or "sell"
    entry       : entry price
    stop        : stop loss price
    tp1         : first target
    tp2         : optional second target (not sent to broker; recorded)
    lots        : omit to use the sized position; a mismatched value is refused
    order_type  : "pending" places a limit/stop order waiting at entry — this
                  is what the method calls for, waiting for the stop hunt to
                  come back. "market" fills now at whatever is there.
    expiry_hours: hours until the order expires
    notes       : reasoning, for the journal entry
    """
    if not mt5.initialize():
        return f"Could not connect to MT5 terminal ({mt5.last_error()}). Is it running?"
    try:
        ok, payload = _validate(pair, direction, entry, stop, tp1, tp2, lots)
        if not ok:
            _clear_stage()
            return payload

        order_type = str(order_type).lower().strip()
        if order_type not in ("pending", "market"):
            return f"REFUSED — order_type must be 'pending' or 'market', got '{order_type}'."

        code = f"{random.randint(1000, 9999)}"
        payload.update({
            "order_type": order_type,
            "notes": notes,
            "code": code,
            "staged_at": _now().isoformat(),
            "expiry_hours": float(expiry_hours) if expiry_hours else 0,
        })

        try:
            with open(STAGED_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            return f"Could not stage the order ({e}). Nothing sent."

        p = payload
        # The code goes to the terminal, NOT into the returned string.
        print("\n" + "=" * 54)
        print(f"  ORDER STAGED — confirmation code: {code}")
        print(f"  {p['direction'].upper()} {p['lots']:g} lots {p['symbol']} "
              f"({order_type})")
        print(f"  entry {p['entry']}  SL {p['stop']}  TP1 {p['tp1'] or '-'}")
        print(f"  risk {p['currency']}{p['risk_money']:,.2f} ({p['risk_pct']:.2f}%)")
        print(f"  expires in {STAGE_TTL_SECONDS // 60} minutes")
        print("=" * 54 + "\n", flush=True)

        return "\n".join([
            f"ORDER STAGED — NOT SENT.",
            f"",
            f"  {p['direction'].upper()}  {p['lots']:g} lots  {p['symbol']}  "
            f"({order_type})",
            f"  Entry   : {p['entry']}    (market now {p['bid']} / {p['ask']}, "
            f"spread {p['spread_pips']:.1f}p)",
            f"  Stop    : {p['stop']}    ({p['stop_pips']:.1f} pips)",
            f"  TP1     : {p['tp1'] or '-'}" + (f"    TP2: {p['tp2']}" if p['tp2'] else ""),
            f"",
            f"  Risk    : {p['currency']}{p['risk_money']:,.2f}  "
            f"({p['risk_pct']:.2f}% of {p['currency']}{p['balance']:,.2f})",
            f"  Per pip : {p['currency']}{p['per_pip']:,.2f} at this size",
            f"  Total exposure if filled: {p['total_pct']:.2f}% "
            f"(ceiling {p['ceiling']}%)",
            f"",
            f"  A confirmation code has been printed to the user's terminal.",
            f"  I cannot see it. Ask the user to read it out, then call",
            f"  confirm_trade with it. Expires in "
            f"{STAGE_TTL_SECONDS // 60} minutes.",
        ])
    finally:
        mt5.shutdown()


# ----------------------------------------------------------------------
# step 2 — confirm and send
# ----------------------------------------------------------------------
RETCODE_HELP = {
    10027: "AutoTrading is disabled in the MT5 terminal. Click the 'Algo "
           "Trading' button in the toolbar so it turns green, then try again.",
    10018: "The market is closed for this symbol. Nothing can be sent until "
           "it opens.",
    10016: "The stop or target is too close to price for this broker, or on "
           "the wrong side of it.",
    10019: "Not enough free margin for this position size.",
    10015: "The broker rejected the price — it has moved. Re-stage on "
           "current prices.",
    10013: "The broker rejected the request as invalid. Check the symbol is "
           "tradeable on this account.",
    10014: "The broker rejected the volume. Check it against volume_min, "
           "volume_max and volume_step.",
}
def _send(p):
    """Build and send the order. Returns (ok, message, ticket)."""
    symbol = p["symbol"]
    info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if info is None or tick is None:
        return False, f"Lost broker data for {symbol}. Nothing sent.", None

    buy = p["direction"] == "buy"

    if p["order_type"] == "market":
        action = mt5.TRADE_ACTION_DEAL
        price = tick.ask if buy else tick.bid
        otype = mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL
    else:
        action = mt5.TRADE_ACTION_PENDING
        price = p["entry"]
        ref = tick.ask if buy else tick.bid
        if buy:
            otype = mt5.ORDER_TYPE_BUY_LIMIT if price < ref else mt5.ORDER_TYPE_BUY_STOP
        else:
            otype = mt5.ORDER_TYPE_SELL_LIMIT if price > ref else mt5.ORDER_TYPE_SELL_STOP

    req = {
        "action": action,
        "symbol": symbol,
        "volume": float(p["lots"]),
        "type": otype,
        "price": float(price),
        "sl": float(p["stop"]),
        "tp": float(p["tp1"]) if p["tp1"] else 0.0,
        "deviation": 20,
        "magic": 240901,          # the date this became necessary
        "comment": "David",
                "type_time": mt5.ORDER_TIME_GTC,
    }

    # A pending order should die with the setup that justified it. The method
    # is a daily-cycle method — an untriggered entry is dead, not patient.
    hours = float(p.get("expiry_hours") or 0)
    if action == mt5.TRADE_ACTION_PENDING and hours > 0:
        from datetime import timedelta
        req["type_time"] = mt5.ORDER_TIME_SPECIFIED
        req["expiration"] = int((datetime.now(timezone.utc)
                                 + timedelta(hours=hours)).timestamp())

    # Filling mode varies by broker and symbol; try the usual three.
    last = None
    for filling in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK,
                    mt5.ORDER_FILLING_RETURN):
        req["type_filling"] = filling
        res = mt5.order_send(req)
        if res is None:
            last = f"order_send returned None ({mt5.last_error()})"
            continue
        if res.retcode in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED):
            ticket = getattr(res, "order", None) or getattr(res, "deal", None)
            return True, f"retcode {res.retcode}", ticket
        last = f"retcode {res.retcode} — {res.comment}"
        if res.retcode in RETCODE_HELP:
            last += f"\n  {RETCODE_HELP[res.retcode]}"
        if res.retcode != mt5.TRADE_RETCODE_INVALID_FILL:
            break

    return False, f"Order rejected by broker: {last}", None


def confirm_trade(code: str) -> str:
    """Send the staged order, using the confirmation code the user read from
    their terminal. Re-validates everything before sending — a stale or
    now-invalid order is refused rather than sent.

    On success the journal entry is written automatically with lots,
    stop_pips, risk_pct, risk_money and the broker ticket, so there is no gap
    between placing and logging.

    code: the 4 digits the USER gives you. You cannot see the code yourself;
    if you do not have it from the user, say so and ask. Never guess digits.
    """
    if not os.path.exists(STAGED_FILE):
        return "No order is staged. Call place_trade first."

    try:
        with open(STAGED_FILE, "r", encoding="utf-8") as f:
            p = json.load(f)
    except Exception as e:
        _clear_stage()
        return f"Staged order unreadable ({e}). Cleared. Stage it again."

    if str(code).strip() != str(p.get("code")):
        return ("Confirmation code does not match the staged order. Nothing "
                "sent. Ask the user to read the code from their terminal again.")

    try:
        staged_at = datetime.fromisoformat(p["staged_at"])
        age = (_now() - staged_at).total_seconds()
    except Exception:
        age = STAGE_TTL_SECONDS + 1

    if age > STAGE_TTL_SECONDS:
        _clear_stage()
        return (f"Staged order expired ({age / 60:.1f} minutes old, limit "
                f"{STAGE_TTL_SECONDS // 60}). Prices have moved. Nothing sent — "
                f"re-run place_trade on current prices.")

    if not mt5.initialize():
        return f"Could not connect to MT5 terminal ({mt5.last_error()}). Nothing sent."
    try:
        # re-validate from scratch: the market has moved since staging
        ok, fresh = _validate(p["pair"], p["direction"], p["entry"], p["stop"],
                              p["tp1"], p["tp2"], p["lots"])
        if not ok:
            _clear_stage()
            return ("Order no longer valid, nothing sent. Staging cleared.\n\n"
                    + fresh)

        fresh["order_type"] = p["order_type"]
        sent, detail, ticket = _send(fresh)
        if not sent:
            return f"NOT SENT — {detail}"

        _clear_stage()

        journal_msg = log_trade(
            pair=fresh["pair"], verdict="entered",
            direction=fresh["direction"],
            entry=str(fresh["entry"]), stop=str(fresh["stop"]),
            target=str(fresh["tp1"]) + (f" / {fresh['tp2']}" if fresh["tp2"] else ""),
            notes=p.get("notes", ""),
            lots=str(fresh["lots"]), stop_pips=str(fresh["stop_pips"]),
            risk_pct=str(fresh["risk_pct"]), risk_money=str(fresh["risk_money"]),
            ticket=str(ticket or ""),
        )

        return "\n".join([
            f"ORDER SENT — {fresh['direction'].upper()} {fresh['lots']:g} lots "
            f"{fresh['symbol']} ({p['order_type']})",
            f"  Ticket  : {ticket}",
            f"  Entry   : {fresh['entry']}   SL {fresh['stop']}   "
            f"TP {fresh['tp1'] or '-'}",
            f"  Risk    : {fresh['currency']}{fresh['risk_money']:,.2f} "
            f"({fresh['risk_pct']:.2f}%)",
            f"  {detail}",
            f"",
            journal_msg,
            f"",
            f"  Nothing is watching this trade. Break-even at +20 pips will not "
            f"happen on its own — the broker holds the stop and target as "
            f"placed.",
        ])
    finally:
        mt5.shutdown()


def cancel_staged() -> str:
    """Discard the staged order without sending it. Use when the user changes
    their mind, the setup invalidates, or the code has been lost."""
    if not os.path.exists(STAGED_FILE):
        return "Nothing staged."
    try:
        with open(STAGED_FILE, "r", encoding="utf-8") as f:
            p = json.load(f)
        who = (f"{p.get('direction','?').upper()} {p.get('lots','?')} lots "
               f"{p.get('symbol','?')} @ {p.get('entry','?')}")
    except Exception:
        who = "staged order"
    _clear_stage()
    return f"Discarded: {who}. Nothing was sent."