"""
Broker data: live price, candles, account state, closed-trade history.

Everything about pip size and time conversion comes from tools.market.
Nothing in this file re-derives either — that duplication is what let the
September pip bug survive in three separate files.
"""

from datetime import timedelta

import MetaTrader5 as mt5

from tools.market import (ALIASES, broker_utc, fmt_utc, pip_size,
                          server_now, spread_limit)

TF_MAP = {
    "1min": mt5.TIMEFRAME_M1, "5min": mt5.TIMEFRAME_M5,
    "15min": mt5.TIMEFRAME_M15, "30min": mt5.TIMEFRAME_M30,
    "1h": mt5.TIMEFRAME_H1, "4h": mt5.TIMEFRAME_H4,
    "1day": mt5.TIMEFRAME_D1, "1week": mt5.TIMEFRAME_W1,
}


def _connect():
    if not mt5.initialize():
        return False, (f"Could not connect to MT5 terminal "
                       f"(error {mt5.last_error()}). Is it running and "
                       f"logged in?")
    return True, ""


def _symbol(pair):
    raw = pair.upper().replace(" ", "")
    if raw in ALIASES:
        s = ALIASES[raw]
    else:
        s = raw.replace("/", "")
        s = ALIASES.get(s, s)
    if mt5.symbol_info(s) is None:
        for suf in (".raw", "m", ".m", "-ECN", "#"):
            if mt5.symbol_info(s + suf) is not None:
                return s + suf
        return None
    return s


def mt5_live_price(pair: str = "EUR/USD") -> str:
    """Get the LIVE bid/ask and current spread for a pair from the broker.
    This is the real, tradeable price and spread — use it for the spread
    gate and before any entry decision."""
    ok, err = _connect()
    if not ok:
        return err
    try:
        sym = _symbol(pair)
        if not sym:
            return f"Symbol {pair} not found on this broker."
        mt5.symbol_select(sym, True)
        tick = mt5.symbol_info_tick(sym)
        info = mt5.symbol_info(sym)
        if tick is None:
            return f"No live tick for {sym}."

        pip = pip_size(sym, info)
        if pip is None:
            return (f"Cannot establish pip size for {sym} — the broker "
                    f"reported no digits. Refusing to quote a spread I "
                    f"cannot measure.")

        spread_pips = (tick.ask - tick.bid) / pip
        when = fmt_utc(tick.time, "%Y-%m-%d %H:%M:%S")

        warn = ""
        limit = spread_limit(info)
        if spread_pips > limit:
            warn = (f"  ** WIDE SPREAD (limit {limit}) — do NOT enter now "
                    f"(method rule) **")

        return (f"LIVE PRICE — {sym}\n"
                f"Bid: {tick.bid} | Ask: {tick.ask}\n"
                f"Spread: {spread_pips:.1f} pips{warn}\n"
                f"Digits: {info.digits} | Pip: {pip} | Time: {when}")
    finally:
        mt5.shutdown()


def mt5_candles(pair: str = "EUR/USD", interval: str = "15min",
                bars: int = 50) -> str:
    """Get recent OHLC candles straight from the broker with exact prices
    and timestamps. Use this instead of third-party data when precision
    matters. Times are true UTC, not the broker's server clock."""
    ok, err = _connect()
    if not ok:
        return err
    try:
        sym = _symbol(pair)
        if not sym:
            return f"Symbol {pair} not found on this broker."
        tf = TF_MAP.get(interval.lower())
        if tf is None:
            return f"Unknown interval '{interval}'. Use: {', '.join(TF_MAP)}"

        mt5.symbol_select(sym, True)
        rates = mt5.copy_rates_from_pos(sym, tf, 0, bars)
        if rates is None or len(rates) == 0:
            return f"No candle data for {sym} {interval}."

        info = mt5.symbol_info(sym)
        pip = pip_size(sym, info)
        if pip is None:
            return (f"Cannot establish pip size for {sym}. Add it to "
                    f"KNOWN_PIPS in tools/market.py — guessing it is a "
                    f"100x error.")

        closes = [r["close"] for r in rates]
        highs = [r["high"] for r in rates]
        lows = [r["low"] for r in rates]
        hi, lo = max(highs), min(lows)
        last = closes[-1]
        pos = ((last - lo) / (hi - lo) * 100) if hi > lo else 50

        lines = [f"BROKER CANDLES — {sym} {interval} ({len(rates)} bars)",
                 f"Latest close: {last} | Period high: {hi} | Period low: {lo}",
                 f"Price at {pos:.0f}% of range | Range: {(hi-lo)/pip:.0f} pips",
                 "",
                 "Last 10 candles (time UTC, O, H, L, C):"]
        for r in rates[-10:]:
            t = broker_utc(r["time"]).strftime("%m-%d %H:%M")
            lines.append(f"  {t}  {r['open']}  {r['high']}  "
                         f"{r['low']}  {r['close']}")
        return "\n".join(lines)
    finally:
        mt5.shutdown()


def mt5_account() -> str:
    """Get the live account state: balance, equity, margin, open positions
    and their P&L. Use for risk sizing and to see what is currently held."""
    ok, err = _connect()
    if not ok:
        return err
    try:
        acc = mt5.account_info()
        if acc is None:
            return "Could not read account info."
        lines = [f"ACCOUNT — {acc.server} (login {acc.login})",
                 f"Balance: {acc.balance} {acc.currency} | Equity: {acc.equity}",
                 f"Free margin: {acc.margin_free} | Leverage: 1:{acc.leverage}",
                 f"Profit (open): {acc.profit}"]

        positions = mt5.positions_get()
        if not positions:
            lines.append("\nOpen positions: none")
        else:
            lines.append(f"\nOpen positions ({len(positions)}):")
            for p in positions:
                side = "BUY" if p.type == 0 else "SELL"
                lines.append(f"  #{p.ticket} {p.symbol} {side} {p.volume} "
                             f"lots @ {p.price_open} | SL {p.sl} TP {p.tp} "
                             f"| P&L {p.profit}")
        return "\n".join(lines)
    finally:
        mt5.shutdown()


def mt5_history(days: int = 7) -> str:
    """Get closed trade history for the last N days — the SOURCE OF TRUTH
    for the journal and for the method's real win rate. Read figures from
    here rather than asking the user to remember them.

    The query window is built in SERVER time, because that is how MT5
    interprets the datetimes passed to it. Using local time here hid any
    trade closed in the last couple of hours."""
    ok, err = _connect()
    if not ok:
        return err
    try:
        now = server_now()
        # reach an hour past 'now' so a trade closing this minute is caught
        deals = mt5.history_deals_get(now - timedelta(days=days),
                                      now + timedelta(hours=1))
        if deals is None or len(deals) == 0:
            return f"No closed trades in the last {days} days."

        closed = [d for d in deals if d.entry == 1]      # exits only
        if not closed:
            return f"No completed trades in the last {days} days."

        wins = sum(1 for d in closed if d.profit > 0)
        total_profit = sum(d.profit for d in closed)

        lines = [f"TRADE HISTORY — last {days} days ({len(closed)} closed)",
                 f"Wins: {wins} | Losses: {len(closed)-wins} | "
                 f"Win rate: {wins/len(closed)*100:.0f}%",
                 f"Net P&L: {total_profit:.2f}", "", "Trades (time UTC):"]
        for d in closed[-15:]:
            t = broker_utc(d.time).strftime("%m-%d %H:%M")
            lines.append(f"  {t} {d.symbol} {d.volume} lots | "
                         f"P&L {d.profit:.2f} | ticket {d.position_id}")
        return "\n".join(lines)
    finally:
        mt5.shutdown()