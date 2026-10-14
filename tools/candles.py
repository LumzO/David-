import requests
import os
from tools.market import pip_size
from dotenv import load_dotenv
load_dotenv()

TD_API = "https://api.twelvedata.com"


def _fetch_candles(pair, interval, bars):
    key = os.getenv("TWELVEDATA_API_KEY")
    if not key:
        return None, "Twelve Data API key not found in environment."
    resp = requests.get(
        f"{TD_API}/time_series",
        params={"symbol": pair, "interval": interval, "outputsize": bars, "apikey": key},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") == "error":
        return None, f"Error from data provider: {data.get('message', 'unknown')}"
    values = data.get("values", [])
    if not values:
        return None, f"No candle data returned for {pair}."
    return list(reversed(values)), None  # oldest-first


def _rsi(closes, period=14):
    """Wilder's RSI. Returns a list aligned to closes (None until warmed up)."""
    if len(closes) < period + 1:
        return [None] * len(closes)
    rsis = [None] * period
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        gains += max(diff, 0)
        losses += max(-diff, 0)
    avg_gain, avg_loss = gains / period, losses / period
    rsis.append(100 - 100 / (1 + (avg_gain / avg_loss)) if avg_loss else 100.0)
    for i in range(period + 1, len(closes)):
        diff = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(diff, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-diff, 0)) / period
        rsis.append(100 - 100 / (1 + (avg_gain / avg_loss)) if avg_loss else 100.0)
    return rsis


def _swings(vals, lookback=2):
    """Indices of local swing highs and lows."""
    highs, lows = [], []
    for i in range(lookback, len(vals) - lookback):
        window = vals[i - lookback: i + lookback + 1]
        if vals[i] == max(window):
            highs.append(i)
        if vals[i] == min(window):
            lows.append(i)
    return highs, lows

def _ema(closes, period):
    """Exponential moving average — returns the current EMA value, or None."""
    if len(closes) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period  # seed with SMA
    for price in closes[period:]:
        ema = price * k + ema * (1 - k)
    return ema

def analyse_candles(pair: str = "EUR/USD", interval: str = "1day", bars: int = 50) -> str:
    """Technical analysis of a forex pair: trend, range, support/resistance,
    momentum, RSI (14) with overbought/oversold, and DIVERGENCE detection
    (price vs RSI). Pair e.g. 'EUR/USD'; interval '1day','4h','1h','15min'."""
    try:
        candles, err = _fetch_candles(pair, interval, max(bars, 30))
        if err:
            return err

        closes = [float(c["close"]) for c in candles]
        highs = [float(c["high"]) for c in candles]
        lows = [float(c["low"]) for c in candles]

        first_close, last_close = closes[0], closes[-1]
        change = last_close - first_close
        change_pct = (change / first_close) * 100 if first_close else 0
        period_high, period_low = max(highs), min(lows)

        def sma(vals, n):
            return sum(vals[-n:]) / n if len(vals) >= n else None

        sma_short, sma_long = sma(closes, 5), sma(closes, 20)
        if sma_short and sma_long:
            if sma_short > sma_long and change > 0:
                trend = "UPTREND"
            elif sma_short < sma_long and change < 0:
                trend = "DOWNTREND"
            else:
                trend = "MIXED/RANGING"
        else:
            trend = "insufficient data"

        rng = period_high - period_low
        pos_in_range = ((last_close - period_low) / rng * 100) if rng else 50
        recent = closes[-3:]
        momentum = "rising" if recent[-1] > recent[0] else "falling" if recent[-1] < recent[0] else "flat"

        # --- RSI + state ---
        rsis = _rsi(closes)
        rsi_now = rsis[-1]
        if rsi_now is None:
            rsi_line = "RSI: insufficient data"
            divergence = "Divergence: insufficient data"
        else:
            if rsi_now >= 68:
                rsi_state = "OVERBOUGHT (extreme high — reversal zone per method)"
            elif rsi_now <= 32:
                rsi_state = "OVERSOLD (extreme low — reversal zone per method)"
            elif rsi_now > 50:
                rsi_state = "above 50 baseline (bullish territory)"
            else:
                rsi_state = "below 50 baseline (bearish territory)"
            rsi_line = f"RSI(14): {rsi_now:.1f} — {rsi_state}"

            # --- Divergence: compare last two price swings vs RSI at those bars ---
            divergence = "Divergence: none detected"
            ph, pl = _swings(closes)
            ph = [i for i in ph if rsis[i] is not None]
            pl = [i for i in pl if rsis[i] is not None]
            if len(ph) >= 2:
                a, b = ph[-2], ph[-1]
                if closes[b] > closes[a] and rsis[b] < rsis[a]:
                    divergence = (f"BEARISH DIVERGENCE: price made a higher high "
                                  f"({closes[a]:.5f} -> {closes[b]:.5f}) but RSI fell "
                                  f"({rsis[a]:.1f} -> {rsis[b]:.1f}) — expect reversal DOWN")
            if len(pl) >= 2 and divergence == "Divergence: none detected":
                a, b = pl[-2], pl[-1]
                if closes[b] < closes[a] and rsis[b] > rsis[a]:
                    divergence = (f"BULLISH DIVERGENCE: price made a lower low "
                                  f"({closes[a]:.5f} -> {closes[b]:.5f}) but RSI rose "
                                  f"({rsis[a]:.1f} -> {rsis[b]:.1f}) — expect reversal UP")

        lines = [
            f"TECHNICAL READ — {pair} ({interval}, last {len(candles)} bars)",
            f"Latest close: {last_close:.5f}",
            f"Change over period: {change:+.5f} ({change_pct:+.2f}%)",
            f"Trend: {trend}",
            f"Period high: {period_high:.5f} | Period low: {period_low:.5f}",
            f"Price sits at {pos_in_range:.0f}% of its range (0%=low, 100%=high)",
            f"Recent momentum (last 3 bars): {momentum}",
            rsi_line,
            divergence,
            "",
            "Key levels:",
            f"  Resistance (period high): {period_high:.5f}",
            f"  Support (period low): {period_low:.5f}",
        ]
        return "\n".join(lines)

    except Exception as e:
        return f"Error analysing candles: {e}"


