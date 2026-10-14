import requests
import json
import os
from datetime import datetime, timezone, timedelta

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
CACHE_FILE = "ff_calendar_cache.json"
CACHE_HOURS = 12  # re-download at most twice a day to avoid being blocked


def _fetch_and_cache():
    """Download the Forex Factory calendar and cache it locally."""
    resp = requests.get(FF_URL, timeout=10)
    resp.raise_for_status()
    events = resp.json()
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "events": events,
    }
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    return events


def _load_events():
    """Return calendar events, using the cache if it's fresh enough."""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                payload = json.load(f)
            fetched = datetime.fromisoformat(payload["fetched_at"])
            if datetime.now(timezone.utc) - fetched < timedelta(hours=CACHE_HOURS):
                return payload["events"]  # cache still fresh
        except Exception:
            pass
    # Cache missing or stale — refetch
    return _fetch_and_cache()


def get_economic_calendar(currency: str = "", impact: str = "", days: int = 7) -> str:
    """Get upcoming economic calendar events from Forex Factory.
    Filter by currency (e.g. 'USD', 'EUR', 'GBP') and/or impact ('High','Medium','Low').
    'days' limits how far ahead to look. Use this to see scheduled market-moving events
    like rate decisions, CPI, and jobs data."""
    try:
        events = _load_events()
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(days=days)

        cur = currency.upper().strip()
        imp = impact.capitalize().strip()

        upcoming = []
        for e in events:
            # Parse the event date (has timezone offset)
            try:
                edate = datetime.fromisoformat(e["date"])
                edate_utc = edate.astimezone(timezone.utc)
            except Exception:
                continue

            if edate_utc < now or edate_utc > cutoff:
                continue
            if cur and e.get("country", "").upper() != cur:
                continue
            if imp and e.get("impact", "") != imp:
                continue

            upcoming.append((edate_utc, e))

        if not upcoming:
            filt = f" ({currency} {impact})".strip()
            return f"No upcoming events found{filt} in the next {days} days."

        upcoming.sort(key=lambda x: x[0])

        lines = [f"ECONOMIC CALENDAR (next {days} days){' — ' + cur if cur else ''}{' ' + imp + ' impact' if imp else ''}:\n"]
        for edate_utc, e in upcoming[:25]:
            when = edate_utc.strftime("%a %d %b %H:%M UTC")
            title = e.get("title", "?")
            country = e.get("country", "")
            impact_lvl = e.get("impact", "")
            forecast = e.get("forecast", "") or "—"
            previous = e.get("previous", "") or "—"
            lines.append(
                f"• {when} | {country} | {title} [{impact_lvl}]\n"
                f"    forecast: {forecast} | previous: {previous}"
            )

        return "\n".join(lines)

    except Exception as e:
        return f"Error fetching economic calendar: {e}"