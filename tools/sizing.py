"""
Position sizing — the gate that stands between David and a 46% drawdown.

Why this file exists
--------------------
On 1 September a XAUUSD trade was opened at 0.1 lots and closed -229.09
on a 500 account. That is ~46% of the account on one trade, against a
framework that calls for 1%. The lot size was not wrong by a typo — it
was wrong because 0.1 lots means something completely different on gold
than it does on a currency pair. Roughly 1 per pip on USD/CAD,
roughly 7-8 per point on gold. Same number in the volume box, about
forty times the risk.

So nothing here is inferred from the instrument's NAME. Everything that
decides money — tick value, tick size, digits, volume step, volume min —
is read from the broker. The name is only used to resolve the symbol.

Four functions:
  calculate_position_size  — the normal path. Refuses rather than guesses.
  size_check               — reverse calculator. You name the lots, it
                             names the money. Never refuses, never blocks.
  set_risk_limit           — change the ceiling. Persists and logs.
  check_position_risk      — audit open positions against the ceiling.

Hard rules enforced here (not suggestions):
  * refuse outright on instruments the backtester could not validate
  * never round a lot size UP
  * refuse if the correctly-sized trade is smaller than the broker's minimum
  * cap risk at the configured ceiling regardless of what was asked for
  * refuse a stop so tight that spread/slippage dominates
"""

import json
import os
from datetime import datetime, timezone

import MetaTrader5 as mt5

# ----------------------------------------------------------------------
# config — lives in the David root, one level up from tools/
# ----------------------------------------------------------------------

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(_ROOT, "risk_config.json")

DEFAULT_RISK_PCT = 1.0     # the framework's number
DEFAULT_CEILING = 2.0      # what the ceiling starts at
ABSOLUTE_MAX = 5.0         # David cannot raise the ceiling past this, ever
MIN_STOP_PIPS = 5.0        # below this, spread and slippage dominate

# Instruments with no validated setup. The backtester ran 730 days over
# XAUUSD and produced ZERO qualifying setups — meaning the method has
# never been tested there. Sizing a trade correctly does not make an
# untested instrument tradeable, so these are refused at the gate.
#
# To unlock one, add it to "allow_unvalidated" in risk_config.json BY HAND.
# David cannot write that key. That asymmetry is the whole point: he can't
# be talked into gold, you can deliberately choose it.
UNVALIDATED = {
    "XAUUSD", "XAGUSD", "XAUEUR", "XAUGBP", "XAUAUD", "XAUCHF",
    "GOLD", "SILVER", "XAUG",
}

# Names the user actually says, mapped to broker symbols.
ALIASES = {
    "GOLD": "XAUUSD",
    "XAU/USD": "XAUUSD",
    "SILVER": "XAGUSD",
    "XAG/USD": "XAGUSD",
}


def _load_config():
    """Read risk_config.json. Missing or corrupt file falls back to defaults
    rather than erroring — a broken config must never mean 'no limit'."""
    cfg = {
        "max_risk_pct": DEFAULT_CEILING,
        "default_risk_pct": DEFAULT_RISK_PCT,
        "allow_unvalidated": [],
        "history": [],
    }
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                cfg.update({k: v for k, v in saved.items() if k in cfg})
    except Exception:
        pass  # defaults stand

    # never trust the file past the absolute max
    try:
        cfg["max_risk_pct"] = min(float(cfg["max_risk_pct"]), ABSOLUTE_MAX)
    except Exception:
        cfg["max_risk_pct"] = DEFAULT_CEILING
    return cfg


def _save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        return True
    except Exception:
        return False


def _unlocked():
    """Set of UNVALIDATED symbols the user has manually unlocked."""
    raw = _load_config().get("allow_unvalidated") or []
    return {str(s).upper().replace("/", "") for s in raw}


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------

