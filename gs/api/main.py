"""API FastAPI dla GS. Serwuje też interfejs z katalogu web/.

Uruchomienie (z katalogu gs/):  uvicorn api.main:app --reload
"""

from __future__ import annotations

import csv
import io
import json
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from engine import data as data_mod
from engine import instruments as reg
from engine.backtest import simulate, compute_stats, watchlist, chart_time
from engine.config import Config, PARAM_INFO
from engine.decision import data_age, decide
from engine.performance import performance
from engine.plan import build_plan
from engine.data import DataError
from engine.instruments import InstrumentError
from engine.levels import key_levels
from engine.signals import (trend_state, trend_text, window_bounds, window_text, market_status,
                            day_tz, PL_TZ)

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
JOURNAL_PATH = ROOT / "data" / "journal.json"
JOURNAL_LOCK = threading.Lock()

app = FastAPI(title="GS – silnik sygnałów", docs_url="/api/docs", redoc_url=None)

# Pozwalamy na zapytania ze stron otwartych lokalnie w inny sposób (Live Server, podgląd
# w VS Code). Tylko localhost. Celowo BEZ origin "null": wysłać go może każda strona
# z internetu (ramka sandbox), a plik otwarty z dysku i tak nie uruchamia modułów JS.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

# Ostatnie wyniki, żeby /api/trades.csv mógł je oddać bez liczenia od nowa.
RUNS: OrderedDict[str, dict] = OrderedDict()
MAX_RUNS = 20

# Czy instrument ma dane: sprawdzamy raz na godzinę (id -> (czas sprawdzenia, wynik)).
AVAILABILITY: dict[str, tuple[float, bool]] = {}
AVAILABILITY_TTL = 3600

BROKER_NOTE = "Tick i wartość punktu dla CFD o rozmiarze 1 – do sprawdzenia u Twojego brokera."
WEEKDAYS = ["w poniedziałek", "we wtorek", "w środę", "w czwartek", "w piątek", "w sobotę", "w niedzielę"]


class AnalyzeRequest(BaseModel):
    instrument: str = "dax"
    ltf_interval: str = "5m"
    params: dict = {}


class JournalEntry(BaseModel):
    key: str
    note: str


class CustomInstrument(BaseModel):
    symbol: str
    name: str = ""
    group: str = "Własne"
    tz: str
    session: str
    tick: float
    point_value: float
    currency: str


@app.exception_handler(DataError)
async def data_error_handler(_: Request, exc: DataError):
    return JSONResponse(status_code=422, content={"error": str(exc)})


@app.exception_handler(InstrumentError)
async def instrument_error_handler(_: Request, exc: InstrumentError):
    return JSONResponse(status_code=422, content={"error": str(exc)})


@app.exception_handler(ValueError)
async def value_error_handler(_: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"error": f"Błędny parametr: {exc}"})


# ---------------------------------------------------------------- konfiguracja i instrumenty

@app.get("/api/config")
def get_config():
    return {"defaults": Config().to_dict(), "params": PARAM_INFO, "intervals": ["5m", "15m"],
            "broker_note": BROKER_NOTE}


@app.get("/api/instruments")
def get_instruments():
    items = reg.all_instruments()
    status = _availability(items)
    now = pd.Timestamp.now(tz=PL_TZ)
    return {"groups": reg.GROUPS, "broker_note": BROKER_NOTE,
            "instruments": [{**i.to_dict(), "available": status[i.id],
                             "market_open": market_status(now, i)["open"]} for i in items]}


@app.get("/api/instruments/check")
def check_instrument(symbol: str):
    """Sprawdza, czy Yahoo ma dane dla symbolu, i podpowiada walutę, strefę czasową i godziny sesji.

    Jeśli taki własny instrument już istnieje, zwraca jego ustawienia (formularz służy też do poprawek).
    """
    info = data_mod.check_symbol(symbol)
    info["session"] = reg.EXCHANGE_HOURS.get(info.get("tz") or "")
    existing = next((i for i in reg.load_user() if i.symbol == info["symbol"]), None)
    info["existing"] = existing.to_dict() if existing else None
    return info


@app.post("/api/instruments")
def add_instrument(body: CustomInstrument):
    inst = reg.make_custom(body.model_dump())
    if not data_mod.has_data(inst.symbol):
        raise DataError(f"Yahoo nie ma danych dla symbolu „{inst.symbol}”, więc nie dodano instrumentu.")
    reg.save_user(inst)
    AVAILABILITY[inst.id] = (time.time(), True)
    return {**inst.to_dict(), "available": True}


