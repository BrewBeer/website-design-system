"""Testy okienka czasowego i stanu giełdy.

Trzy tryby (WINDOW_MODE): cała sesja (domyślnie), pierwsze N minut po otwarciu, własne godziny.
"""

import pandas as pd

from api.main import window_info
from engine.config import Config
from engine.instruments import get_instrument
from engine.signals import in_window, market_status, window_bounds, window_label

DAX = get_instrument("dax")
SP = get_instrument("sp500")
EURUSD = get_instrument("eurusd")
BTC = get_instrument("btc")
PL = "Europe/Warsaw"
OPEN90 = Config(WINDOW_MODE="open", TRADING_WINDOW=90)


def pl(s):
    return pd.Timestamp(s, tz=PL)


# ---------------------------------------------------------------- cała sesja (domyślnie)

def test_domyslnie_cala_sesja():
    assert Config().WINDOW_MODE == "session"
    start, end = window_bounds(pd.Timestamp("2026-11-03", tz=SP.tz), SP, Config())
    assert start == pl("2026-11-03 15:30") and end == pl("2026-11-03 22:00")


def test_cala_sesja_sygnaly_przez_cala_sesje_nyse():
    # Piątek 17:05 w Polsce = 11:05 w Nowym Jorku: giełda otwarta, więc szukamy wejść.
    assert in_window(pl("2026-11-06 17:05"), SP, Config())
    assert in_window(pl("2026-11-06 21:55"), SP, Config())
    assert not in_window(pl("2026-11-06 22:00"), SP, Config())     # zamknięcie 16:00 NY
    assert not in_window(pl("2026-11-06 15:25"), SP, Config())     # przed otwarciem


def test_cala_sesja_na_rynku_calodobowym_to_cala_doba():
    assert in_window(pl("2026-11-03 03:00"), EURUSD, Config())
    assert in_window(pl("2026-11-03 23:55"), EURUSD, Config())
    assert not in_window(pl("2026-11-07 12:00"), EURUSD, Config())  # sobota: waluty nie handlują
    assert in_window(pl("2026-11-07 12:00"), BTC, Config())         # bitcoin: 7 dni


# ---------------------------------------------------------------- pierwsze N minut (dawna zasada)

def test_dax_pierwsze_90_minut():
    start, end = window_bounds(pd.Timestamp("2026-11-03", tz=DAX.tz), DAX, OPEN90)
    assert start == pl("2026-11-03 09:00") and end == pl("2026-11-03 10:30")
    assert window_label(start, end, DAX) == "9:00–10:30 czasu polskiego (9:00–10:30 Frankfurt)"
    assert in_window(pl("2026-11-03 10:25"), DAX, OPEN90)
    assert not in_window(pl("2026-11-03 10:30"), DAX, OPEN90)
    assert not in_window(pl("2026-11-07 09:30"), DAX, OPEN90)       # sobota


def test_dlugosc_okienka_z_parametru():
    start, end = window_bounds(pd.Timestamp("2026-11-03", tz=DAX.tz), DAX, Config(WINDOW_MODE="open", TRADING_WINDOW=30))
    assert end - start == pd.Timedelta(minutes=30)


def test_sp500_zwykle_6_godzin_roznicy():
    start, end = window_bounds(pd.Timestamp("2026-11-03", tz=SP.tz), SP, OPEN90)
    assert start == pl("2026-11-03 15:30") and end == pl("2026-11-03 17:00")
    assert window_label(start, end, SP) == "15:30–17:00 czasu polskiego (9:30–11:00 Nowy Jork)"


def test_sp500_gdy_europa_juz_zmienila_czas_a_usa_jeszcze_nie():
    # 26.10.2026: Polska już na czasie zimowym, USA jeszcze na letnim – różnica 5 godzin.
    start, end = window_bounds(pd.Timestamp("2026-10-26", tz=SP.tz), SP, OPEN90)
    assert start == pl("2026-10-26 14:30") and end == pl("2026-10-26 16:00")


def test_pierwsze_minuty_na_rynku_calodobowym_uzywa_wlasnych_godzin():
    # Rynek całodobowy nie ma otwarcia, więc „pierwsze minuty” = godziny z WINDOW_START–WINDOW_END.
    start, end = window_bounds(pl("2026-11-03"), EURUSD, OPEN90)
    assert start == pl("2026-11-03 09:00") and end == pl("2026-11-03 10:30")


# ---------------------------------------------------------------- własne godziny

def test_wlasne_godziny_w_czasie_polskim():
    cfg = Config(WINDOW_MODE="custom", WINDOW_START="14:00", WINDOW_END="15:15")
    assert in_window(pl("2026-11-03 14:00"), EURUSD, cfg)
    assert not in_window(pl("2026-11-03 15:15"), EURUSD, cfg)
    assert in_window(pl("2026-11-03 14:30"), DAX, cfg)              # działa też dla rynków z sesją
    assert not in_window(pl("2026-11-03 09:30"), DAX, cfg)


def test_wlasne_godziny_nie_przesuwaja_sie_przy_zmianie_czasu():
    cfg = Config(WINDOW_MODE="custom")
    for day in ("2026-10-23", "2026-10-26"):
        start, _ = window_bounds(pl(day), EURUSD, cfg)
        assert (start.hour, start.minute) == (9, 0)


# ---------------------------------------------------------------- stan giełdy i teksty

def test_stan_gieldy():
    assert market_status(pl("2026-11-06 17:05"), SP)["open"] is True
    closed = market_status(pl("2026-11-06 22:30"), SP)
    assert closed["open"] is False and closed["opens"] == pl("2026-11-09 15:30")   # poniedziałek
    assert market_status(pl("2026-11-07 12:00"), BTC)["open"] is True


def test_nyse_otwarta_po_okienku_90_minut_to_nie_zamknieta_gielda():
    w = window_info(pl("2026-11-06 17:05"), SP, OPEN90)
    assert w["in_window"] is False and w["market_open"] is True
    assert w["text"].startswith("Giełda otwarta do 22:00 czasu polskiego, ale strategia szuka wejść tylko w okienku.")
    assert "Następne okno: w poniedziałek w godzinach 15:30–17:00 czasu polskiego" in w["text"]


def test_teksty_trybu_cala_sesja():
    w = window_info(pl("2026-11-06 17:05"), SP, Config())
    assert w["in_window"] is True and w["market_open"] is True
    assert w["text"] == "Szukamy wejść przez całą sesję, 15:30–22:00 czasu polskiego (9:30–16:00 Nowy Jork)."
    assert w["market_text"] == "Giełda otwarta do 22:00 czasu polskiego"
    closed = window_info(pl("2026-11-06 22:30"), SP, Config())
    assert closed["in_window"] is False and closed["market_open"] is False
    assert closed["text"] == "Giełda zamknięta – otwarcie w poniedziałek o 15:30 czasu polskiego."
    morning = window_info(pl("2026-11-03 08:00"), DAX, Config())
    assert morning["text"] == "Giełda zamknięta – otwarcie dziś o 9:00 czasu polskiego."


def test_rynek_calodobowy_opis():
    w = window_info(pl("2026-11-03 03:00"), EURUSD, Config())
    assert w["label"] == "przez całą dobę, pn–pt" and w["market_text"] == "Rynek otwarty całą dobę"
    assert window_info(pl("2026-11-07 12:00"), BTC, Config())["label"] == "przez całą dobę, 7 dni w tygodniu"
