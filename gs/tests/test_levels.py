"""Testy swingów, stref i poziomów kluczowych."""

import pandas as pd
import pytest

from engine.config import Config
from engine.levels import atr, find_swings, cluster_swings, key_levels, merge_levels
from tests.helpers import daily


def test_atr_prosty():
    df = daily([(10, 12, 8, 10), (10, 14, 10, 13)])
    # TR1 = 4, TR2 = max(4, |14-10|, |10-10|) = 4
    assert list(atr(df, 14)) == [4.0, 4.0]


def test_swing_high_i_low():
    df = daily([
        (10, 11, 9, 10),
        (10, 12, 9, 11),
        (11, 15, 10, 14),   # swing high 15
        (14, 13, 8, 9),
        (9, 12, 7, 8),
        (8, 10, 5, 6),      # swing low 5
        (6, 9, 6, 8),
        (8, 10, 7, 9),
    ])
    swings = find_swings(df, n=2)
    assert {"price": 15.0, "kind": "high"}.items() <= swings[0].items()
    assert any(s["price"] == 5.0 and s["kind"] == "low" for s in swings)


def test_rowne_maksima_nie_sa_swingiem():
    # Maksimum musi być WYŻSZE od sąsiadów, równość nie wystarcza.
    df = daily([(10, 11, 9, 10), (10, 12, 9, 11), (11, 12, 10, 11), (11, 11, 9, 10), (10, 10, 8, 9)])
    assert not any(s["kind"] == "high" for s in find_swings(df, n=2))


def test_grupowanie_w_strefe():
    t = pd.Timestamp("2026-01-01", tz="UTC")
    swings = [
        {"price": 100.0, "time": t, "kind": "high"},
        {"price": 100.4, "time": t + pd.Timedelta(days=5), "kind": "low"},
        {"price": 110.0, "time": t, "kind": "high"},
    ]
    zones = cluster_swings(swings, tol=1.0, min_width=0.1)
    assert len(zones) == 2
    z = zones[0]
    assert (z["low"], z["high"], z["touches"]) == (100.0, 100.4, 2)
    assert z["last_touch"] == t + pd.Timedelta(days=5)


def test_minimalna_szerokosc_strefy():
    t = pd.Timestamp("2026-01-01", tz="UTC")
    swings = [{"price": 50.0, "time": t, "kind": "high"}, {"price": 50.0, "time": t, "kind": "low"}]
    z = cluster_swings(swings, tol=1.0, min_width=0.4)[0]
    assert z["high"] - z["low"] == pytest.approx(0.4) and z["low"] == pytest.approx(49.8)


def _zigzag_daily():
    """Rynek odbijający się kilka razy od 100 i 110, ATR ok. 4."""
    rows = []
    pattern = [104, 107, 110, 107, 104, 101, 100, 101, 104]
    for _ in range(4):
        for p in pattern:
            rows.append((p, p + 0.2 if p != 110 else 110.0, p - 0.2 if p != 100 else 100.0, p))
    return daily(rows)


def test_poziom_kluczowy_wymaga_dwoch_dotkniec():
    cfg = Config(MIN_TOUCHES=2, MERGE_TOL=0)   # pojedyncze poziomy, bez łączenia
    lv = key_levels(_zigzag_daily(), pd.DataFrame(), cfg)
    frames = {l["frame"] for l in lv}
    assert {"PDC", "PDH", "PDL"} <= frames
    d_levels = [l for l in lv if l["frame"] == "D"]
    assert any(l["low"] <= 110 <= l["high"] and l["touches"] >= 2 for l in d_levels)
    assert any(l["low"] <= 100 <= l["high"] and l["touches"] >= 2 for l in d_levels)
    # Przy MIN_TOUCHES większym niż liczba odbić strefa D znika.
    assert not [l for l in key_levels(_zigzag_daily(), pd.DataFrame(), Config(MIN_TOUCHES=99, MERGE_TOL=0))
                if l["frame"] == "D"]


def test_poziomy_dnia_poprzedniego():
    df = daily([(10, 12, 8, 11), (11, 15, 9, 13), (13, 14, 10, 12)])
    lv = {l["frame"]: l for l in key_levels(df, pd.DataFrame(), Config())}
    mid = lambda l: (l["low"] + l["high"]) / 2
    assert mid(lv["PDC"]) == 12 and mid(lv["PDH"]) == 14 and mid(lv["PDL"]) == 10