def _availability(items) -> dict[str, bool]:
    """Sprawdza dostępność danych równolegle, z wynikiem zapamiętanym na godzinę."""
    now = time.time()
    todo = [i for i in items if i.id not in AVAILABILITY or now - AVAILABILITY[i.id][0] > AVAILABILITY_TTL]
    if todo:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for inst, ok in zip(todo, pool.map(lambda i: data_mod.has_data(i.symbol), todo)):
                AVAILABILITY[inst.id] = (now, ok)
    return {i.id: AVAILABILITY[i.id][1] for i in items}


# ---------------------------------------------------------------- analiza

@app.post("/api/analyze")
def analyze(req: AnalyzeRequest):
    inst = reg.get_instrument(req.instrument)
    defaults = Config().to_dict()
    params = {k: v for k, v in (req.params or {}).items() if k in defaults}
    cfg = Config.from_overrides({**params, "LTF_INTERVAL": req.ltf_interval})
    effective = reg.with_overrides(inst, cfg.TICK, cfg.POINT_VALUE)

    # „Ustawienia własne” = coś różni się od pliku config.py albo od rejestru instrumentów.
    custom = any(cfg.to_dict()[k] != defaults[k] for k in params if k not in ("TICK", "POINT_VALUE"))
    custom |= effective.tick != inst.tick or effective.point_value != inst.point_value

    md = data_mod.load_market_data(effective, cfg)
    result = build_response(md, cfg, custom)
    RUNS[result["run_id"]] = {"name": inst.id, "trades": result["trades"]}
    while len(RUNS) > MAX_RUNS:
        RUNS.popitem(last=False)
    return result


@app.get("/api/trades.csv")
def trades_csv(run_id: str):
    run = RUNS.get(run_id)
    if run is None:
        return JSONResponse(status_code=404, content={"error": "Nie znaleziono wyniku. Uruchom analizę ponownie."})
    notes = _load_journal()
    buf = io.StringIO()
    buf.write("﻿")  # BOM, żeby Excel poprawnie pokazał polskie znaki
    w = csv.writer(buf, delimiter=";")
    w.writerow(["data wejścia", "data wyjścia", "wzorzec", "kierunek", "wejście", "SL", "TP",
                "wynik R", "powód wyjścia", "notatka"])
    pl = lambda x: "" if x is None else str(x).replace(".", ",")
    for t in run["trades"]:
        w.writerow([t["entry_iso"], t["exit_iso"], t["setup"], t["side"], pl(t["entry"]), pl(t["sl"]),
                    pl(t["tp"]), pl(t["result_r"]), t["exit_reason"], notes.get(t["key"], {}).get("note", "")])
    filename = f"gs_transakcje_{run['name']}_{run_id[:8]}.csv"
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ---------------------------------------------------------------- dziennik

@app.get("/api/journal")
def get_journal():
    return _load_journal()