def _resolve(pair):
    """Return (symbol, symbol_info) or (None, reason)."""
    raw = pair.upper().replace(" ", "")
    name = ALIASES.get(raw, raw.replace("/", ""))

    info = mt5.symbol_info(name)
    if info is None:
        # brokers suffix symbols: EURUSD.raw, EURUSDm, EURUSD-ECN ...
        for suffix in (".raw", "m", ".m", "-ECN", "#", ".pro"):
            info = mt5.symbol_info(name + suffix)
            if info is not None:
                name = name + suffix
                break

    if info is None:
        return None, f"Symbol {pair} not found on this broker."

    if not info.visible:
        mt5.symbol_select(name, True)
        info = mt5.symbol_info(name)

    return name, info


def _pip_size(info):
    """Pip from the broker's digits, never from the instrument name.

    5-digit FX (1.09876) -> 0.0001
    3-digit JPY (151.234) -> 0.01
    2-digit metals/indices (4380.99) -> 0.01
    """
    return 0.01 if info.digits <= 3 else 0.0001


def _value_per_lot_per_pip(symbol, info):
    """How much one lot moves, per pip, in account currency.

    Read from the broker's own tick value and tick size. This is the
    number that differs ~40x between gold and a currency pair, and the
    number that must never be assumed.
    """
    tick_value = info.trade_tick_value      # account currency per tick, 1 lot
    tick_size = info.trade_tick_size        # price movement of one tick

    if not tick_value or not tick_size:
        return None, (f"Broker did not report tick value/size for {symbol}. "
                      f"Cannot size this instrument safely — refusing.")

    pip = _pip_size(info)
    ticks_per_pip = pip / tick_size
    return tick_value * ticks_per_pip, None


def _blocked(pair):
    """True if this instrument has no validated setup and is not unlocked."""
    raw = pair.upper().replace(" ", "").replace("/", "")
    sym = ALIASES.get(raw, raw)
    if raw not in UNVALIDATED and sym not in UNVALIDATED:
        return False
    return not ({raw, sym} & _unlocked())


def _refusal_text(pair):
    return (
        f"REFUSED — {pair} has no validated setup.\n\n"
        f"The backtester ran 730 days on this instrument and produced "
        f"ZERO qualifying setups. The method has never been tested here, "
        f"so there is no expectancy to size against.\n\n"
        f"This is also the instrument that cost 229.09 on 1 September "
        f"(~46% of the account) because 0.1 lots on gold is not 0.1 lots "
        f"on a currency pair.\n\n"
        f"I cannot unlock this. If you want it unlocked you add the symbol "
        f"to \"allow_unvalidated\" in risk_config.json yourself — deliberately, "
        f"not because I talked you into it."
    )


# ----------------------------------------------------------------------
# 1. the normal path
# ----------------------------------------------------------------------

