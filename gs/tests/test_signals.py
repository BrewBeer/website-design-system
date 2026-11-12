"""Testy wzorców A/B/C (long i short) na syntetycznych świecach oraz budowy zleceń."""

import pandas as pd
import pytest

from engine.config import Config
from engine.instruments import get_instrument
from engine.signals import detect_patterns, build_order, reject_reason, trend_state
from tests.helpers import bars, daily

# Jedna strefa 100–102 z trzema dotknięciami.
ZONE = {"id": "L2", "low": 100.0, "high": 102.0, "touches": 3, "frame": "D"}
TOL = 0.5
LOOKBACK = 12
DAX = get_instrument("dax")


def patterns(rows):
    df = bars(rows)
    return {(p["setup"], p["side"]) for p in detect_patterns(df, len(df) - 1, [ZONE], TOL, LOOKBACK)}


# ---------------------------------------------------------------- A: wybicie

def test_a_long_zamkniecie_nad_strefa():
    assert ("A", "long") in patterns([(101, 101.5, 100.5, 101), (101, 103.5, 100.8, 103)])


def test_a_short_zamkniecie_pod_strefa():
    assert ("A", "short") in patterns([(101, 101.5, 100.5, 101), (101, 101.2, 98.5, 99)])


def test_knot_nad_strefa_bez_zamkniecia_to_brak_wybicia():
    found = patterns([(101, 101.5, 100.5, 101), (101, 104, 100.8, 101.5)])
    assert ("A", "long") not in found
    assert ("B", "long") not in found


def test_knot_pod_strefa_bez_zamkniecia_to_brak_wybicia_w_dol():
    assert ("A", "short") not in patterns([(101, 101.5, 100.5, 101), (101, 101.2, 98, 100.5)])


def test_a_wymaga_poprzedniego_zamkniecia_w_strefie_lub_pod():
    # Poprzednia świeca już nad strefą: to nie jest świeże wybicie.
    assert ("A", "long") not in patterns([(103, 104, 102.5, 103), (103, 104.5, 102.6, 104)])


# ---------------------------------------------------------------- B: retest

def test_b_long_retest_po_wybiciu():
    rows = [
        (101, 101.5, 100.5, 101),
        (101, 103.8, 100.9, 103.5),   # wybicie w górę
        (103.5, 104.5, 103.2, 104),
        (104, 104.6, 103.6, 104),
        (104, 104.2, 102.3, 103),     # retest: minimum ≤ 102 + 0,5, zamknięcie nad 102
    ]
    assert ("B", "long") in patterns(rows)


def test_b_short_retest_po_wybiciu_w_dol():
    rows = [
        (101, 101.5, 100.5, 101),
        (101, 101.1, 98.2, 98.5),     # wybicie w dół
        (98.5, 98.8, 97.5, 98),
        (98, 98.4, 97.4, 98),
        (98, 99.7, 97.8, 99),         # retest: maksimum ≥ 100 − 0,5, zamknięcie pod 100
    ]
    assert ("B", "short") in patterns(rows)


def test_b_bez_wczesniejszego_wybicia_to_brak_retestu():
    rows = [(104, 104.5, 103.5, 104)] * 4 + [(104, 104.2, 102.3, 103)]
    assert ("B", "long") not in patterns(rows)


def test_b_wybicie_starsze_niz_lookback_nie_liczy_sie():
    rows = [(101, 101.5, 100.5, 101), (101, 103.8, 100.9, 103.5)]
    rows += [(104, 104.6, 103.6, 104)] * (LOOKBACK + 1)
    rows += [(104, 104.2, 102.3, 103)]
    assert ("B", "long") not in patterns(rows)


# ---------------------------------------------------------------- C: grabienie płynności

def test_c_long_knot_pod_wsparciem_i_powrot():
    assert ("C", "long") in patterns([(101.5, 101.8, 101, 101.5), (101.5, 101.6, 99.5, 100.5)])


def test_c_short_knot_nad_opor_i_powrot():
    assert ("C", "short") in patterns([(100.5, 101, 100.4, 100.5), (100.5, 102.5, 100.4, 101.5)])


def test_c_long_wymaga_zamkniecia_nad_dolna_granica():
    assert ("C", "long") not in patterns([(101.5, 101.8, 101, 101.5), (101.5, 101.6, 99.0, 99.5)])


# ---------------------------------------------------------------- zlecenia

