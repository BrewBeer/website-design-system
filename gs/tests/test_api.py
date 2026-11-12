"""Testy API na danych syntetycznych (bez internetu)."""

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import api.main as main
from engine import instruments as reg
from engine.data import MarketData
from tests.test_backtest import random_market


@pytest.fixture
def client(monkeypatch, tmp_path):
    daily, ltf = random_market()

    def fake_load(inst, cfg):
        return MarketData(daily=daily, hourly=pd.DataFrame(), ltf=ltf, instrument=inst,
                          fx_pln=4.25, warnings=["Dane testowe"])

    monkeypatch.setattr(main.data_mod, "load_market_data", fake_load)
    monkeypatch.setattr(main.data_mod, "has_data", lambda symbol: symbol != "BRAK")
    monkeypatch.setattr(main, "JOURNAL_PATH", tmp_path / "journal.json")
    monkeypatch.setattr(reg, "USER_PATH", tmp_path / "instruments_user.json")
    main.AVAILABILITY.clear()
    return TestClient(main.app)


def analyze(client, instrument="dax", **params):
    return client.post("/api/analyze", json={"instrument": instrument, "ltf_interval": "5m", "params": params})


def test_config_bez_symbolu(client):
    body = client.get("/api/config").json()
    assert body["defaults"]["MIN_RR"] == 1.5
    assert "SYMBOL" not in body["defaults"]


def test_lista_instrumentow(client):
    body = client.get("/api/instruments").json()
    ids = {i["id"] for i in body["instruments"]}
    assert ids >= {"dax", "sp500", "nasdaq100", "wig20", "eurusd", "gold", "btc"}
    groups = {i["group"] for i in body["instruments"]}
    assert groups >= {"Indeksy", "Waluty", "Surowce", "Krypto"}
    assert all(i["available"] is True for i in body["instruments"])
    btc = next(i for i in body["instruments"] if i["id"] == "btc")
    assert btc["session"] == "24x7" and btc["is_24h"]


def test_dodanie_wlasnego_instrumentu(client):
    r = client.post("/api/instruments", json={"symbol": "pkn.wa", "name": "Orlen", "group": "Akcje",
                                              "tz": "Europe/Warsaw", "session": "09:00-17:00",
                                              "tick": 0.01, "point_value": 1, "currency": "PLN"})
    assert r.status_code == 200 and r.json()["id"] == "u-pkn-wa"
    ids = [i["id"] for i in client.get("/api/instruments").json()["instruments"]]
    assert "u-pkn-wa" in ids
    assert reg.USER_PATH.exists()
    assert analyze(client, "u-pkn-wa").status_code == 200


@pytest.mark.parametrize("body, fragment", [
    ({"symbol": "BRAK", "tz": "Europe/Warsaw", "session": "09:00-17:00", "tick": 0.01,
      "point_value": 1, "currency": "PLN"}, "nie ma danych"),
    ({"symbol": "AAPL", "tz": "Mars/Olympus", "session": "09:30-16:00", "tick": 0.01,
      "point_value": 1, "currency": "USD"}, "strefa czasowa"),
    ({"symbol": "AAPL", "tz": "America/New_York", "session": "9-16", "tick": 0.01,
      "point_value": 1, "currency": "USD"}, "GG:MM"),
    ({"symbol": "AAPL", "tz": "America/New_York", "session": "24x5", "tick": 0,
      "point_value": 1, "currency": "USD"}, "większe od zera"),
])
def test_bledny_wlasny_instrument(client, body, fragment):
    r = client.post("/api/instruments", json=body)
    assert r.status_code == 422 and fragment in r.json()["error"]


def test_analyze_kontrakt(client):
    body = analyze(client, MIN_TOUCHES=1, MIN_RR=0.5).json()
    for key in ("run_id", "meta", "trend", "candles", "levels", "signals", "watchlist", "backtest", "trades"):
        assert key in body
    assert body["meta"]["custom_params"] is True
    assert body["meta"]["instrument"]["id"] == "dax"
    assert "brokera" in body["meta"]["broker_note"]
    assert "czasu polskiego" in body["meta"]["window"]["text"]
    for s in body["signals"]:
        assert s["status"] in ("active", "pending", "filled", "cancelled", "rejected")


def test_ryzyko_w_zl_z_wartosci_punktu_i_kursu(client):
    body = analyze(client, MIN_TOUCHES=1, MIN_RR=0.1, MAX_RISK_ATR=99, USE_TREND_FILTER=False,
                   POINT_VALUE=2).json()
    s = next(s for s in body["signals"] if s["status"] != "rejected")
    risk_pts = abs(s["entry"] - s["sl"])
    # 1% z 10 000 zł = 100 zł; 1 pkt = 2 EUR × 4,25 zł
    assert s["size"] == pytest.approx(100 / (risk_pts * 2 * 4.25), rel=0.01)
    assert s["risk_pln"] == pytest.approx(100, abs=0.5)
    assert body["meta"]["custom_params"] is True
    assert body["meta"]["instrument"]["point_value"] == 2


def test_parametry_domyslne_to_nie_ustawienia_wlasne(client):
    assert analyze(client).json()["meta"]["custom_params"] is False


def test_nieznany_instrument_daje_czytelny_blad(client):
    r = analyze(client, "xyz")
    assert r.status_code == 422 and "Nieznany instrument" in r.json()["error"]


def test_zly_interwal(client):
    r = client.post("/api/analyze", json={"instrument": "dax", "ltf_interval": "1m", "params": {}})
    assert r.status_code == 400 and "5m albo 15m" in r.json()["error"]