def calculate_position_size(pair: str, entry: float, stop: float,
                            risk_percent: float = 0) -> str:
    """Work out the correct lot size for a trade from live account balance
    and the broker's own contract specification.

    Never guesses pip value from the instrument name. Refuses on
    instruments with no validated setup, refuses on stops too tight to
    survive spread, and rounds lot size DOWN only.

    pair          : e.g. "EUR/USD", "USDJPY", "GBP/USD"
    entry         : intended entry price
    stop          : stop loss price
    risk_percent  : percent of balance to risk. Omit to use the configured
                    default (1%). Capped at the configured ceiling.
    """
    if not mt5.initialize():
        return f"Could not connect to MT5 terminal ({mt5.last_error()}). Is it running?"

    try:
        cfg = _load_config()
        ceiling = float(cfg["max_risk_pct"])

        if _blocked(pair):
            return _refusal_text(pair)

        symbol, info = _resolve(pair)
        if symbol is None:
            return info  # error string

        acct = mt5.account_info()
        if acct is None:
            return "Could not read account info from MT5."

        balance = float(acct.balance)
        currency = acct.currency

        # --- risk cap ---
        requested = float(risk_percent) if risk_percent else float(cfg["default_risk_pct"])
        capped = min(requested, ceiling)
        cap_note = ""
        if capped < requested:
            cap_note = (f"\n  Risk request of {requested}% capped to "
                        f"{ceiling}% (configured ceiling).")

        risk_money = balance * capped / 100.0

        # --- stop distance ---
        pip = _pip_size(info)
        stop_pips = abs(float(entry) - float(stop)) / pip

        if stop_pips < MIN_STOP_PIPS:
            return (f"REFUSED — stop is only {stop_pips:.1f} pips from entry "
                    f"({entry} -> {stop} on {symbol}).\n"
                    f"Minimum is {MIN_STOP_PIPS} pips. Below that, spread and "
                    f"slippage decide the outcome, not the setup.")

        # --- value per pip, from the broker ---
        per_lot_per_pip, err = _value_per_lot_per_pip(symbol, info)
        if err:
            return "REFUSED — " + err

        risk_per_lot = per_lot_per_pip * stop_pips
        if risk_per_lot <= 0:
            return f"REFUSED — computed zero risk per lot on {symbol}. Will not size blind."

        exact_lots = risk_money / risk_per_lot

        # --- round DOWN to the broker's volume step, never up ---
        step = info.volume_step or 0.01
        vol_min = info.volume_min or 0.01
        vol_max = info.volume_max or 100.0

        lots = int(exact_lots / step) * step
        lots = round(lots, 8)

        if lots < vol_min:
            needed = risk_per_lot * vol_min
            needed_pct = needed / balance * 100.0
            max_stop = (balance * capped / 100.0) / (per_lot_per_pip * vol_min)
            return (
                f"REFUSED — correct size is below the broker's minimum.\n\n"
                f"{symbol}: {capped}% of {currency}{balance:,.2f} = "
                f"{currency}{risk_money:,.2f}\n"
                f"Stop distance: {stop_pips:.1f} pips\n"
                f"Correct size: {exact_lots:.4f} lots\n"
                f"Broker minimum: {vol_min} lots\n\n"
                f"Trading the minimum would risk {currency}{needed:,.2f} "
                f"({needed_pct:.1f}% of the account).\n"
                f"At {vol_min} lots, the widest stop that fits {capped}% is "
                f"{max_stop:.0f} pips.\n\n"
                f"Options: tighten the stop to inside {max_stop:.0f} pips if the "
                f"structure allows it, raise the risk deliberately with "
                f"set_risk_limit, or skip the trade. Do not size up to the "
                f"minimum and call it {capped}%."
            )

        if lots > vol_max:
            lots = vol_max

        actual_risk = lots * risk_per_lot
        actual_pct = actual_risk / balance * 100.0
        pip_value_at_size = per_lot_per_pip * lots

        out = [
            f"POSITION SIZE — {symbol}",
            f"",
            f"  Lot size      : {lots:g}",
            f"",
            f"  Account       : {currency}{balance:,.2f}",
            f"  Risk          : {capped}%  =  {currency}{risk_money:,.2f}"
            f"   (ceiling {ceiling}%)",
            f"  Entry / Stop  : {entry} -> {stop}   ({stop_pips:.1f} pips)",
            f"  Actual risk   : {currency}{actual_risk:,.2f}  ({actual_pct:.2f}%)",
            f"",
            f"  At {lots:g} lots, {symbol} moves "
            f"{currency}{pip_value_at_size:,.2f} per pip.",
            f"  One lot moves {currency}{per_lot_per_pip:,.2f} per pip "
            f"(read from broker, not assumed).",
            f"",
            f"  Broker spec   : digits {info.digits}, tick size {info.trade_tick_size}, "
            f"tick value {info.trade_tick_value}, step {step}, min {vol_min}",
        ]

        if cap_note:
            out.append(cap_note)

        out.append("")
        out.append("  Do NOT carry this lot size to another instrument. "
                   "The same number means a different amount of money on "
                   "every symbol — re-run this tool for each trade.")

        return "\n".join(out)

    finally:
        mt5.shutdown()


