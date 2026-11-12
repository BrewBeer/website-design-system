"""Testy planu na sesję: kierunek ze swingów, scenariusze, otwarcie, podsumowanie."""

import pandas as pd

from engine.config import Config
from engine.instruments import get_instrument
from engine.plan import direction, opening_text, scenarios, structure, summary_text, zones_text
from tests.helpers import bars, daily

DAX = get_instrument("dax")
UP = [10, 11, 13, 12, 11, 12, 14, 16, 15, 14, 15, 17, 19, 18, 17, 18, 19, 20]   # wyższe szczyty i dołki


def swings_df(values):
    return daily([(v, v + 0.5, v - 0.5, v) for v in values])


def down():
    return swings_df([30 - v for v in UP])


# ---------------------------------------------------------------- a) kierunek

def test_struktura_wzrostowa_spadkowa_boczna():
    assert structure(swings_df(UP), 2)["state"] == "up"
    assert structure(down(), 2)["state"] == "down"
    # Szczyty coraz wyższe, dołki coraz niższe = brak jednego kierunku.
    expanding = [10, 11, 13, 12, 9, 12, 15, 14, 8, 13, 17, 15, 7, 12, 14]
    assert structure(swings_df(expanding), 2)["state"] == "mixed"


def test_kierunek_zdanie_ze_szczytami():
    d = direction(down(), pd.DataFrame(), 2, 1)
    assert d["state"] == "down"
    assert d["text"] == "Kierunek: spadkowy – szczyt 13,5 niższy od 16,5 (wykres dzienny). Preferuj sprzedaż."


def test_kierunek_laczenie_ram():
    assert direction(swings_df(UP), swings_df(UP), 2, 1)["text"].endswith("(wykres dzienny i 30 min). Preferuj kupno.")
    assert direction(swings_df(UP), down(), 2, 1)["state"] == "mixed"           # przeciwne ramy = boczny
    flat = swings_df([10] * 20)
    assert "(wykres 30 min)" in direction(flat, down(), 2, 1)["text"]           # dzienny boczny → 30 min


# ---------------------------------------------------------------- c) scenariusze

def zone(i, low, high, frame, daily_=False):
    return {"id": i, "low": low, "high": high, "touches": 3, "frame": frame,
            "sources": [frame], "daily": daily_}


LEVELS = [zone("L1", 106, 107, "D", True), zone("L2", 101, 101.5, "H1"),
          zone("L3", 98.5, 99, "H1"), zone("L4", 93, 94, "D", True)]


def scen(dir_state, levels=LEVELS, sma="mixed", cfg=None):
    return scenarios(dir_state, levels, 100.0, 1.0, sma, cfg or Config(), DAX, 1.0)


def test_spadkowy_preferowana_sprzedaz_pierwsza():
    s = scen("down")
    assert [x["kind"] for x in s] == ["preferred", "counter"]
    assert s[0]["text"] == ("1. Preferowany: Jeśli świeca 5m zamknie się pod 98,5, to zlecenie stop sprzedaży "
                            "po ok. 97,9, SL 99,1, TP 94,1, R:R 3,17.")
    assert s[0]["zone_id"] == "L3" and s[0]["setup"] == "A" and s[0]["side"] == "short"


def test_przeciw_trendowi_tylko_przy_strefie_d():
    s = scen("down")
    assert s[1]["text"].startswith("2. Przeciw trendowi: Jeśli knot zejdzie pod 93")
    assert s[1]["zone_id"] == "L4"            # strefa D, a nie bliższa strefa H1
    no_d = [z | {"daily": False} for z in LEVELS]
    assert scen("down", no_d)[1]["text"] == "2. Przeciw trendowi (kupno) pominięty: brak strefy D pod ceną."


def test_scenariusz_z_niskim_rr_pominiety_z_powodem():
    close = [zone("L1", 102.5, 103, "H1"), zone("L2", 101, 101.5, "H1"), zone("L3", 98.5, 99, "H1")]
    s = scen("up", close)
    assert s[0]["kind"] == "skipped" and s[0]["ok"] is False
    assert s[0]["text"] == ("1. Preferowany (kupno) pominięty: R:R 0,25 poniżej minimum 1,5 "
                            "– następna strefa jest za blisko.")


def test_scenariusz_sprzeczny_z_filtrem_sma_pominiety():
    s = scen("down", sma="long")
    assert "filtr średnich SMA pozwala dziś tylko na kupno" in s[0]["text"]


def test_boczny_dwa_scenariusze_bez_preferencji():
    s = scen("mixed")
    assert [x["kind"] for x in s] == ["neutral", "neutral"]
    assert s[0]["side"] == "long" and s[1]["side"] == "short"


def test_strefy_cena_w_strefie():
    text = zones_text(LEVELS + [zone("L5", 99.5, 100.5, "PDC")], 100.0, 1)
    assert text == "Strefy: zamknięcie 100 w strefie PDC 99,5–100,5; nad nią H1 ×3 101–101,5; pod nią H1 ×3 98,5–99."


# ---------------------------------------------------------------- d) otwarcie

Z = zone("L2", 101, 101.5, "H1")


def test_otwarcie_przed_sesja_podaje_progi_luki():
    t = opening_text(pd.DataFrame(), 100.0, 2.0, Z, 1)
    assert t.startswith("Na otwarciu: luka, jeśli pierwsza cena będzie poniżej 98 albo powyżej 102 (PDC 100).")


def test_luka_w_gore_i_pierwsze_swiece_nad_strefa():
    b = bars([(103, 103.5, 102.8, 103.2), (103.2, 103.6, 103, 103.4), (103.4, 103.9, 103.2, 103.8)])
    assert opening_text(b, 100.0, 2.0, Z, 1) == \
        "Otwarcie: luka w górę – 103, 3 pkt nad PDC 100. Pierwsze 3 świece zamknęły się nad strefą 101–101,5."


def test_otwarcie_bez_luki():
    b = bars([(100.5, 101, 100, 100.8)])
    assert opening_text(b, 100.0, 2.0, Z, 1).startswith("Otwarcie bez luki: 100,5 (PDC 100).")


# ---------------------------------------------------------------- podsumowanie

def test_podsumowanie_zrealizowany_scenariusz():
    plan = scen("down")
    sig = {"id": "S1", "status": "filled", "level_id": "L3", "setup": "A", "side": "short"}
    trade = {"signal_id": "S1", "result_r": 2.0, "exit_reason": "tp"}
    day = pd.Timestamp("2026-11-06", tz="Europe/Berlin")
    assert summary_text(day, plan, [sig], [trade], 1) == \
        "Podsumowanie 06.11: zrealizował się scenariusz 1 (sprzedaż) – wynik +2,00 R (take profit)."


def test_podsumowanie_zaden_scenariusz():
    day = pd.Timestamp("2026-11-06", tz="Europe/Berlin")
    other = {"id": "S9", "status": "filled", "level_id": "L1", "setup": "C", "side": "long"}
    assert summary_text(day, scen("down"), [other], [{"signal_id": "S9", "result_r": -1.0, "exit_reason": "sl"}], 1) == \
        "Podsumowanie 06.11: żaden scenariusz się nie zrealizował. Inne transakcje: 1, razem -1,00 R."
