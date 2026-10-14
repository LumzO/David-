"""
Render candlestick charts from MT5 broker data with David's analysis drawn on.
Two modes:
  clean  — candles + only the levels David specifies
  full   — candles + EMAs (5/13/50/200) + ADR bands + session shading + levels
"""
import os
from datetime import datetime, timezone, timedelta

import matplotlib
matplotlib.use("Agg")          # no GUI backend needed
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle

import MetaTrader5 as mt5

# MT5 reports bar times as SERVER time dressed as a unix timestamp.
# MetaQuotes-Demo runs UTC+3 (verified 3 Oct 2026). Treating those stamps
# as UTC — which this file did until 4 Oct — put every chart's x-axis and
# the Asian-session shading 3 hours out.
SERVER_UTC_OFFSET = 3


def _utc(ts):
    """Broker-server timestamp -> true UTC."""
    return (datetime.fromtimestamp(ts, tz=timezone.utc)
            - timedelta(hours=SERVER_UTC_OFFSET))


TF_MAP = {
    "1min": mt5.TIMEFRAME_M1, "5min": mt5.TIMEFRAME_M5,
    "15min": mt5.TIMEFRAME_M15, "30min": mt5.TIMEFRAME_M30,
    "1h": mt5.TIMEFRAME_H1, "4h": mt5.TIMEFRAME_H4,
    "1day": mt5.TIMEFRAME_D1, "1week": mt5.TIMEFRAME_W1,
}

LINE_STYLE = {
    "entry":  dict(color="#1E90FF", linestyle="-",  linewidth=1.8),
    "stop":   dict(color="#DC2828", linestyle="--", linewidth=1.6),
    "target": dict(color="#28B450", linestyle="--", linewidth=1.6),
    "level":  dict(color="#FFB400", linestyle=":",  linewidth=1.3),
    "note":   dict(color="#AA5ADC", linestyle=":",  linewidth=1.1),
}


def _symbol(pair):
    s = pair.upper().replace("/", "").replace(" ", "")
    if mt5.symbol_info(s) is None:
        for suf in (".raw", "m", ".m", "-ECN", "#"):
            if mt5.symbol_info(s + suf) is not None:
                return s + suf
        return None
    return s


def _ema(vals, period):
    if len(vals) < period:
        return [None] * len(vals)
    k = 2 / (period + 1)
    out = [None] * (period - 1)
    ema = sum(vals[:period]) / period
    out.append(ema)
    for v in vals[period:]:
        ema = v * k + ema * (1 - k)
        out.append(ema)
    return out