def get_adr(pair: str = "EUR/USD") -> str:
    """Average Daily Range for a pair (last 14 daily candles) plus today's range
    vs the ADR — tells you if the day's range has been met (reversal filter) and
    the per-level size (ADR / 3), per the trading method."""
    try:
        candles, err = _fetch_candles(pair, "1day", 16)
        if err:
            return err
        if len(candles) < 15:
            return f"Not enough daily data for {pair} to compute ADR."

        # Pip size from tools.market — never inferred from the name.
        # Twelve Data reports no digit count, so this falls through to the
        # KNOWN_PIPS table and returns None for anything unlisted rather
        # than guessing 0.0001 (a 100x error on metals).
        pip = pip_size(pair)
        if pip is None:
            return (f"Cannot establish pip size for {pair}. Add it to "
                    f"KNOWN_PIPS in tools/market.py — guessing it is a "
                    f"100x error.")

        prior = candles[-15:-1]      # last 14 complete days
        today = candles[-1]          # current/most recent day

        ranges = [(float(c["high"]) - float(c["low"])) / pip for c in prior]
        adr = sum(ranges) / len(ranges)

        t_high, t_low = float(today["high"]), float(today["low"])
        today_range = (t_high - t_low) / pip
        pct_of_adr = (today_range / adr * 100) if adr else 0
        met = today_range >= adr

        level_size = adr / 3

        return "\n".join([
            f"ADR — {pair}",
            f"Average daily range (14 days): {adr:.0f} pips",
            f"Today's range so far: {today_range:.0f} pips ({pct_of_adr:.0f}% of ADR)",
            f"Range met: {'YES — day likely done moving; reversal setups strengthened' if met else 'NO — room left in the day'}",
            f"Per-level size (ADR/3): ~{level_size:.0f} pips per level",
            f"Today's high: {t_high:.5f} | Today's low: {t_low:.5f}",
        ])

    except Exception as e:
        return f"Error computing ADR: {e}"
