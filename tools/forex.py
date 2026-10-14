import requests
import os
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv()

FINNHUB_API = "https://finnhub.io/api/v1"


def get_forex_news(limit: int = 8) -> str:
    """Get the latest forex market news headlines from Finnhub.
    Use this for macro context on currencies, central banks, and geopolitical
    events affecting forex markets."""
    try:
        key = os.getenv("FINNHUB_API_KEY")
        if not key:
            return "Finnhub API key not found in environment."

        resp = requests.get(
            f"{FINNHUB_API}/news",
            params={"category": "forex", "token": key},
            timeout=10,
        )
        resp.raise_for_status()
        articles = resp.json()

        if not articles:
            return "No forex news available right now."

        lines = []
        for a in articles[:limit]:
            headline = a.get("headline", "").strip()
            source = a.get("source", "")
            ts = a.get("datetime", 0)
            when = ""
            if ts:
                when = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            # Strip HTML from summary, keep it short
            import re
            summary = a.get("summary", "").strip()
            summary = re.sub(r"<[^>]+>", " ", summary)  # remove HTML tags
            summary = re.sub(r"\s+", " ", summary).strip()[:150]

            lines.append(
                f"• {headline}\n"
                f"  ({source}, {when} UTC)\n"
                f"  {summary}..."
            )

        return "\n\n".join(lines)

    except Exception as e:
        return f"Error fetching forex news: {e}"