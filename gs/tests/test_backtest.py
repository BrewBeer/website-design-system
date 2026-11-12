"""Testy symulacji: kolejność SL/TP, zamknięcie na koniec sesji, statystyki, brak look-ahead."""

import numpy as np
import pandas as pd
import pytest

import engine.backtest as bt
from engine.config import Config
from engine.instruments import get_instrument
from engine.levels import key_levels
from tests.helpers import bars, flat_daily

DAX = get_instrument("dax")
FUTURE = pd.Timestamp("2030-01-01", tz="Europe/Berlin")   # „teraz” daleko w przyszłości: brak dnia na żywo
LEVELS = [
    {"id": "L1", "low": 110.0, "high": 111.0, "touches": 2, "frame": "D"},
    {"id": "L2", "low": 100.0, "high": 102.0, "touches": 3, "frame": "D"},
    {"id": "L3", "low": 90.0, "high": 91.0, "touches": 2, "frame": "H1"},
]
CFG = Config(USE_TREND_FILTER=False, WINDOW_MODE="open")   # okienko 9:00–10:30, żeby testować „poza okienkiem”


@pytest.fixture
def fixed_levels(monkeypatch):
    monkeypatch.setattr(bt, "key_levels", lambda *a, **k: [dict(l) for l in LEVELS])


def day_bars(third_bar, rest=6):
    """Dzień 5m: świeca startowa, wybicie A long nad 102, wejście, potem `third_bar` i spokój."""
    rows = [
        (101.0, 101.8, 100.3, 101.0),   # 09:00 zamknięcie w strefie
        (101.5, 102.8, 101.4, 102.6),   # 09:05 sygnał A long: wejście 102,9, SL 101,3
        (102.6, 103.5, 102.4, 103.2),   # 09:10 wejście (maksimum ≥ 102,9)
        third_bar,
    ] + [(103.0, 103.6, 102.6, 103.2)] * rest
    return bars(rows, start="2026-03-02 09:00")


def run(ltf):
    return bt.simulate(flat_daily(60), pd.DataFrame(), ltf, DAX, CFG, now=FUTURE)


def test_sygnal_a_staje_sie_transakcja(fixed_levels):
    res = run(day_bars((103.2, 103.8, 102.8, 103.5)))
    s1 = res["signals"][0]
    assert (s1["setup"], s1["side"], s1["status"]) == ("A", "long", "filled")
    assert s1["entry"] == pytest.approx(102.9) and s1["sl"] == pytest.approx(101.3)
    assert s1["weak"] is True        # płaski rynek: cena między średnimi


def test_sl_i_tp_w_tej_samej_swiecy_liczymy_sl(fixed_levels):
    res = run(day_bars((103.0, 112.0, 101.0, 105.0)))   # świeca sięga i TP, i SL
    t = res["trades"][0]
    assert t["exit_reason"] == "sl" and t["result_r"] == -1.0


def test_tp(fixed_levels):
    res = run(day_bars((103.0, 112.0, 102.5, 110.0)))
    t = res["trades"][0]
    tp = res["signals"][0]["tp"]
    assert t["exit_reason"] == "tp"
    assert tp == pytest.approx(round(tp, 1))                    # TP leży na siatce ticka 0,1
    assert tp == pytest.approx(109.85, abs=0.051)               # 110 − 10% ATR (≈1,5)
    assert t["result_r"] == pytest.approx((tp - 102.9) / 1.6, abs=0.01)


def test_pozycja_zamykana_na_koniec_sesji(fixed_levels):
    res = run(day_bars((103.2, 103.8, 102.8, 103.5)))
    t = res["trades"][0]
    assert t["exit_reason"] == "eod" and t["exit_price"] == 103.2
    assert t["result_r"] == pytest.approx((103.2 - 102.9) / 1.6, abs=0.01)


def test_zlecenie_wygasa_po_entry_valid_swiecach(fixed_levels):
    rows = [
        (101.0, 101.8, 100.3, 101.0),
        (101.5, 102.8, 101.4, 102.6),   # sygnał, wejście 102,9
    ] + [(102.5, 102.85, 102.3, 102.6)] * 5   # cena nie dochodzi do 102,9
    res = bt.simulate(flat_daily(60), pd.DataFrame(), bars(rows, start="2026-03-02 09:00"),
                      DAX, Config(USE_TREND_FILTER=False, ENTRY_VALID=3), now=FUTURE)
    assert res["signals"][0]["status"] == "cancelled"
    assert res["trades"] == []


def test_sygnal_poza_okienkiem_ignorowany(fixed_levels):
    ltf = day_bars((103.2, 103.8, 102.8, 103.5))
    ltf.index = ltf.index + pd.Timedelta(hours=3)   # 12:00, poza okienkiem 9:00–10:30
    assert run(ltf)["signals"] == []


