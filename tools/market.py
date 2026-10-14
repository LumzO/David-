"""
One source of truth for the two things this project keeps getting wrong.

Both bugs here have already cost real money or real time.

  PIP SIZE FROM THE INSTRUMENT NAME
    `0.01 if "JPY" in name else 0.0001` gives 0.0001 on gold, which is
    100x too small. On 1 September 2026 a related assumption — carrying a
    lot size from a currency pair to gold — cost 229.09 on a 500 account,
    46% in one trade. The rule "read it from the broker, never from the
    name" was then written into values.py as prose, and the bug was
    reproduced six more times across candles.py, pips.py and mt5_chart.py
    anyway. Prose does not stop a bug recurring. A shared function does.

  MT5 TIMES AND THE SERVER CLOCK
    MT5 hands back SERVER time as a unix timestamp, and interprets any
    datetime you pass IN as server time too. Both directions were wrong:
      - reading: `fromtimestamp(ts, tz=utc)` was 3 hours late, which put
        the backtester's session window 3 hours early for two years. It
        was testing Asian-session setups as London ones and silently
        discarding every Friday setup (21% of the method's profit).
      - writing: `datetime.now()` passed to history_deals_get queried a
        window 2 hours in the server's past, so recently closed trades
        were invisible to mt5_history — the journal's source of truth.

Everything that touches an MT5 price or an MT5 timestamp imports from
here. Nothing re-derives either.

NOTE: this applies to MT5 ONLY. Finnhub, Twelve Data and Forex Factory
return genuine Unix UTC — do not run their timestamps through
broker_utc().
"""

from datetime import datetime, timedelta, timezone

try:
    import MetaTrader5 as mt5
except Exception:                      # usable without MT5 installed
    mt5 = None

# The broker's clock follows EU daylight saving: UTC+2 in winter, UTC+3 in
# summer. Verified 3 Oct 2026 — the Friday 17:00 ET close landed at
# 23:59:40 server time, which is only true at UTC+3.
#
# Using a zone rather than a fixed number matters because the UK, the EU
# and the US do not change clocks on the same dates. On 25 Oct 2026 the EU
# goes back; the US waits until 1 Nov. For that week server-minus-Eastern
# is 6 hours, not the usual 7. A hardcoded offset is silently wrong for
# that week every year.
SERVER_TZ_NAME = "Europe/Riga"         # UTC+2 / UTC+3, EU DST dates
FALLBACK_OFFSET = 3                    # used only if zoneinfo is missing

try:
    from zoneinfo import ZoneInfo
    SERVER_TZ = ZoneInfo(SERVER_TZ_NAME)
    ET_TZ = ZoneInfo("America/New_York")
except Exception:                      # no tzdata: fall back to fixed hours
    SERVER_TZ = None
    ET_TZ = None


# Known pip sizes for instruments where the broker cannot be asked —
# Twelve Data and other feeds report no digit count. Extend this rather
# than guessing from the name or from the price magnitude.
KNOWN_PIPS = {
    # FX, 5-digit quotes
    "EURUSD": 0.0001, "GBPUSD": 0.0001, "AUDUSD": 0.0001,
    "NZDUSD": 0.0001, "USDCAD": 0.0001, "USDCHF": 0.0001,
    "EURGBP": 0.0001, "EURCHF": 0.0001, "EURAUD": 0.0001,
    "GBPAUD": 0.0001, "GBPCAD": 0.0001, "AUDCAD": 0.0001,
    "AUDNZD": 0.0001, "EURCAD": 0.0001, "GBPCHF": 0.0001,
    "AUDCHF": 0.0001, "CADCHF": 0.0001, "NZDCAD": 0.0001,
    # JPY crosses, 3-digit quotes
    "USDJPY": 0.01, "EURJPY": 0.01, "GBPJPY": 0.01, "AUDJPY": 0.01,
    "CADJPY": 0.01, "CHFJPY": 0.01, "NZDJPY": 0.01,
    # Metals
    "XAUUSD": 0.01, "XAGUSD": 0.01,
    "XAUEUR": 0.01, "XAUGBP": 0.01, "XAUAUD": 0.01, "XAUCHF": 0.01,
}