# ----------------------------------------------------------------------
# 2. reverse calculator — the honest override
# ----------------------------------------------------------------------

def size_check(pair: str, entry: float, stop: float, lots: float) -> str:
    """Reverse of calculate_position_size: the USER names the lot size, this
    reports what it actually costs in money and percent of account.

    This never refuses and never blocks — it is pure information, for when
    the user wants to override the calculated size and key in their own
    number. Its job is to make sure no lot size gets typed into MT5 without
    the user knowing what it means in pounds.

    Use this whenever the user says a lot size out loud rather than asking
    for one. Report the numbers plainly; do not argue with the user's choice
    beyond stating what it risks.

    pair  : e.g. "EUR/USD", "USDJPY"
    entry : intended entry price
    stop  : stop loss price
    lots  : the lot size the user wants to trade
    """
    if not mt5.initialize():
        return f"Could not connect to MT5 terminal ({mt5.last_error()}). Is it running?"

    try:
        cfg = _load_config()
        ceiling = float(cfg["max_risk_pct"])

        symbol, info = _resolve(pair)
        if symbol is None:
            return info

        acct = mt5.account_info()
        if acct is None:
            return "Could not read account info from MT5."
        balance = float(acct.balance)
        currency = acct.currency

        pip = _pip_size(info)
        stop_pips = abs(float(entry) - float(stop)) / pip

        per_lot_per_pip, err = _value_per_lot_per_pip(symbol, info)
        if err:
            return ("Cannot price this instrument — " + err +
                    "\nWithout tick value there is no way to tell you what "
                    f"{lots} lots costs. Do not key it in blind.")

        lots = float(lots)
        per_pip = per_lot_per_pip * lots
        risk = per_pip * stop_pips
        pct = risk / balance * 100.0

        # what the rules would have given, for comparison
        correct_money = balance * float(cfg["default_risk_pct"]) / 100.0
        correct_lots = correct_money / (per_lot_per_pip * stop_pips)
        step = info.volume_step or 0.01
        correct_lots = round(int(correct_lots / step) * step, 8)

        out = [
            f"SIZE CHECK — {symbol} at {lots:g} lots  (your number, not calculated)",
            f"",
            f"  Account       : {currency}{balance:,.2f}",
            f"  Entry / Stop  : {entry} -> {stop}   ({stop_pips:.1f} pips)",
            f"  Per pip       : {currency}{per_pip:,.2f}",
            f"  If stopped out: {currency}{risk:,.2f}   ({pct:.2f}% of account)",
            f"",
            f"  Framework size at {cfg['default_risk_pct']}% would be "
            f"{correct_lots:g} lots ({currency}{correct_money:,.2f}).",
        ]

        if pct > ceiling:
            multiple = pct / ceiling if ceiling else 0
            out += [
                f"",
                f"  *** This is {pct:.2f}% — {multiple:.1f}x the {ceiling}% ceiling. ***",
                f"  Not blocked. You asked what it costs and that is what it costs.",
                f"  For reference: the 1 September gold trade was ~46%.",
            ]
        elif pct > float(cfg["default_risk_pct"]) * 1.2:
            out += [
                f"",
                f"  Above the {cfg['default_risk_pct']}% default but inside the "
                f"{ceiling}% ceiling.",
            ]
        else:
            out += [f"", f"  Inside the normal band."]

        out += [
            f"",
            f"  One lot moves {currency}{per_lot_per_pip:,.2f} per pip on {symbol} "
            f"(read from broker).",
        ]
        return "\n".join(out)

    finally:
        mt5.shutdown()


# ----------------------------------------------------------------------
# 3. change the ceiling — persists and logs
# ----------------------------------------------------------------------