@app.post("/api/journal")
def post_journal(entry: JournalEntry):
    # Blokada: dwie notatki zapisane naraz nie mogą nadpisać jedna drugiej.
    with JOURNAL_LOCK:
        journal = _load_journal(strict=True)
        if entry.note.strip():
            journal[entry.key] = {"note": entry.note.strip(),
                                  "updated": pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds")}
        else:
            journal.pop(entry.key, None)
        reg.atomic_write_json(JOURNAL_PATH, journal)
    return {"ok": True}


def _load_journal(strict: bool = False) -> dict:
    """Notatki z pliku. Przy zapisie (strict) uszkodzony plik to błąd, żeby go nie nadpisać."""
    if not JOURNAL_PATH.exists():
        return {}
    try:
        return json.loads(JOURNAL_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        if strict:
            raise DataError(f"Plik {JOURNAL_PATH.name} jest uszkodzony, więc nie zapisano notatki "
                            "(żeby nie stracić pozostałych). Popraw go albo usuń.") from None
        return {}


# ---------------------------------------------------------------- budowa odpowiedzi

def build_response(md, cfg: Config, custom: bool, now: pd.Timestamp | None = None) -> dict:
    inst = md.instrument
    dec = inst.price_decimals
    tz = day_tz(inst)
    now = now or pd.Timestamp.now(tz=tz)
    today = now.tz_convert(tz).normalize()
    warnings = list(md.warnings)

    # Jedna symulacja na całej historii z archiwum (wyniki 1M/3M/YTD/rok). Wykres, dziennik
    # i statystyki „ostatnich dni” biorą z niej tylko zakres md.ltf (LTF_DAYS).
    full = md.ltf_full if md.ltf_full is not None and not md.ltf_full.empty else md.ltf
    sim = simulate(md.daily, md.hourly, full, inst, cfg, now=now, fx_pln=md.fx_pln)
    warnings += sim["warnings"]
    cutoff = md.ltf.index[0] if not md.ltf.empty else None
    recent = lambda ts: cutoff is None or ts >= cutoff
    recent_trades = [t for t in sim["trades"] if recent(t["entry_ts"])]

    # Poziomy i trend „na teraz”: z danych sprzed dzisiejszego dnia handlowego.
    daily_before = md.daily[md.daily.index < today]
    levels = key_levels(md.daily, md.hourly, cfg, asof=today)
    trend = trend_state(daily_before, cfg)

    intraday = not md.ltf.empty
    candles_src = md.ltf if intraday else md.daily
    last_price = float(candles_src["close"].iloc[-1])
    # Zmiana dzienna liczona względem zamknięcia dnia PRZED dniem ostatniej ceny.
    # (Przed otwarciem sesji ostatnia cena jest z wczoraj, więc porównujemy z przedwczoraj.)
    last_day = candles_src.index[-1].tz_convert(tz).normalize()
    ref = md.daily[md.daily.index < last_day]
    prev_close = float(ref["close"].iloc[-1]) if len(ref) else None
    change_pct = (last_price / prev_close - 1) * 100 if prev_close else None

    signals = [_signal_out(s) for s in sim["signals"] if recent(s["ts"])]
    active = next((s for s in reversed(signals) if s["status"] in ("active", "pending")), None)
    window = window_info(now, inst, cfg)
    freshness = data_age(md.ltf.index[-1] if intraday else None, cfg.LTF_INTERVAL, now)
    decision = decide(active_signal=active, window=window, freshness=freshness, levels=levels,
                      last_price=last_price, trend=trend["state"], intraday=intraday, inst=inst, cfg=cfg)
    is_today = intraday and md.ltf.index[-1].tz_convert(tz).normalize() == today
    rp = lambda x: round(float(x), dec)

    return {
        "run_id": uuid.uuid4().hex,
        "meta": {
            "symbol": inst.symbol, "name": inst.name, "ltf": cfg.LTF_INTERVAL if intraday else "1d",
            "instrument": inst.to_dict(), "fx_pln": round(md.fx_pln, 4), "broker_note": BROKER_NOTE,
            "data_from": candles_src.index[0].isoformat() if len(candles_src) else None,
            "data_to": candles_src.index[-1].isoformat() if len(candles_src) else None,
            "delayed": md.delayed, "warnings": warnings, "intraday": intraday,
            "last_price": rp(last_price),
            "change_pct": round(change_pct, 2) if change_pct is not None else None,
            "custom_params": custom, "params": cfg.to_dict(),
            "trend_text": trend_text(trend["state"], cfg) if trend["enough_data"] else "Za mało danych do filtra trendu",
            "window": window, "source": md.source,
            "trades_today": sim.get("trades_today", 0) if is_today else 0, "max_trades": cfg.MAX_TRADES,
        },
        "trend": {"sma15": _r(trend["sma15"], dec), "sma45": _r(trend["sma45"], dec), "state": trend["state"]},
        "candles": _candles_out(candles_src, rp),
        "levels": [_level_out(l, dec) for l in levels],
        "sma": _sma_series(sim["days"], md.ltf, dec),
        "signals": signals,
        "active_signal": active,
        "decision": decision,
        "session_plan": build_plan(md, cfg, sim, now),
        "watchlist": watchlist(levels, last_price, trend["state"], md.ltf, cfg, dec),
        "backtest": compute_stats(recent_trades),
        "performance": performance(sim["trades"], full, inst, tz, now, cfg),
        "trades": [_trade_out(t, inst.id) for t in recent_trades],
    }


def _r(x, dec):
    return round(x, dec) if x is not None else None


def _candles_out(df: pd.DataFrame, rp) -> list[dict]:
    """Świece dla wykresu. Czas wykresu to godzina z zegara giełdy, więc przy zmianie
    czasu na zimowy godzina 2:00–3:00 występuje dwa razy. Wykres wymaga rosnących
    czasów, więc powtórzoną godzinę pomijamy (tylko na wykresie, backtest ma wszystko)."""
    out, last = [], None
    for ts, r in df.iterrows():
        t = chart_time(ts)
        if last is not None and t <= last:
            continue
        last = t
        out.append({"time": t, "open": rp(r.open), "high": rp(r.high), "low": rp(r.low),
                    "close": rp(r.close), "volume": float(r.volume)})
    return out


def _level_out(l: dict, dec: int) -> dict:
    return {"id": l["id"], "low": round(l["low"], dec), "high": round(l["high"], dec),
            "touches": l["touches"], "last_touch": pd.Timestamp(l["last_touch"]).isoformat(),
            "frame": l["frame"], "sources": l["sources"], "daily": l["daily"]}


def _signal_out(s: dict) -> dict:
    out = {k: v for k, v in s.items() if k != "ts"}
    out["iso"] = s["ts"].isoformat()
    return out


def _trade_out(t: dict, instrument_id: str) -> dict:
    out = {k: v for k, v in t.items() if k not in ("entry_ts", "exit_ts")}
    out["entry_iso"] = t["entry_ts"].isoformat(timespec="minutes")
    out["exit_iso"] = t["exit_ts"].isoformat(timespec="minutes")
    # Stały klucz notatki: nie zmienia się między uruchomieniami (w przeciwieństwie do T1, T2…).
    out["key"] = f"{instrument_id}|{out['entry_iso']}|{t['setup']}|{t['side']}"
    return out


def _sma_series(days: list[dict], ltf: pd.DataFrame, dec: int) -> list[dict]:
    """Średnie z dziennego jako schodki na osi LTF: jedna wartość na cały dzień."""
    if ltf.empty:
        return []
    by_day: dict = {}   # dzień -> [pierwsza świeca, ostatnia świeca]
    for ts in ltf.index:
        local_day = ts.tz_convert(days[0]["day"].tz).date() if days else ts.date()
        by_day.setdefault(local_day, [ts, ts])[1] = ts
    points = []
    for d in days:
        if d["trend"]["sma15"] is None or d["day"].date() not in by_day:
            continue
        for ts in by_day[d["day"].date()]:
            points.append({"time": chart_time(ts), "sma15": round(d["trend"]["sma15"], dec),
                           "sma45": round(d["trend"]["sma45"], dec)})
    return points


# ---------------------------------------------------------------- okienko czasowe

def _when(day, today) -> str:
    diff = (day - today).days
    return "dziś" if diff == 0 else "jutro" if diff == 1 else WEEKDAYS[day.weekday()]


def window_info(now: pd.Timestamp, inst, cfg: Config) -> dict:
    """Okienko strategii i stan giełdy: okienko trwa / giełda otwarta, ale poza okienkiem / zamknięta."""
    tz = day_tz(inst)
    local = now.tz_convert(tz)
    today = local.date()
    market = market_status(now, inst)
    pl = lambda ts: ts.tz_convert(PL_TZ)
    hm = lambda ts: f"{pl(ts).hour}:{pl(ts).minute:02d}"

    if market["open"]:
        market_text = ("Rynek otwarty całą dobę" if inst.is_24h
                       else f"Giełda otwarta do {hm(market['closes'])} czasu polskiego")
    elif market["opens"] is not None:
        market_text = (f"Giełda zamknięta – otwarcie {_when(pl(market['opens']).date(), today)} "
                       f"o {hm(market['opens'])} czasu polskiego")
    else:
        market_text = "Giełda zamknięta"

    # Bieżące albo najbliższe okienko w dniu handlowym.
    start = end = None
    day = local.normalize()
    for _ in range(8):
        if inst.trades_on(day.weekday()):
            s_, e_ = window_bounds(day, inst, cfg)
            if now < e_:
                start, end = s_, e_
                break
        day += timedelta(days=1)
    label = window_text(start, end, inst, cfg) if start is not None else ""
    in_window = start is not None and start <= now < end
    base = {"in_window": in_window, "label": label, "mode": cfg.WINDOW_MODE,
            "market_open": market["open"], "market_text": market_text}

    if in_window:
        return {**base, "text": f"Szukamy wejść {label}."}
    when = _when(day.date(), today) if start is not None else ""
    if market["open"]:
        # Giełda działa, ale wybrane okienko to tylko część sesji.
        return {**base, "text": f"{market_text}, ale strategia szuka wejść tylko w okienku. "
                                f"Następne okno: {when} {label}. Możesz to zmienić w Ustawieniach "
                                "(„Kiedy szukać wejść”)."}
    if cfg.WINDOW_MODE == "session":
        return {**base, "text": f"{market_text}."}
    return {**base, "text": f"{market_text}. Następne okno: {when} {label}."}


# Pliki interfejsu podpinamy na końcu, żeby nie przykryły ścieżek /api.
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