ALIASES = {
    "GOLD": "XAUUSD", "XAUUSD": "XAUUSD", "XAU/USD": "XAUUSD",
    "SILVER": "XAGUSD", "XAGUSD": "XAGUSD", "XAG/USD": "XAGUSD",
}


# ----------------------------------------------------------------------
# time — reading FROM MT5
# ----------------------------------------------------------------------

def broker_utc(ts):
    """An MT5 timestamp -> true UTC.

    Use this for EVERY MT5 time: ticks, candles, deals, orders, positions.
    Never call datetime.fromtimestamp on an MT5 timestamp directly.
    """
    wall = datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)
    if SERVER_TZ is not None:
        return wall.replace(tzinfo=SERVER_TZ).astimezone(timezone.utc)
    return wall.replace(tzinfo=timezone.utc) - timedelta(hours=FALLBACK_OFFSET)


def broker_et(ts):
    """An MT5 timestamp -> US Eastern, the clock the framework thinks in
    (London 03:00-09:00 ET, New York 09:30-17:00 ET)."""
    u = broker_utc(ts)
    if ET_TZ is not None:
        return u.astimezone(ET_TZ)
    return u - timedelta(hours=4)


def fmt_utc(ts, fmt="%Y-%m-%d %H:%M"):
    """Format an MT5 timestamp as true UTC, labelled so nobody has to
    guess which clock it is on."""
    return broker_utc(ts).strftime(fmt) + " UTC"


def fmt_et(ts, fmt="%Y-%m-%d %H:%M"):
    """Format an MT5 timestamp as US Eastern, labelled."""
    return broker_et(ts).strftime(fmt) + " ET"


# ----------------------------------------------------------------------
# time — writing TO MT5
# ----------------------------------------------------------------------

def server_now():
    """Current time on the broker's server, naive — the form MT5 wants for
    history_deals_get, history_orders_get and copy_rates_range.

    MT5 interprets datetimes you pass IN as server time, so handing it
    datetime.now() (local) queries the wrong window. In London summer that
    is 2 hours behind the server, so the most recently closed trades go
    missing from mt5_history.
    """
    if SERVER_TZ is not None:
        return datetime.now(SERVER_TZ).replace(tzinfo=None)
    return (datetime.now(timezone.utc)
            + timedelta(hours=FALLBACK_OFFSET)).replace(tzinfo=None)


