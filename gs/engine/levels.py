"""Kluczowe poziomy z wyższej ramy: swingi, grupowanie w strefy, poziomy dnia poprzedniego.

Wszystko jest mechaniczne: te same dane zawsze dają te same poziomy.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from .config import Config


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR jako prosta średnia z True Range (bez wygładzania Wildera, łatwiej sprawdzić ręcznie)."""
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


def find_swings(df: pd.DataFrame, n: int = 2) -> list[dict]:
    """Szuka swingów: maksimum wyższe niż maksima n świec po obu stronach (dołek analogicznie).

    Ostatnie n świec nie może być swingiem, bo nie znamy jeszcze świec „po prawej”.
    """
    if n < 1 or len(df) < 2 * n + 1:
        return []
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    # Okna po 2n+1 świec: środek porównujemy z n sąsiadami z każdej strony naraz (numpy, bez pętli).
    win_h = sliding_window_view(highs, 2 * n + 1)
    win_l = sliding_window_view(lows, 2 * n + 1)
    others_h = np.delete(win_h, n, axis=1)
    others_l = np.delete(win_l, n, axis=1)
    is_high = (win_h[:, n:n + 1] > others_h).all(axis=1)
    is_low = (win_l[:, n:n + 1] < others_l).all(axis=1)
    times = df.index
    swings = []
    for k in np.flatnonzero(is_high | is_low):
        i = k + n
        if is_high[k]:
            swings.append({"time": times[i], "price": float(highs[i]), "kind": "high"})
        if is_low[k]:
            swings.append({"time": times[i], "price": float(lows[i]), "kind": "low"})
    return swings


def cluster_swings(swings: list[dict], tol: float, min_width: float) -> list[dict]:
    """Grupuje swingi (high i low razem), które różnią się o mniej niż tol.

    Swing trafia do grupy, jeśli jest bliżej niż tol od NAJNIŻSZEJ ceny grupy.
    Dzięki temu strefa nie „rozjeżdża się” łańcuchowo w nieskończoność.
    """
    zones: list[dict] = []
    group: list[dict] = []
    for s in sorted(swings, key=lambda s: s["price"]):
        if group and s["price"] - group[0]["price"] >= tol:
            zones.append(_make_zone(group, min_width))
            group = []
        group.append(s)
    if group:
        zones.append(_make_zone(group, min_width))
    return zones


def _make_zone(group: list[dict], min_width: float) -> dict:
    low = min(s["price"] for s in group)
    high = max(s["price"] for s in group)
    if high - low < min_width:
        mid = (low + high) / 2
        low, high = mid - min_width / 2, mid + min_width / 2
    return {"low": low, "high": high, "touches": len(group),
            "last_touch": max(s["time"] for s in group)}


def _point_zone(price: float, width: float, frame: str, time) -> dict:
    return {"low": price - width / 2, "high": price + width / 2, "touches": 1,
            "last_touch": time, "frame": frame}


def key_levels(daily: pd.DataFrame, hourly: pd.DataFrame, cfg: Config,
               asof: pd.Timestamp | None = None) -> list[dict]:
    """Zwraca kluczowe poziomy na dzień `asof`, używając TYLKO danych sprzed tego dnia.

    asof = None oznacza „po ostatniej świecy dziennej” (poziomy na dziś/jutro).
    """
    if asof is not None:
        day_start = pd.Timestamp(asof).normalize()
        daily = daily[daily.index < day_start]
        if hourly is not None and not hourly.empty:
            hourly = hourly[hourly.index < day_start.tz_convert(hourly.index.tz)]
    if len(daily) < 2:
        return []

    daily_atr = float(atr(daily, cfg.ATR_PERIOD).iloc[-1])
    tol = cfg.TOL_ATR * daily_atr
    min_width = cfg.MIN_ZONE_ATR * daily_atr

    levels: list[dict] = []
    for frame, df in (("D", daily), ("H1", hourly)):
        if df is None or df.empty:
            continue
        for zone in cluster_swings(find_swings(df, cfg.SWING_N), tol, min_width):
            if zone["touches"] >= cfg.MIN_TOUCHES:
                levels.append({**zone, "frame": frame})

    # Poziomy dnia poprzedniego są zawsze kluczowe.
    prev = daily.iloc[-1]
    prev_time = daily.index[-1]
    levels.append(_point_zone(float(prev["close"]), min_width, "PDC", prev_time))
    levels.append(_point_zone(float(prev["high"]), min_width, "PDH", prev_time))
    levels.append(_point_zone(float(prev["low"]), min_width, "PDL", prev_time))

    levels = merge_levels(levels, cfg.MERGE_TOL * daily_atr)
    levels.sort(key=lambda lv: lv["high"], reverse=True)
    for i, lv in enumerate(levels, start=1):
        lv["id"] = f"L{i}"
    return levels


# Kolejność źródeł w podpisie strefy: najpierw najmocniejsze.
SOURCE_ORDER = ["D", "H1", "PDH", "PDC", "PDL"]


def merge_levels(levels: list[dict], tol: float) -> list[dict]:
    """Łączy poziomy leżące bliżej siebie niż `tol` w jedną strefę (od najniższej do najwyższej ceny).

    Odległość to przerwa między krawędziami (nachodzące na siebie poziomy mają przerwę ujemną),
    liczona od PIERWSZEGO poziomu strefy, a nie od ostatnio dołączonego. Bez tego gęste poziomy H1
    sklejałyby się łańcuchowo w jedną strefę na setki punktów.
    tol <= 0 wyłącza łączenie. Każda strefa dostaje:
      sources – źródła (np. ["D", "H1"]), frame – podpis („D+H1”),
      touches – suma dotknięć, daily – czy zawiera poziom z wykresu dziennego (mocniejsza).
    """
    for lv in levels:
        lv.setdefault("sources", [lv["frame"]])
    if tol > 0:
        merged: list[dict] = []
        anchor_high = None   # górna krawędź pierwszego poziomu bieżącej strefy
        for lv in sorted(levels, key=lambda l: l["low"]):
            last = merged[-1] if merged else None
            # Łączymy, gdy poziom jest blisko pierwszego poziomu strefy albo cały w niej leży.
            if last is not None and (lv["low"] - anchor_high < tol or lv["high"] <= last["high"]):
                last["high"] = max(last["high"], lv["high"])
                last["touches"] += lv["touches"]
                last["last_touch"] = max(last["last_touch"], lv["last_touch"])
                last["sources"] = last["sources"] + [s for s in lv["sources"] if s not in last["sources"]]
            else:
                zone = dict(lv)
                # Nowa strefa zaczynająca się wewnątrz poprzedniej: przycinamy ją do krawędzi
                # poprzedniej (sklejanie takich stref rozrastało je łańcuchowo do ponad 1,5 ATR).
                if last is not None and zone["low"] < last["high"]:
                    zone["low"] = last["high"]
                merged.append(zone)
                anchor_high = zone["high"]
        levels = merged
    for lv in levels:
        lv["sources"] = sorted(lv["sources"], key=SOURCE_ORDER.index)
        lv["frame"] = "+".join(lv["sources"])
        lv["daily"] = "D" in lv["sources"]
    return levels