def set_risk_limit(max_percent: float = 0, default_percent: float = 0,
                   reason: str = "") -> str:
    """Change the risk ceiling and/or the default risk per trade.

    The new values persist to risk_config.json and survive restarts, and
    every change is logged with a timestamp and reason so a loosened limit
    cannot quietly stay loosened.

    Hard limit: the ceiling can never be set above 5%, by anyone, through
    this tool. Call with no arguments to report the current settings and
    the change history.

    max_percent     : new ceiling, e.g. 3.0. Omit to leave unchanged.
    default_percent : new default risk per trade, e.g. 0.5. Omit to leave.
    reason          : why. Required when raising the ceiling — the user's
                      own words, not your paraphrase.
    """
    cfg = _load_config()
    old_max = float(cfg["max_risk_pct"])
    old_def = float(cfg["default_risk_pct"])

    # report-only
    if not max_percent and not default_percent:
        lines = [
            "RISK SETTINGS",
            f"  Ceiling         : {old_max}%",
            f"  Default / trade : {old_def}%",
            f"  Absolute max    : {ABSOLUTE_MAX}% (cannot be raised past this)",
            f"  Config file     : {CONFIG_PATH}",
        ]
        unlocked = sorted(_unlocked())
        lines.append(f"  Unlocked instruments : "
                     f"{', '.join(unlocked) if unlocked else 'none'}")
        hist = cfg.get("history") or []
        if hist:
            lines.append("")
            lines.append("  Change history (most recent last):")
            for h in hist[-10:]:
                lines.append(f"    {h.get('when','?')}  "
                             f"ceiling {h.get('from_max')}% -> {h.get('to_max')}%, "
                             f"default {h.get('from_default')}% -> {h.get('to_default')}%"
                             f"  — {h.get('reason') or 'no reason given'}")
        return "\n".join(lines)

    new_max = float(max_percent) if max_percent else old_max
    new_def = float(default_percent) if default_percent else old_def

    if new_max > ABSOLUTE_MAX:
        return (f"REFUSED — {new_max}% is above the absolute maximum of "
                f"{ABSOLUTE_MAX}%. That limit is in the code, not the config, "
                f"and I cannot move it. Ceiling stays at {old_max}%.")

    if new_max <= 0 or new_def <= 0:
        return "REFUSED — risk percentages must be above zero."

    if new_def > new_max:
        return (f"REFUSED — default risk ({new_def}%) cannot exceed the "
                f"ceiling ({new_max}%). Raise the ceiling first if that is "
                f"what you mean.")

    if new_max > old_max and not reason.strip():
        return (f"REFUSED — raising the ceiling from {old_max}% to {new_max}% "
                f"needs a reason, in the user's own words. Ask them why, then "
                f"call this again with it. A limit loosened without a stated "
                f"reason is a limit that never goes back.")

    cfg["max_risk_pct"] = new_max
    cfg["default_risk_pct"] = new_def
    cfg.setdefault("history", []).append({
        "when": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "from_max": old_max, "to_max": new_max,
        "from_default": old_def, "to_default": new_def,
        "reason": reason.strip(),
    })

    if not _save_config(cfg):
        return (f"Could not write {CONFIG_PATH}. Settings unchanged "
                f"(ceiling {old_max}%, default {old_def}%).")

    if new_max != old_max:
        direction = "CEILING RAISED" if new_max > old_max else "CEILING LOWERED"
    elif new_def != old_def:
        direction = "DEFAULT RAISED" if new_def > old_def else "DEFAULT LOWERED"
    else:
        direction = "UNCHANGED"

    out = [
        f"RISK SETTINGS UPDATED  [{direction}]",
        f"  Ceiling         : {old_max}%  ->  {new_max}%",
        f"  Default / trade : {old_def}%  ->  {new_def}%",
        f"  Reason          : {reason.strip() or 'none given'}",
        f"  Logged to       : {CONFIG_PATH}",
    ]
    if new_max > old_max or new_def > old_def:
        out += [
            "",
            "  This persists until changed back. It applies to every trade "
            "from now on, not just the next one.",
        ]
    return "\n".join(out)


