"""Testy wyników strategii w okresach (1M/3M/YTD/rok) i archiwum świec."""

from datetime import date

import pandas as pd
import pytest

import engine.data as data
from engine.config import Config
from engine.instruments import get_instrument
from engine.performance import performance, period_start, trading_days

DAX = get_instrument("dax")
BTC = get_instrument("btc")
TZ = "Europe/Berlin"
NOW = pd.Timestamp("2026-11-06 18:00", tz=TZ)


def test_poczatki_okresow():
    today = date(2026, 11, 6)
    assert period_start("1m", today) == date(2026, 10, 6)
    assert period_start("3m", today) == date(2026, 8, 6)
    assert period_start("ytd", today) == date(2026, 1, 1)
    assert period_start("1y", today) == date(2025, 11, 6)


def test_dni_handlowe_zalezne_od_rynku():
    week = (date(2026, 11, 2), date(2026, 11, 8))   # poniedziałek – niedziela
    assert trading_days(*week, DAX) == 5
    assert trading_days(*week, BTC) == 7


def ltf_on(days):
    """Po jednej świecy 5m o 9:00 w każdym z podanych dni."""
    idx = pd.DatetimeIndex([pd.Timestamp(f"{d} 09:00", tz=TZ) for d in days])
    return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 0.0}, index=idx)


def trade(day, r, hhmm="10:00"):
    ts = pd.Timestamp(f"{day} {hhmm}", tz=TZ)
    return {"entry_ts": ts, "exit_time": int((ts.tz_localize(None) - pd.Timestamp("1970-01-01")).total_seconds()),
            "result_r": r, "setup": "A", "side": "long"}


def test_okresy_filtruja_transakcje_po_dacie_wejscia():
    days = pd.bdate_range("2026-08-17", "2026-11-06").date        # ok. 60 dni danych jak z Yahoo
    trades = [trade("2026-08-20", 2.0), trade("2026-09-15", -1.0), trade("2026-10-20", 1.5), trade("2026-11-05", -1.0)]
    p = {x["key"]: x for x in performance(trades, ltf_on(days), DAX, TZ, NOW, Config())["periods"]}
    assert p["1m"]["stats"]["trades"] == 2 and p["1m"]["stats"]["sum_r"] == 0.5
    assert p["3m"]["stats"]["trades"] == 4 and p["3m"]["stats"]["sum_r"] == 1.5
    assert p["1y"]["stats"]["trades"] == 4
    # 1% z 10 000 zł = 100 zł ryzyka na transakcję
    assert p["3m"]["sum_pln"] == 150.0


def test_pokrycie_danymi_jest_uczciwe():
    days = pd.bdate_range("2026-08-17", "2026-11-06").date
    p = {x["key"]: x for x in performance([], ltf_on(days), DAX, TZ, NOW, Config())["periods"]}
    assert p["1m"]["complete"] is True
    assert p["3m"]["complete"] is False                            # brakuje 6–14 sierpnia
    assert p["ytd"]["complete"] is False and p["1y"]["complete"] is False
    assert p["1y"]["days_with_data"] == len(days) < p["1y"]["days_expected"]
    assert p["ytd"]["data_from"] == "2026-08-17"


def test_krzywa_zaczyna_sie_od_zera_i_czas_rosnie():
    days = pd.bdate_range("2026-10-01", "2026-11-06").date
    trades = [trade("2026-10-20", 1.0), trade("2026-10-20", -1.0), trade("2026-10-21", 2.0)]
    trades[1]["exit_time"] = trades[0]["exit_time"]                 # dwa wyjścia w tej samej świecy
    eq = performance(trades, ltf_on(days), DAX, TZ, NOW, Config())["periods"][0]["equity"]
    assert eq[0]["value"] == 0.0
    assert [e["value"] for e in eq] == [0.0, 1.0, 0.0, 2.0]
    times = [e["time"] for e in eq]
    assert times == sorted(times) and len(set(times)) == len(times)


# ---------------------------------------------------------------- archiwum

def candles(start, n):
    idx = pd.date_range(start, periods=n, freq="5min", tz="UTC")
    return pd.DataFrame({"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0}, index=idx)


@pytest.fixture
def archive_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "ARCHIVE_DIR", tmp_path)
    return tmp_path


def test_archiwum_dopisuje_nowe_swieczki(archive_dir):
    first, _ = data.update_archive("Yahoo", "^GDAXI", "5m", candles("2026-08-17 07:00", 10))
    later = candles("2026-08-17 07:30", 10)                          # 4 świece się pokrywają
    later.loc[later.index[0], "close"] = 9.9                          # poprawiona (wcześniej niedokończona) świeca
    merged, note = data.update_archive("Yahoo", "^GDAXI", "5m", later)
    assert note is None
    assert len(merged) == 16                                          # 10 + 10 − 4 wspólne
    assert merged.loc[later.index[0], "close"] == 9.9                 # świeże dane wygrywają
    assert merged.index.is_monotonic_increasing


def test_archiwum_pamieta_historie_gdy_zrodlo_daje_tylko_ostatnie_dni(archive_dir):
    data.update_archive("Yahoo", "X", "5m", candles("2026-06-01 07:00", 5))
    merged, _ = data.update_archive("Yahoo", "X", "5m", candles("2026-08-17 07:00", 5))
    assert merged.index[0] == pd.Timestamp("2026-06-01 07:00", tz="UTC")


def test_uszkodzone_archiwum_nie_jest_nadpisywane(archive_dir):
    path = data._archive_path("Yahoo", "X", "5m")
    path.write_text("to nie jest csv,,,\n\x00", encoding="utf-8")
    merged, note = data.update_archive("Yahoo", "X", "5m", candles("2026-08-17 07:00", 3))
    assert "uszkodzone" in note and len(merged) == 3
    assert path.read_text(encoding="utf-8").startswith("to nie jest csv")