def test_csv_i_dziennik(client):
    body = analyze(client, MIN_TOUCHES=1, MIN_RR=0.1, MAX_RISK_ATR=99, USE_TREND_FILTER=False, MERGE_TOL=0).json()
    assert body["trades"], "potrzebna choć jedna transakcja"
    key = body["trades"][0]["key"]
    assert client.post("/api/journal", json={"key": key, "note": "Za wcześnie weszłem"}).json()["ok"]
    assert client.get("/api/journal").json()[key]["note"] == "Za wcześnie weszłem"
    csv = client.get(f"/api/trades.csv?run_id={body['run_id']}")
    assert csv.status_code == 200 and "Za wcześnie weszłem" in csv.text
    assert client.get("/api/trades.csv?run_id=brak").status_code == 404


# ---------------------------------------------------------------- poprawki z przeglądu kodu

def test_uszkodzony_plik_wlasnych_instrumentow_nie_jest_nadpisywany(client):
    reg.USER_PATH.write_text("[{to nie jest json", encoding="utf-8")
    r = client.post("/api/instruments", json={"symbol": "AAPL", "tz": "America/New_York", "session": "09:30-16:00",
                                              "tick": 0.01, "point_value": 1, "currency": "USD"})
    assert r.status_code == 422 and "uszkodzony" in r.json()["error"]
    assert reg.USER_PATH.read_text(encoding="utf-8") == "[{to nie jest json"   # nic nie zginęło


def test_wpis_z_nieznanym_polem_zostaje_w_pliku(client):
    import json
    reg.USER_PATH.write_text(json.dumps([{"id": "u-old", "stare_pole": 1}]), encoding="utf-8")
    client.post("/api/instruments", json={"symbol": "AAPL", "tz": "America/New_York", "session": "09:30-16:00",
                                          "tick": 0.01, "point_value": 1, "currency": "USD"})
    ids = [i["id"] for i in json.loads(reg.USER_PATH.read_text(encoding="utf-8"))]
    assert ids == ["u-old", "u-aapl"]


def test_cors_tylko_localhost(client):
    ok = client.get("/api/journal", headers={"Origin": "http://127.0.0.1:5500"})
    assert ok.headers.get("access-control-allow-origin") == "http://127.0.0.1:5500"
    for origin in ("null", "https://zla-strona.example"):
        r = client.get("/api/journal", headers={"Origin": origin})
        assert "access-control-allow-origin" not in r.headers


def test_uszkodzony_dziennik_nie_jest_nadpisywany(client):
    main.JOURNAL_PATH.write_text("{zepsute", encoding="utf-8")
    r = client.post("/api/journal", json={"key": "k", "note": "notatka"})
    assert r.status_code == 422
    assert main.JOURNAL_PATH.read_text(encoding="utf-8") == "{zepsute"


@pytest.mark.parametrize("params, fragment", [
    ({"SMA_SLOW": 0}, "SMA_SLOW"),
    ({"SWING_N": 0}, "SWING_N"),
    ({"KAPITAL": -5}, "Kapitał"),
    ({"SMA_FAST": 50, "SMA_SLOW": 20}, "SMA_FAST"),
])
def test_parametry_poza_zakresem(client, params, fragment):
    r = analyze(client, **params)
    assert r.status_code == 400 and fragment in r.json()["error"]


def test_opis_trendu_z_aktualnych_okresow_sma():
    from engine.config import Config
    from engine.signals import trend_text
    assert trend_text("long", Config(SMA_FAST=20, SMA_SLOW=100)) == "Nad SMA 20 i 100 – tylko long"


def test_plan_na_sesje_w_odpowiedzi(client):
    plan = analyze(client).json()["session_plan"]
    assert plan["title"].startswith("Plan na")
    assert 1 <= len(plan["lines"]) <= 8
    assert any(l["kind"] == "rules" and "maks. 2 transakcje" in l["text"] for l in plan["lines"])


def test_strefy_maja_zrodla_i_flage_dzienna(client):
    for lv in analyze(client).json()["levels"]:
        assert lv["frame"] == "+".join(lv["sources"])
        assert lv["daily"] == ("D" in lv["sources"])


def test_wyniki_w_okresach(client):
    perf = analyze(client).json()["performance"]
    assert [p["key"] for p in perf["periods"]] == ["1m", "3m", "ytd", "1y"]
    for p in perf["periods"]:
        assert {"stats", "equity", "days_with_data", "days_expected", "complete", "sum_pln"} <= p.keys()


def test_mozliwy_zysk_w_sygnale_i_zleceniu(client):
    body = analyze(client, MIN_TOUCHES=1, MIN_RR=0.1, MAX_RISK_ATR=99, USE_TREND_FILTER=False).json()
    for s in (s for s in body["signals"] if s["tp"] is not None):
        assert s["reward_pln"] == pytest.approx(s["risk_pln"] * s["rr"], rel=0.02, abs=0.5)


def test_sprawdzenie_symbolu_podpowiada_godziny_gieldy_i_wczytuje_istniejacy(client, monkeypatch):
    monkeypatch.setattr(main.data_mod, "check_symbol", lambda s: {"symbol": s.upper(), "last_price": 200.0,
                                                                  "currency": "USD", "tz": "America/New_York",
                                                                  "daily_bars": 250, "intraday": True})
    info = client.get("/api/instruments/check?symbol=aapl").json()
    assert info["session"] == "09:30-16:00" and info["existing"] is None
    client.post("/api/instruments", json={"symbol": "AAPL", "tz": "America/New_York", "session": "15:00-21:00",
                                          "tick": 0.01, "point_value": 1, "currency": "USD"})
    assert client.get("/api/instruments/check?symbol=AAPL").json()["existing"]["session"] == "15:00-21:00"