def to_server(dt):
    """Any aware datetime -> naive server time, for passing into MT5."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if SERVER_TZ is not None:
        return dt.astimezone(SERVER_TZ).replace(tzinfo=None)
    return (dt.astimezone(timezone.utc)
            + timedelta(hours=FALLBACK_OFFSET)).replace(tzinfo=None)


def clock_check():
    """Report the broker offset as it is RIGHT NOW. Run this after any
    clock change to confirm the conversions are still right."""
    now_utc = datetime.now(timezone.utc)
    srv = server_now()
    offset = round((srv.replace(tzinfo=timezone.utc) - now_utc).total_seconds() / 3600)
    lines = [
        f"true UTC      : {now_utc.strftime('%Y-%m-%d %H:%M')}",
        f"server clock  : {srv.strftime('%Y-%m-%d %H:%M')}  (UTC+{offset})",
        f"zone source   : {SERVER_TZ_NAME if SERVER_TZ else 'FIXED FALLBACK'}",
    ]
    if ET_TZ is not None:
        et = now_utc.astimezone(ET_TZ)
        lines.append(f"US Eastern    : {et.strftime('%Y-%m-%d %H:%M %Z')}")
        lines.append(f"server - ET   : {offset + abs(int(et.utcoffset().total_seconds() // 3600))} hours")
    if mt5 is not None:
        try:
            if mt5.initialize():
                t = mt5.symbol_info_tick("EURUSD")
                if t:
                    lines.append(f"last MT5 tick : {fmt_utc(t.time, '%Y-%m-%d %H:%M:%S')}")
                mt5.shutdown()
        except Exception:
            pass
    return "\n".join(lines)


# ----------------------------------------------------------------------
# pip size
# ----------------------------------------------------------------------
def _ask_mt5(symbol):
    """Read symbol_info, connecting only if nobody else already has.

    pip_size is called from places that manage their own MT5 connection
    and from places that don't. Shutting down a connection we didn't open
    would break the caller, so check terminal_info first and only tear
    down what we set up.
    """
    if mt5 is None:
        return None
    try:
        mine = mt5.terminal_info() is None
        if mine and not mt5.initialize():
            return None
        try:
            return mt5.symbol_info(symbol)
        finally:
            if mine:
                mt5.shutdown()
    except Exception:
        return None
    
def resolve(instrument):
    """Normalise a user-facing name to a broker symbol, following aliases
    and trying the common broker suffixes."""
    raw = str(instrument).upper().replace(" ", "")
    name = ALIASES.get(raw, raw.replace("/", ""))
    name = ALIASES.get(name, name)
    if mt5 is None:
        return name
    try:
        if _ask_mt5(name) is not None:
            return name
        for suffix in (".raw", "m", ".m", "-ECN", "#", ".pro"):
            if _ask_mt5(name + suffix) is not None:
                return name + suffix
    except Exception:
        pass
    return name


def pip_size(instrument, info=None):
    """Pip size for an instrument, or None if it cannot be established.

    Order of trust:
      1. the broker's own digit count, which is always right
      2. the KNOWN_PIPS table, for feeds that report no digits
      3. nothing — returns None

    It returns None rather than guessing. A wrong pip size is a 100x error
    in every figure downstream, so the caller must handle the unknown case
    by refusing, not by falling back to 0.0001.
    """
    name = resolve(instrument)

    if info is None:
        info = _ask_mt5(name)

    if info is not None and getattr(info, "digits", None):
        return 0.01 if info.digits <= 3 else 0.0001

    bare = name
    for suffix in (".raw", "m", ".m", "-ECN", "#", ".pro"):
        if bare.endswith(suffix):
            bare = bare[: -len(suffix)]
            break
    if bare in KNOWN_PIPS:
        return KNOWN_PIPS[bare]

    return None


def pip_size_or_raise(instrument, info=None):
    """pip_size, but raises instead of returning None. Use where a missing
    pip size should stop the operation rather than be handled."""
    p = pip_size(instrument, info)
    if p is None:
        raise ValueError(
            f"Cannot establish pip size for '{instrument}'. The broker "
            f"reported no digits and it is not in KNOWN_PIPS. Add it to "
            f"KNOWN_PIPS in tools/market.py rather than guessing — a wrong "
            f"pip size is a 100x error in every figure downstream."
        )
    return p


def pips_between(instrument, a, b, info=None):
    """Distance between two prices in pips, or None if the pip size is
    unknown. Use this instead of dividing by a local pip constant."""
    p = pip_size(instrument, info)
    if p is None:
        return None
    return abs(float(a) - float(b)) / p


def spread_limit(info):
    """Max acceptable spread in pips for this instrument's precision.
    Metals and indices run naturally wider than FX majors."""
    d = getattr(info, "digits", 5)
    if d <= 2:
        return 40
    if d == 3:
        return 3
    return 5    
    """Distance between two prices in pips, or None if the pip size is
    unknown. Always use this instead of dividing by a local pip constant."""
    p = pip_size(instrument, info)
    if p is None:
        return None
    return abs(float(a) - float(b)) / p