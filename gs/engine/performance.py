"""Wyniki strategii w okresach: 1 miesiąc, 3 miesiące, od początku roku, rok.

Liczymy JEDNĄ symulację na całej historii z archiwum, a okres to po prostu filtr
transakcji po dacie wejścia. To daje ten sam wynik co osobna symulacja okresu,
bo każdy dzień jest niezależny (pozycje zamykamy na koniec sesji, limity są dzienne).

Uczciwość danych: dla każdego okresu podajemy, ile dni handlowych faktycznie ma
dane 5m. Wynik dotyczy tylko tych dni – brakujących niczym nie zastępujemy.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from .backtest import compute_stats, chart_time
from .config import Config
from .instruments import Instrument

PERIODS = [("1m", "1 mies."), ("3m", "3 mies."), ("ytd", "Od początku roku"), ("1y", "Rok")]


def period_start(key: str, today: date) -> date:
    t = pd.Timestamp(today)
    if key == "1m":
        return (t - pd.DateOffset(months=1)).date()
    if key == "3m":
        return (t - pd.DateOffset(months=3)).date()
    if key == "ytd":
        return date(today.year, 1, 1)
    if key == "1y":
        return (t - pd.DateOffset(years=1)).date()
    raise ValueError(f"Nieznany okres: {key}")


def trading_days(start: date, end: date, inst: Instrument) -> int:
    """Ile dni handlowych (wg kalendarza tygodnia instrumentu) jest między start i end włącznie."""
    days, d = 0, start
    while d <= end:
        days += inst.trades_on(d.weekday())
        d += timedelta(days=1)
    return days


def performance(trades: list[dict], ltf_full: pd.DataFrame, inst: Instrument, tz: str,
                now: pd.Timestamp, cfg: Config) -> dict:
    """Statystyki, krzywa kapitału w R i pokrycie danymi dla każdego okresu."""
    today = now.tz_convert(tz).date()
    data_days = sorted(set(ltf_full.index.tz_convert(tz).date)) if ltf_full is not None and len(ltf_full) else []
    risk_pln = cfg.KAPITAL * cfg.RYZYKO_PROC / 100
    out = []
    for key, label in PERIODS:
        start = period_start(key, today)
        sub = [t for t in trades if t["entry_ts"].tz_convert(tz).date() >= start]
        stats = compute_stats(sub)
        expected = trading_days(start, today, inst)
        have = sum(1 for d in data_days if start <= d <= today and inst.trades_on(d.weekday()))
        first = next((d for d in data_days if d >= start), None)
        # Krzywa zaczyna się od 0 w pierwszym dniu z danymi; czasy muszą rosnąć (wymóg wykresu).
        curve, last_t = [], None
        if first is not None:
            last_t = chart_time(pd.Timestamp(first, tz=tz))
            curve.append({"time": last_t, "value": 0.0})
        for p in stats["equity_r"]:
            t = p["time"] if last_t is None or p["time"] > last_t else last_t + 1
            curve.append({"time": t, "value": p["value"]})
            last_t = t
        out.append({
            "key": key, "label": label, "start": start.isoformat(),
            "days_expected": expected, "days_with_data": have,
            "complete": have >= expected * 0.95,
            "data_from": first.isoformat() if first else None,
            "stats": {k: v for k, v in stats.items() if k != "equity_r"},
            "sum_pln": round(stats["sum_r"] * risk_pln, 2),
            "equity": curve,
        })
    return {"data_from": data_days[0].isoformat() if data_days else None,
            "risk_pln": round(risk_pln, 2), "periods": out}