def test_dzien_na_zywo_zostawia_pozycje_otwarta(fixed_levels):
    ltf = day_bars((103.2, 103.8, 102.8, 103.5))
    now = ltf.index[-1] + pd.Timedelta(minutes=10)
    res = bt.simulate(flat_daily(60), pd.DataFrame(), ltf, DAX, CFG, now=now)
    assert res["trades"] == [] and res["signals"][0]["status"] == "active"


# ---------------------------------------------------------------- statystyki

def test_statystyki():
    trades = [{"result_r": r, "exit_time": i, "setup": s, "side": d}
              for i, (r, s, d) in enumerate([(2, "A", "long"), (-1, "B", "long"), (-1, "A", "short"),
                                             (-1, "C", "short"), (3, "A", "long")])]
    st = bt.compute_stats(trades)
    assert st["trades"] == 5
    assert st["winrate"] == 40.0
    assert st["sum_r"] == 2.0 and st["avg_r"] == 0.4
    assert st["max_dd_r"] == 3.0          # szczyt 2, dołek −1
    assert st["max_loss_streak"] == 3
    assert st["by_setup"]["A"] == {"trades": 3, "winrate": 66.7, "sum_r": 4.0}
    assert st["by_side"]["short"]["sum_r"] == -2.0
    assert [p["value"] for p in st["equity_r"]] == [2, 1, 0, -1, 2]


def test_statystyki_bez_transakcji():
    st = bt.compute_stats([])
    assert st["trades"] == 0 and st["sum_r"] == 0 and st["equity_r"] == []


# ---------------------------------------------------------------- brak look-ahead

def random_market(seed=7):
    rng = np.random.default_rng(seed)
    d_close = 100 + np.cumsum(rng.normal(0, 1.5, 150))
    d_rows = [(c, c + abs(rng.normal(1, .3)), c - abs(rng.normal(1, .3)), c + rng.normal(0, .5)) for c in d_close]
    daily = bars(d_rows, start="2026-01-01", freq="1D")
    days = daily.index[-3:]
    ltf_parts = []
    price = float(daily["close"].iloc[-4])
    for d in days:
        idx = pd.date_range(d + pd.Timedelta(hours=9), d + pd.Timedelta(hours=17, minutes=25), freq="5min")
        steps = np.cumsum(rng.normal(0, .4, len(idx))) + price
        rows = [(p, p + abs(rng.normal(.3, .1)), p - abs(rng.normal(.3, .1)), p + rng.normal(0, .2)) for p in steps]
        ltf_parts.append(pd.DataFrame(rows, index=idx, columns=["open", "high", "low", "close"]))
        price = steps[-1]
    ltf = pd.concat(ltf_parts)
    ltf["volume"] = 100.0
    return daily, ltf


def _day_signals(res, day):
    keep = ("ts", "setup", "side", "entry", "sl", "tp", "status", "reject_reason")
    return [{k: s[k] for k in keep} for s in res["signals"] if s["ts"].date() == day]


def test_poziomy_dnia_d_liczone_z_danych_do_d_minus_1(monkeypatch):
    daily, ltf = random_market()
    cfg = Config(MIN_TOUCHES=1, MIN_RR=0.1, MAX_RISK_ATR=99)   # luźno, żeby było dużo sygnałów
    calls = []
    real = bt.key_levels

    def spy(d, h, c, asof=None):
        calls.append(asof)
        out = real(d, h, c, asof=asof)
        # To samo, co z danymi uciętymi ręcznie do D−1.
        assert out == real(d[d.index < asof], h, c)
        return out

    monkeypatch.setattr(bt, "key_levels", spy)
    bt.simulate(daily, pd.DataFrame(), ltf, DAX, cfg, now=FUTURE)
    assert calls and all(a is not None for a in calls)


def test_przyszle_dane_nie_zmieniaja_sygnalow_dnia_d():
    daily, ltf = random_market()
    cfg = Config(MIN_TOUCHES=1, MIN_RR=0.1, MAX_RISK_ATR=99, USE_TREND_FILTER=False)
    day_d = ltf.index[0].date()
    full = bt.simulate(daily, pd.DataFrame(), ltf, DAX, cfg, now=FUTURE)
    assert _day_signals(full, day_d), "test potrzebuje choć jednego sygnału w dniu D"

    # Zmieniamy wszystko od dnia D włącznie na wykresie dziennym (świeca D i późniejsze).
    day_start = pd.Timestamp(day_d, tz="Europe/Berlin")
    future_daily = daily.copy()
    future_daily.loc[future_daily.index >= day_start, ["high", "close"]] = [9999.0, 9000.0]
    # I odcinamy kolejne dni LTF.
    ltf_until_d = ltf[ltf.index.date <= day_d]
    changed = bt.simulate(future_daily, pd.DataFrame(), ltf_until_d, DAX, cfg, now=FUTURE)
    assert _day_signals(changed, day_d) == _day_signals(full, day_d)
