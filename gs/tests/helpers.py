"""Pomocnicze funkcje do budowania syntetycznych świec w testach."""

import pandas as pd

TZ = "Europe/Berlin"


def bars(rows, start="2026-10-01 09:00", freq="5min", tz=TZ):
    """rows: lista krotek (open, high, low, close). Zwraca DataFrame jak z data.py."""
    index = pd.date_range(start=start, periods=len(rows), freq=freq, tz=tz)
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=index, dtype=float)
    df["volume"] = 100.0
    return df


def daily(rows, start="2026-01-01"):
    """Świece dzienne (dni kalendarzowe, wystarczą do testów)."""
    return bars(rows, start=start, freq="1D")


def flat_daily(n, price=100.0, rng=2.0, start="2026-01-01"):
    """n spokojnych świec dziennych wokół jednej ceny (ATR = rng)."""
    return daily([(price, price + rng / 2, price - rng / 2, price)] * n, start=start)