def test_poziomy_dnia_d_tylko_z_danych_do_d_minus_1():
    df = _zigzag_daily()
    day_d = df.index[-1]
    # Na dzień D dokładamy ogromny szczyt: nie może się pojawić w poziomach dnia D.
    df.loc[day_d, ["high", "close"]] = [500.0, 480.0]
    lv = key_levels(df, pd.DataFrame(), Config(MERGE_TOL=0), asof=day_d)
    assert all(l["high"] < 400 for l in lv)
    # PDC to zamknięcie z D-1, a nie z D.
    pdc = next(l for l in lv if l["frame"] == "PDC")
    assert abs((pdc["low"] + pdc["high"]) / 2 - df["close"].iloc[-2]) < 1e-9


def test_id_posortowane_od_gory():
    lv = key_levels(_zigzag_daily(), pd.DataFrame(), Config())
    highs = [l["high"] for l in lv]
    assert highs == sorted(highs, reverse=True)
    assert [l["id"] for l in lv][:2] == ["L1", "L2"]


# ---------------------------------------------------------------- łączenie bliskich poziomów w strefy

def _lv(low, high, frame, touches=2):
    return {"low": low, "high": high, "touches": touches, "frame": frame,
            "last_touch": pd.Timestamp("2026-01-01", tz="UTC")}


def test_dwa_poziomy_blizej_niz_merge_tol_daja_jedna_strefe():
    # Przerwa 100,5 → 101,0 = 0,5 < tolerancja 1,0
    zones = merge_levels([_lv(100.0, 100.5, "D", 3), _lv(101.0, 101.2, "H1", 4)], tol=1.0)
    assert len(zones) == 1
    z = zones[0]
    assert (z["low"], z["high"], z["touches"]) == (100.0, 101.2, 7)
    assert z["frame"] == "D+H1" and z["sources"] == ["D", "H1"] and z["daily"] is True


def test_dwa_poziomy_dalej_niz_merge_tol_daja_dwie_strefy():
    # Przerwa 100,5 → 102,0 = 1,5 > tolerancja 1,0
    zones = merge_levels([_lv(100.0, 100.5, "D"), _lv(102.0, 102.2, "H1")], tol=1.0)
    assert len(zones) == 2
    assert [z["frame"] for z in zones] == ["D", "H1"]
    assert [z["daily"] for z in zones] == [True, False]


def test_kolejnosc_zrodel_w_podpisie():
    zones = merge_levels([_lv(100.0, 100.2, "PDL", 1), _lv(100.3, 100.4, "H1"), _lv(100.5, 100.6, "D")], tol=0.5)
    assert len(zones) == 1 and zones[0]["frame"] == "D+H1+PDL" and zones[0]["touches"] == 5


def test_brak_laczenia_lancuchowego():
    # Każdy kolejny poziom jest blisko poprzedniego, ale trzeci jest daleko od pierwszego:
    # strefa nie może rosnąć w nieskończoność.
    zones = merge_levels([_lv(100.0, 100.2, "H1"), _lv(100.5, 100.6, "H1"), _lv(100.9, 101.0, "H1"),
                          _lv(101.3, 101.4, "H1")], tol=0.5)
    assert len(zones) == 2
    assert all(z["high"] - z["low"] <= 0.5 + 0.2 + 0.1 for z in zones)


def test_merge_tol_zero_wylacza_laczenie():
    assert len(merge_levels([_lv(100.0, 100.5, "D"), _lv(100.4, 100.8, "H1")], tol=0)) == 2


def test_key_levels_uzywa_merge_tol_z_ustawien():
    df = _zigzag_daily()
    split = key_levels(df, pd.DataFrame(), Config(MERGE_TOL=0))
    joined = key_levels(df, pd.DataFrame(), Config(MERGE_TOL=1.0))
    assert len(joined) < len(split)
    assert sum(l["touches"] for l in joined) == sum(l["touches"] for l in split)


def test_strefy_nigdy_na_siebie_nie_nachodza():
    # Trzeci poziom jest daleko od pierwszego, ale zaczyna się wewnątrz strefy:
    # zostaje osobną strefą przyciętą do jej krawędzi (sklejanie rozrastało strefy łańcuchowo).
    zones = merge_levels([_lv(100.0, 100.2, "H1"), _lv(100.5, 101.2, "H1"), _lv(101.1, 101.6, "PDC", 1)], tol=0.5)
    assert [(z["low"], z["high"]) for z in zones] == [(100.0, 101.2), (101.2, 101.6)]


def test_poziom_lezacy_w_calosci_w_strefie_dolacza_do_niej():
    zones = merge_levels([_lv(100.0, 102.0, "D", 3), _lv(101.5, 101.7, "PDH", 1)], tol=0.3)
    assert len(zones) == 1 and zones[0]["frame"] == "D+PDH" and zones[0]["touches"] == 4
