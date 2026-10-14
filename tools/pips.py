"""
Pip conversions.

Rewritten 4 Oct 2026. The original inferred pip size from the instrument
name — `0.01 if "JPY" in pair else 0.0001` — in four separate functions.
That is the same assumption that made 0.1 lots on gold behave like 0.1
lots on a currency pair and cost 46% of the account on 1 September 2026.
It gives 0.0001 for XAUUSD, which is 100x too small, so every figure
derived from it is wrong by two orders of magnitude.

Pip size now comes from tools.market, which reads the broker's digit
count and falls back to an explicit table. When it cannot establish a pip
size it returns None, and so does everything here. A None is a refusal to
answer, not a reason to substitute 0.0001.
"""

from tools.market import pip_size, resolve, _ask_mt5


def pips_to_price(pair: str, pips: float):
    """Convert pips to a price movement for a given instrument.

    Returns None if the pip size cannot be established — add the symbol to
    KNOWN_PIPS in tools/market.py rather than guessing.

    pips_to_price('EUR/USD', 20) -> 0.0020
    pips_to_price('USD/JPY', 20) -> 0.20
    pips_to_price('XAUUSD', 20)  -> 0.20     (was 0.0020 before the fix)
    """
    pip = pip_size(pair)
    if pip is None:
        return None
    return float(pips) * pip


def price_to_pips(pair: str, price_move: float):
    """Convert a price movement to pips for a given instrument.

    Returns None if the pip size cannot be established.

    price_to_pips('EUR/USD', 0.0050) -> 50
    price_to_pips('USD/JPY', 0.50)   -> 50
    price_to_pips('XAUUSD', 0.50)    -> 50   (was 5000 before the fix)
    """
    pip = pip_size(pair)
    if pip is None:
        return None
    return float(price_move) / pip


def round_to_pips(pair: str, price: float):
    """Round a price to pip precision for a given instrument.

    Decimals are derived from the pip size, not from the name, so a
    2-digit instrument rounds to 2 and a 5-digit one to 4.

    round_to_pips('EUR/USD', 1.123456) -> 1.1235
    round_to_pips('USD/JPY', 123.456)  -> 123.46
    round_to_pips('XAUUSD', 4380.9876) -> 4380.99
    """
    pip = pip_size(pair)
    if pip is None:
        return None
    decimals = 4 if pip == 0.0001 else 2
    return round(float(price), decimals)


def pip_precision(pair: str) -> dict:
    """Pip details for an instrument: pip size, decimal places, the resolved
    broker symbol, and where the figure came from.

    `source` is 'broker' when the digit count was read from MT5, 'table'
    when it came from KNOWN_PIPS, and 'unknown' when it could not be
    established — in which case pip and decimals are None and the caller
    must refuse rather than assume.
    """
    symbol = resolve(pair)
    pip = pip_size(pair)

    if pip is None:
        return {
            "pair": str(pair).upper().replace(" ", ""),
            "symbol": symbol,
            "pip": None,
            "decimals": None,
            "source": "unknown",
            "note": (f"Cannot establish pip size for {symbol}. Add it to "
                     f"KNOWN_PIPS in tools/market.py. Do not assume 0.0001 "
                     f"— that is a 100x error on metals and indices."),
        }

    info = _ask_mt5(symbol)
    source = ("broker" if info is not None and getattr(info, "digits", None)
              else "table")

    return {
        "pair": str(pair).upper().replace(" ", ""),
        "symbol": symbol,
        "pip": pip,
        "decimals": 4 if pip == 0.0001 else 2,
        "source": source,
    }