LEVELS = [
    {"id": "L1", "low": 110.0, "high": 111.0, "touches": 2, "frame": "D"},
    ZONE,
    {"id": "L3", "low": 90.0, "high": 91.0, "touches": 2, "frame": "H1"},
]


def signal_bar(o, h, l, c):
    return pd.Series({"open": o, "high": h, "low": l, "close": c})


def test_zlecenie_long():
    cfg = Config(KAPITAL=10000, RYZYKO_PROC=1)
    order = build_order(signal_bar(101, 103.5, 100.8, 103), "long", LEVELS, atr_ltf=2.0, cfg=cfg, inst=DAX)
    assert order["entry"] == pytest.approx(103.6)          # maksimum + 1 tick
    assert order["sl"] == pytest.approx(100.7)             # minimum − 1 tick
    assert order["tp"] == pytest.approx(109.8)             # 110 − 10% ATR
    assert order["rr"] == pytest.approx(6.2 / 2.9, abs=0.01)
    assert order["size"] == pytest.approx(100 / 2.9, abs=0.01)
    assert order["target_level_id"] == "L1"
    # Ryzyko 1% z 10 000 zł = 100 zł; możliwy zysk = odległość do TP × wielkość = R:R × ryzyko.
    assert order["risk_pln"] == pytest.approx(100, abs=0.01)
    assert order["reward_pln"] == pytest.approx(100 * 6.2 / 2.9, abs=0.01)


def test_zlecenie_short():
    order = build_order(signal_bar(101, 101.2, 98.5, 99), "short", LEVELS, atr_ltf=2.0, cfg=Config(), inst=DAX)
    assert order["entry"] == pytest.approx(98.4)
    assert order["sl"] == pytest.approx(101.3)
    assert order["tp"] == pytest.approx(91.2)              # 91 + 10% ATR
    assert order["target_level_id"] == "L3"


def test_brak_poziomu_na_tp():
    order = build_order(signal_bar(111, 113, 110.5, 112.5), "long", LEVELS, 2.0, Config(), DAX)
    assert order["tp"] is None
    assert "take profit" in reject_reason(order, "long", "long", 2.0, 0, Config())


@pytest.mark.parametrize("kwargs, fragment", [
    ({"side": "short", "trend": "long"}, "filtrem trendu"),
    ({"trades_today": 2}, "Limit 2"),
    ({"atr_ltf": 1.0}, "Ryzyko"),          # ryzyko 2,9 > 1,5 × 1,0
])
def test_powody_odrzucenia(kwargs, fragment):
    cfg = Config()
    order = build_order(signal_bar(101, 103.5, 100.8, 103), "long", LEVELS, 2.0, cfg, DAX)
    args = {"side": "long", "trend": "long", "atr_ltf": 2.0, "trades_today": 0} | kwargs
    assert fragment in reject_reason(order, args["side"], args["trend"], args["atr_ltf"],
                                     args["trades_today"], cfg)


def test_odrzucenie_przez_niskie_rr():
    levels = [{"id": "L1", "low": 105.0, "high": 106.0, "touches": 2, "frame": "D"}, ZONE]
    order = build_order(signal_bar(101, 103.5, 100.8, 103), "long", levels, 2.0, Config(), DAX)
    assert "R:R" in reject_reason(order, "long", "long", 2.0, 0, Config())


def test_sygnal_poprawny_nie_ma_powodu_odrzucenia():
    order = build_order(signal_bar(101, 103.5, 100.8, 103), "long", LEVELS, 2.0, Config(), DAX)
    assert reject_reason(order, "long", "long", 2.0, 0, Config()) is None
    # Przy wyłączonym filtrze kierunek sprzeczny z trendem przechodzi.
    assert reject_reason(order, "long", "short", 2.0, 0, Config(USE_TREND_FILTER=False)) is None


# ---------------------------------------------------------------- trend
# (okienko czasowe: tests/test_window.py)

def test_trend_long_short_mixed():
    up = daily([(p, p + 1, p - 1, p) for p in range(100, 160)])
    down = daily([(p, p + 1, p - 1, p) for p in range(160, 100, -1)])
    assert trend_state(up, Config())["state"] == "long"
    assert trend_state(down, Config())["state"] == "short"
    mixed = daily([(p, p + 1, p - 1, p) for p in range(100, 160)] +
                  [(p, p + 1, p - 1, p) for p in range(160, 152, -1)])   # krótki spadek: między średnimi
    assert trend_state(mixed, Config())["state"] == "mixed"

