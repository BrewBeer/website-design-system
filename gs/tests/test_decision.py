"""Testy jednej decyzji na górze ekranu: KUP / SPRZEDAJ / PLAN / CZEKAJ / POZA OKIENKIEM."""

import pandas as pd
import pytest

from engine.config import Config
from engine.decision import data_age, decide, wait_sentence
from engine.instruments import get_instrument

DAX = get_instrument("dax")
PL = "Europe/Warsaw"
NOW = pd.Timestamp("2026-11-03 09:50", tz=PL)
IN_WINDOW = {"in_window": True, "text": "Okienko handlu trwa: 9:00–10:30 czasu polskiego."}
OUT_WINDOW = {"in_window": False, "market_open": True,
              "text": "Giełda otwarta do 17:30 czasu polskiego, ale strategia szuka wejść tylko w okienku. "
                      "Następne okno: jutro 9:00–10:30 czasu polskiego (9:00–10:30 Frankfurt)."}
CLOSED = {"in_window": False, "market_open": False,
          "text": "Giełda zamknięta – otwarcie jutro o 9:00 czasu polskiego."}
LEVELS = [
    {"id": "L1", "low": 18500.0, "high": 18520.0, "touches": 3},
    {"id": "L2", "low": 18400.0, "high": 18420.0, "touches": 4},
    {"id": "L3", "low": 18300.0, "high": 18310.0, "touches": 2},
]
SIGNAL = {"id": "S7", "setup": "A", "side": "long", "status": "pending", "entry": 18425.0, "sl": 18398.0,
          "tp": 18470.0, "rr": 1.67, "size": 3.7, "risk_pln": 100.0, "reward_pln": 166.67,
          "level_low": 18400.0, "level_high": 18420.0}


def fresh():
    return data_age(NOW - pd.Timedelta(minutes=9), "5m", NOW)      # świeca 9:41–9:46, 4 min temu


def stale():
    return data_age(NOW - pd.Timedelta(minutes=20), "5m", NOW)     # świeca kończy się 9:35, 15 min temu


def run(**kw):
    args = dict(active_signal=None, window=IN_WINDOW, freshness=fresh(), levels=LEVELS, last_price=18450.0,
                trend="long", intraday=True, inst=DAX, cfg=Config()) | kw
    return decide(**args)


# ---------------------------------------------------------------- wiek danych

def test_wiek_danych_tekst_i_opoznienie():
    f = data_age(pd.Timestamp("2026-11-03 10:15", tz=PL), "5m", pd.Timestamp("2026-11-03 10:35", tz=PL))
    assert f["text"] == "Dane z 10:20 (opóźnienie ok. 15 min)"
    assert f["minutes"] == 15 and f["stale"] is True


def test_dane_swieze_gdy_opoznienie_do_jednej_swiecy():
    assert fresh()["stale"] is False
    # Dokładnie jedna świeca 15m opóźnienia to jeszcze nie „opóźnione”.
    f = data_age(pd.Timestamp("2026-11-03 10:00", tz=PL), "15m", pd.Timestamp("2026-11-03 10:30", tz=PL))
    assert f["minutes"] == 15 and f["stale"] is False


# ---------------------------------------------------------------- stany

def test_kup_przy_aktywnym_sygnale_long_na_swiezych_danych():
    d = run(active_signal=SIGNAL)
    assert (d["state"], d["headline"], d["side"]) == ("buy", "KUP", "long")
    o = d["order"]
    assert o["type"] == "Stop kupna" and o["activation"] == 18425.0
    assert (o["sl"], o["tp"], o["size"], o["risk_pln"], o["reward_pln"]) == (18398.0, 18470.0, 3.7, 100.0, 166.67)
    assert o["validity"] == "Do końca dnia"
    assert "bracket" in o["broker_hint"] and "bossie" in o["broker_hint"]


def test_sprzedaj_przy_aktywnym_sygnale_short():
    short = SIGNAL | {"side": "short", "entry": 18395.0, "sl": 18422.0, "tp": 18320.0}
    d = run(active_signal=short)
    assert (d["state"], d["headline"]) == ("sell", "SPRZEDAJ")
    assert d["order"]["type"] == "Stop sprzedaży"


@pytest.mark.parametrize("signal", [SIGNAL, SIGNAL | {"side": "short"}])
def test_nigdy_kup_ani_sprzedaj_na_danych_opoznionych(signal):
    d = run(active_signal=signal, freshness=stale())
    assert d["state"] == "plan" and d["headline"] == "PLAN"
    assert d["headline"] not in ("KUP", "SPRZEDAJ")


def test_plan_zawiera_warunek_i_gotowe_zlecenie():
    d = run(active_signal=SIGNAL, freshness=stale())
    assert d["sentence"] == ("Jeśli na Twoim wykresie świeca 5m zamknie się nad 18 420: "
                             "zlecenie stop kupna 18 425, SL 18 398, TP 18 470.")
    assert d["freshness"]["text"].startswith("Dane z 9:35")


def test_czekaj_jedno_zdanie_z_najblizszym_warunkiem():
    d = run()
    assert (d["state"], d["headline"]) == ("wait", "CZEKAJ")
    assert d["sentence"] == "Kup, jeśli świeca 5m zamknie się nad 18 520."
    assert d["order"] is None


def test_czekaj_w_trendzie_spadkowym_proponuje_sprzedaz():
    assert run(trend="short")["sentence"] == "Sprzedaj, jeśli świeca 5m zamknie się pod 18 400."


def test_przy_mieszanym_trendzie_wybiera_blizszy_warunek():
    # Cena 18 430: do 18 400 jest 30 pkt, do 18 520 – 90 pkt.
    assert wait_sentence(LEVELS, 18430.0, "mixed", Config(), 1) == "Sprzedaj, jeśli świeca 5m zamknie się pod 18 400."


def test_poza_okienkiem_gdy_gielda_otwarta():
    d = run(window=OUT_WINDOW)
    assert (d["state"], d["headline"]) == ("out", "POZA OKIENKIEM")
    assert d["sentence"] == OUT_WINDOW["text"]


def test_gielda_zamknieta_to_osobny_stan():
    d = run(window=CLOSED)
    assert (d["state"], d["headline"]) == ("closed", "GIEŁDA ZAMKNIĘTA")
    assert d["sentence"] == "Giełda zamknięta – otwarcie jutro o 9:00 czasu polskiego."


def test_aktywna_pozycja_wazniejsza_niz_koniec_okienka():
    # Zlecenie złożone w okienku nadal obowiązuje po 10:30.
    assert run(active_signal=SIGNAL | {"status": "active"}, window=OUT_WINDOW)["state"] == "buy"


def test_brak_danych_intraday():
    d = run(intraday=False, freshness=data_age(None, "5m", NOW))
    assert d["state"] == "wait" and "Brak danych 5-minutowych" in d["sentence"]