def render_mt5_chart(pair: str = "EUR/USD", interval: str = "15min", bars: int = 80,
                     levels: str = "", mode: str = "clean", title: str = "",
                     output_name: str = "") -> str:
    """Draw a candlestick chart from BROKER data with your analysis marked on it,
    then save and open it so the user can see exactly where your levels sit.

    levels: semicolon-separated 'type,price,label' using ACTUAL PRICES
      type = entry | stop | target | level | note
      e.g. 'level,1.15472,Swing high; entry,1.15100,SELL; stop,1.15570,SL; target,1.14800,TP1'
    mode: 'clean' (candles + your levels only) or
          'full'  (adds 5/13/50/200 EMAs, ADR high/low bands, Asian session shading)
    """
    if not mt5.initialize():
        return f"Could not connect to MT5 terminal ({mt5.last_error()}). Is it running?"
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

        times = [_utc(r["time"]) for r in rates]
        opens = [float(r["open"]) for r in rates]
        highs = [float(r["high"]) for r in rates]
        lows = [float(r["low"]) for r in rates]
        closes = [float(r["close"]) for r in rates]
        # Pip from the broker's digits, NEVER from the name. Inferring it
        # from "JPY" gave 0.0001 on 2-digit metals and indices — a 100x
        # error in every pip figure on the chart. Same class of mistake as
        # the 1 September gold sizing error.
        _si = mt5.symbol_info(sym)
        pip = 0.01 if (_si and _si.digits <= 3) else 0.0001

        fig, ax = plt.subplots(figsize=(14, 7.5), facecolor="#0e1117")
        ax.set_facecolor("#0e1117")

        # candle width in days for matplotlib date axis
        if len(times) > 1:
            step = (times[1] - times[0]).total_seconds() / 86400.0
        else:
            step = 0.01
        w = step * 0.62

        for t, o, h, l, c in zip(times, opens, highs, lows, closes):
            up = c >= o
            colour = "#26a69a" if up else "#ef5350"
            x = mdates.date2num(t)
            ax.vlines(x, l, h, color=colour, linewidth=0.9)
            ax.add_patch(Rectangle((x - w / 2, min(o, c)), w, max(abs(c - o), pip * 0.1),
                                   facecolor=colour, edgecolor=colour, linewidth=0.5))

        drawn = []

        if mode.lower() == "full":
            for period, colour, lw in ((5, "#FFD700", 0.9), (13, "#FF6B6B", 0.9),
                                       (50, "#4FC3F7", 1.3), (200, "#FFFFFF", 1.3)):
                e = _ema(closes, period)
                pts = [(t, v) for t, v in zip(times, e) if v is not None]
                if pts:
                    ax.plot([p[0] for p in pts], [p[1] for p in pts],
                            color=colour, linewidth=lw, alpha=0.85, label=f"EMA{period}")
            drawn.append("EMAs 5/13/50/200")

            # ADR bands from the last 14 daily candles
            daily = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_D1, 0, 15)
            if daily is not None and len(daily) >= 15:
                rng = [(float(d["high"]) - float(d["low"])) for d in daily[-15:-1]]
                adr = sum(rng) / len(rng)
                today_open = float(daily[-1]["open"])
                ax.axhline(today_open + adr / 2, color="#FF8A65", linestyle="-.",
                           linewidth=1.0, alpha=0.8)
                ax.axhline(today_open - adr / 2, color="#FF8A65", linestyle="-.",
                           linewidth=1.0, alpha=0.8)
                ax.text(times[0], today_open + adr / 2, f" ADR high  ({adr/pip:.0f}p ADR)",
                        color="#FF8A65", fontsize=8, va="bottom")
                ax.text(times[0], today_open - adr / 2, " ADR low",
                        color="#FF8A65", fontsize=8, va="top")
                drawn.append(f"ADR bands ({adr/pip:.0f} pips)")

            # Asian session shading. The framework defines Asian as
            # 20:00-03:00 ET, which in true UTC (EDT) is 00:00-07:00.
            # Correct only now that `times` are true UTC rather than
            # server time.
            shaded = set()
            for t in times:
                key = t.date()
                if key in shaded:
                    continue
                a_start = datetime(t.year, t.month, t.day, 0, 0, tzinfo=timezone.utc)
                a_end = a_start + timedelta(hours=7)
                if times[0] <= a_end and a_start <= times[-1]:
                    ax.axvspan(max(a_start, times[0]), min(a_end, times[-1]),
                               color="#5C6BC0", alpha=0.12)
                    shaded.add(key)
            if shaded:
                drawn.append("Asian session shading")

        # --- user/David levels, at ACTUAL prices ---
        for raw in levels.split(";"):
            parts = [p.strip() for p in raw.split(",")]
            if len(parts) < 3:
                continue
            kind = parts[0].lower()
            try:
                price = float(parts[1])
            except ValueError:
                continue
            label = ",".join(parts[2:]).strip()
            style = LINE_STYLE.get(kind, LINE_STYLE["note"])
            ax.axhline(price, **style)
            ax.text(times[-1], price, f" {label} ", color="white", fontsize=9,
                    va="center", ha="left",
                    bbox=dict(facecolor=style["color"], edgecolor="none",
                              boxstyle="round,pad=0.25"))
            drawn.append(f"{kind} @ {price} — {label}")

        ax.set_title(title or (f"{sym}  {interval}  —  broker data, times in UTC  "
                               f"({len(rates)} bars)"),
                     color="white", fontsize=12, loc="left")
        ax.grid(color="#2a2e39", linewidth=0.5, alpha=0.6)
        ax.tick_params(colors="#b0b6c0", labelsize=8)
        for s in ax.spines.values():
            s.set_color("#2a2e39")
        ax.yaxis.tick_right()
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
        fig.autofmt_xdate(rotation=0, ha="center")
        if mode.lower() == "full":
            ax.legend(facecolor="#0e1117", edgecolor="#2a2e39", labelcolor="#b0b6c0",
                      fontsize=8, loc="upper left")
        fig.tight_layout()

        out_name = output_name or f"{sym}_{interval}_{mode}.png"
        out_path = os.path.join(os.getcwd(), out_name)
        fig.savefig(out_path, dpi=130, facecolor="#0e1117")
        plt.close(fig)

        try:
            os.startfile(out_path)
        except Exception:
            pass

        summary = (f"Chart rendered from broker data and opened: {out_path}\n"
                   f"{sym} {interval}, {len(rates)} bars, mode={mode}\n"
                   f"Latest close: {closes[-1]} | high {max(highs)} | low {min(lows)}")
        if drawn:
            summary += "\nDrawn:\n  " + "\n  ".join(drawn)
        return summary

    finally:
        mt5.shutdown()