# ----------------------------------------------------------------------
# 4. audit open positions
# ----------------------------------------------------------------------

def check_position_risk(pair: str = "") -> str:
    """Audit every open position against the risk ceiling.

    Reports, per position: lot size, value per pip, distance to stop,
    money at risk, and percent of account. Flags any position with NO
    stop loss and any position over the ceiling.

    pair: optional filter, e.g. "USDJPY". Empty = all open positions.
    """
    if not mt5.initialize():
        return f"Could not connect to MT5 terminal ({mt5.last_error()}). Is it running?"

    try:
        cfg = _load_config()
        ceiling = float(cfg["max_risk_pct"])

        acct = mt5.account_info()
        if acct is None:
            return "Could not read account info from MT5."
        balance = float(acct.balance)
        currency = acct.currency

        if pair:
            symbol, _info = _resolve(pair)
            positions = mt5.positions_get(symbol=symbol) if symbol else None
        else:
            positions = mt5.positions_get()

        if positions is None or len(positions) == 0:
            return (f"No open positions{' on ' + pair if pair else ''}.\n"
                    f"Account: {currency}{balance:,.2f} | "
                    f"equity {currency}{float(acct.equity):,.2f}")

        lines = [
            f"OPEN POSITION RISK AUDIT",
            f"Account: {currency}{balance:,.2f} | "
            f"equity {currency}{float(acct.equity):,.2f} | "
            f"ceiling {ceiling}% = {currency}{balance * ceiling / 100:,.2f}",
            "",
        ]

        total_risk = 0.0
        flags = []

        for p in positions:
            sym = p.symbol
            info = mt5.symbol_info(sym)
            if info is None:
                lines.append(f"  {sym}: could not read broker spec — cannot assess risk")
                continue

            pip = _pip_size(info)
            per_lot_per_pip, _err = _value_per_lot_per_pip(sym, info)
            direction = "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL"

            lines.append(f"  {sym}  {direction}  {p.volume:g} lots  "
                         f"@ {p.price_open}  (ticket {p.ticket})")
            lines.append(f"    current {p.price_current}  "
                         f"P/L {currency}{float(p.profit):,.2f}")

            if per_lot_per_pip:
                lines.append(f"    {currency}{per_lot_per_pip * p.volume:,.2f} per pip "
                             f"at this size")

            if not p.sl:
                lines.append(f"    *** NO STOP LOSS — risk is unbounded ***")
                flags.append(f"{sym} ticket {p.ticket} has no stop loss")
            elif per_lot_per_pip:
                stop_pips = abs(float(p.price_open) - float(p.sl)) / pip
                risk = stop_pips * per_lot_per_pip * p.volume
                pct = risk / balance * 100.0
                total_risk += risk
                lines.append(f"    stop {p.sl}  ({stop_pips:.1f} pips)  "
                             f"risk {currency}{risk:,.2f}  ({pct:.2f}%)")
                if pct > ceiling:
                    lines.append(f"    *** OVER CEILING by "
                                 f"{pct - ceiling:.2f} percentage points ***")
                    flags.append(f"{sym} ticket {p.ticket} risks {pct:.2f}% "
                                 f"(ceiling {ceiling}%)")
            lines.append("")

        if total_risk:
            total_pct = total_risk / balance * 100.0
            lines.append(f"  Total measurable risk: {currency}{total_risk:,.2f} "
                         f"({total_pct:.2f}% of balance)")
            if total_pct > ceiling:
                lines.append(f"  *** AGGREGATE EXPOSURE OVER CEILING ***")

        if flags:
            lines.append("")
            lines.append("  ACTION NEEDED:")
            for f in flags:
                lines.append(f"    - {f}")

        return "\n".join(lines)

    finally:
        mt5.shutdown()