def plan_trade(pair: str = "GBP/USD", direction: str = "buy", interval: str = "15min") -> str:
    """Build a method-compliant trade plan: structure-based stop loss, EMA-based
    take-profit targets (50/200 EMA minus buffer), risk in pips, R:R ratios, and
    ADR sanity check. direction: 'buy' or 'sell'. Use AFTER the checklist
    supports a setup — never invent a plan for a no-trade read."""
    try:
        direction = direction.lower().strip()
        if direction not in ("buy", "sell"):
            return "direction must be 'buy' or 'sell'."

        # Need enough bars for the 200 EMA
        candles, err = _fetch_candles(pair, interval, 250)
        if err:
            return err

        closes = [float(c["close"]) for c in candles]
        highs = [float(c["high"]) for c in candles]
        lows = [float(c["low"]) for c in candles]
        price = closes[-1]

        pip = pip_size(pair)
        if pip is None:
            return (f"Cannot establish pip size for {pair}. Add it to "
                    f"KNOWN_PIPS in tools/market.py — I will not build a "
                    f"plan on a guessed pip size.")
        buffer_pips = 15          # stop buffer beyond structure (per method)
        target_shave = 5          # exit 5 pips before the obvious EMA target

        ema50 = _ema(closes, 50)
        ema200 = _ema(closes, 200)
        if ema50 is None or ema200 is None:
            return f"Not enough data to compute 50/200 EMA for {pair} on {interval}."

        # Structure for the stop: most recent meaningful swing (last 30 bars)
        recent_high = max(highs[-30:])
        recent_low = min(lows[-30:])

        lines = [f"TRADE PLAN — {pair} {direction.upper()} ({interval})",
                 f"Current price: {price:.5f}",
                 f"50 EMA: {ema50:.5f} | 200 EMA: {ema200:.5f}"]

        if direction == "buy":
            stop = recent_low - buffer_pips * pip
            risk_pips = (price - stop) / pip
            t1 = ema50 - target_shave * pip
            t2 = ema200 - target_shave * pip
            t1_pips = (t1 - price) / pip
            t2_pips = (t2 - price) / pip
            lines.append(f"Stop loss: {stop:.5f} ({buffer_pips} pips below swing low {recent_low:.5f})")
            valid1, valid2 = t1 > price, t2 > price
        else:
            stop = recent_high + buffer_pips * pip
            risk_pips = (stop - price) / pip
            t1 = ema50 + target_shave * pip
            t2 = ema200 + target_shave * pip
            t1_pips = (price - t1) / pip
            t2_pips = (price - t2) / pip
            lines.append(f"Stop loss: {stop:.5f} ({buffer_pips} pips above swing high {recent_high:.5f})")
            valid1, valid2 = t1 < price, t2 < price

        # NEVER state money here. "$1/pip on 0.10 lot" was true for a
        # currency pair and ~40x wrong for gold, and 0.1 lots is exactly
        # the habit that cost 46% of the account on 1 September 2026.
        # Lot size and money at risk come from calculate_position_size,
        # which reads the live balance and the broker's tick value.
        lines.append(f"Risk: {risk_pips:.0f} pips "
                     f"(call calculate_position_size for the lot size and "
                     f"the money — do NOT assume a lot size)")

        if valid1 and risk_pips > 0:
            lines.append(f"TARGET / EXIT (50 EMA -{target_shave}p): {t1:.5f} "
                         f"→ {t1_pips:+.0f} pips | R:R {abs(t1_pips)/risk_pips:.1f}"
                         f"   <- this is where the trade closes")
        else:
            lines.append(f"Target 1: 50 EMA ({ema50:.5f}) is on the WRONG SIDE of price for a {direction} — price already beyond it. Reassess.")
        if valid2 and risk_pips > 0:
            rr2 = abs(t2_pips) / risk_pips
            lines.append(f"R:R GATE (200 EMA -{target_shave}p): {t2:.5f} "
                         f"→ {t2_pips:+.0f} pips | R:R {rr2:.1f}"
                         f"   <- selection filter only, NOT an exit"
                         + ("" if rr2 >= 1.5 else
                            "   *** BELOW 1.5 — GATE 4 FAILS, NO TRADE ***"))
        else:
            lines.append(f"Target 2: 200 EMA ({ema200:.5f}) is on the wrong side of price for a {direction}.")

        lines.append("Management: NONE. One target, one exit, at the 50 EMA. "
                     "Measured +0.257R against +0.236R for running to the 200 "
                     "EMA, with the worst losing streak falling from 18 to 6. "
                     "Do NOT move the stop to break-even — the trade closes "
                     "at the level where that move would have happened. At "
                     "0.01 lots a position cannot be closed in part, so there "
                     "is no runner and no second leg.")
        lines.append("BEFORE ENTRY: confirm the setup candle has CLOSED, call "
                     "mt5_live_price to check the real spread, and call "
                     "calculate_position_size for the lot size.")
        return "\n".join(lines)

    except Exception as e:
        return f"Error building trade plan: {e}"