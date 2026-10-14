"""
Three-clock fluency for David:
  UTC        — raw data (MT5 candles, calendar feed)
  US Eastern — the trading method's reference (sessions, 5pm reset)
  UK local   — the user's own clock (auto-handles BST)
"""
from datetime import datetime, timezone, timedelta

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
    UK = ZoneInfo("Europe/London")
except Exception:
    ET = UK = timezone.utc


def _session_now(et_dt):
    """Which trading session is active, per the method's Eastern-time map."""
    h = et_dt.hour + et_dt.minute / 60
    wd = et_dt.weekday()          # 0=Mon .. 6=Sun

    # Market closed: Fri 17:00 ET -> Sun 17:00 ET
    if (wd == 4 and h >= 17) or wd == 5 or (wd == 6 and h < 17):
        return "MARKET CLOSED (weekend)", False

    if 17 <= h < 20:
        return "Dead gap (5pm-8pm ET) — new day started, no volume", True
    if h >= 20 or h < 3:
        return "ASIAN — accumulation. Range should be <=50 pips. Do not trade.", True
    if 3 <= h < 3.5:
        return "London pre-open", True
    if 3.5 <= h < 9:
        return "LONDON — tradeable. Watch stop hunt then trend.", True
    if 9 <= h < 9.5:
        return "NY pre-open", True
    return "NEW YORK — tradeable. Watch for M at high / W at low.", True


def get_time(detail: str = "") -> str:
    """Get the current time across all three clocks the user needs, plus which
    trading session is active and where we are in the weekly cycle.
    Call this before any session-based reasoning or timing judgment."""
    now_utc = datetime.now(timezone.utc)
    now_et = now_utc.astimezone(ET)
    now_uk = now_utc.astimezone(UK)

    session, open_now = _session_now(now_et)

    # Weekly cycle position, per the method
    wd = now_et.weekday()
    week_map = {
        6: "Sunday — weekly ACCUMULATION. Do not trade.",
        0: "Monday — weekly ACCUMULATION / trap day. Do not trade.",
        1: "Tuesday — SAFETY TRADE day. Identify who got trapped.",
        2: "Wednesday — MIDWEEK REVERSAL window (best weekly setup).",
        3: "Thursday — reversal can still run; levels building.",
        4: "Friday — END-OF-WEEK TRAP. Careful; second legs may stall.",
        5: "Saturday — market closed.",
    }
    week_note = week_map.get(wd, "")

    # Time until the 5pm ET day reset
    reset = now_et.replace(hour=17, minute=0, second=0, microsecond=0)
    if now_et >= reset:
        reset += timedelta(days=1)
    to_reset = reset - now_et
    hrs, rem = divmod(int(to_reset.total_seconds()), 3600)
    mins = rem // 60

    return "\n".join([
        f"UTC (data):        {now_utc.strftime('%a %d %b %Y  %H:%M')}",
        f"US EASTERN (method): {now_et.strftime('%a %d %b %Y  %H:%M %Z')}",
        f"UK LOCAL (you):    {now_uk.strftime('%a %d %b %Y  %H:%M %Z')}",
        "",
        f"Market: {'OPEN' if open_now else 'CLOSED'}",
        f"Session: {session}",
        f"Weekly cycle: {week_note}",
        f"Next 5pm ET day reset in: {hrs}h {mins}m",
    ])


def convert_time(time_str: str, from_zone: str = "utc") -> str:
    """Convert a given time between UTC, US Eastern and UK local.
    time_str like '14:30' or '2026-08-05 14:30'. from_zone: utc | et | uk."""
    try:
        s = time_str.strip()
        if len(s) <= 5 and ":" in s:
            base = datetime.now(timezone.utc).strftime("%Y-%m-%d ")
            s = base + s
        naive = datetime.strptime(s, "%Y-%m-%d %H:%M")

        zones = {"utc": timezone.utc, "et": ET, "uk": UK}
        src = zones.get(from_zone.lower().strip(), timezone.utc)
        aware = naive.replace(tzinfo=src)

        return "\n".join([
            f"UTC:        {aware.astimezone(timezone.utc).strftime('%a %d %b %H:%M')}",
            f"US Eastern: {aware.astimezone(ET).strftime('%a %d %b %H:%M %Z')}",
            f"UK local:   {aware.astimezone(UK).strftime('%a %d %b %H:%M %Z')}",
        ])
    except Exception as e:
        return f"Could not parse '{time_str}' ({e}). Use '14:30' or '2026-08-05 14:30'."