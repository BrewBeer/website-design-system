"""Plan na sesję: krótki tekst (maks. 8 linii) generowany automatycznie.

Przed okienkiem i w trakcie sesji: plan na dziś. Po sesji: jedna linia podsumowania
(który scenariusz się zrealizował) i plan na następną sesję.

Linie planu:
  a) kierunek ze struktury swingów (wykres dzienny i 30 min),
  b) najbliższe strefy nad i pod ceną,
  c) dwa scenariusze warunkowe (preferowany pierwszy; przeciw trendowi tylko przy strefie D),
  d) na co patrzeć na otwarciu (luka, pierwsze świece),
  e) przypomnienie zasad.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from .backtest import fmt_price
from .config import Config
from .decision import condition_text
from .instruments import Instrument
from .levels import atr, find_swings, key_levels
from .signals import build_order, day_tz, trend_state, window_bounds, window_text

WEEKDAY_NAMES = ["poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela"]
WEEKDAY_ACC = ["poniedziałek", "wtorek", "środę", "czwartek", "piątek", "sobotę", "niedzielę"]
SIDE_WORD = {"long": "kupno", "short": "sprzedaż"}


# ---------------------------------------------------------------- a) kierunek ze struktury swingów

def structure(df: pd.DataFrame, n: int) -> dict:
    """Dwa ostatnie szczyty i dwa ostatnie dołki: wyższe = wzrostowy, niższe = spadkowy, inaczej boczny."""
    swings = find_swings(df, n) if len(df) > 2 * n else []
    highs = [s["price"] for s in swings if s["kind"] == "high"][-2:]
    lows = [s["price"] for s in swings if s["kind"] == "low"][-2:]
    if len(highs) < 2 or len(lows) < 2:
        return {"state": "mixed", "highs": highs, "lows": lows}
    if highs[1] > highs[0] and lows[1] > lows[0]:
        state = "up"
    elif highs[1] < highs[0] and lows[1] < lows[0]:
        state = "down"
    else:
        state = "mixed"
    return {"state": state, "highs": highs, "lows": lows}


def direction(daily: pd.DataFrame, m30: pd.DataFrame, n: int, dec: int) -> dict:
    """Łączy kierunek z wykresu dziennego i 30-minutowego w jedno zdanie.

    Zgodne → ten kierunek; jeden boczny → kierunek drugiego; przeciwne → boczny.
    """
    d, m = structure(daily, n), structure(m30, n) if m30 is not None and not m30.empty else {"state": "mixed"}
    if d["state"] == m["state"]:
        state, src, frame = d["state"], d, "dzienny i 30 min"
    elif d["state"] == "mixed":
        state, src, frame = m["state"], m, "30 min"
    elif m["state"] == "mixed":
        state, src, frame = d["state"], d, "dzienny"
    else:
        state, src, frame = "mixed", d, ""

    p = lambda x: fmt_price(x, dec)
    if state == "down":
        text = (f"Kierunek: spadkowy – szczyt {p(src['highs'][1])} niższy od {p(src['highs'][0])} "
                f"(wykres {frame}). Preferuj sprzedaż.")
    elif state == "up":
        text = (f"Kierunek: wzrostowy – dołek {p(src['lows'][1])} wyższy od {p(src['lows'][0])} "
                f"(wykres {frame}). Preferuj kupno.")
    else:
        text = "Kierunek: boczny – szczyty i dołki nie układają się w jedną stronę. Bez preferencji."
    return {"state": state, "text": text}


# ---------------------------------------------------------------- b) strefy

def zone_text(z: dict, dec: int) -> str:
    """„D+H1 ×14 4136,7–4142,4” – jak podpis strefy na wykresie."""
    count = f" ×{z['touches']}" if any(s in ("D", "H1") for s in z.get("sources", [z["frame"]])) else ""
    return f"{z['frame']}{count} {fmt_price(z['low'], dec)}–{fmt_price(z['high'], dec)}"


def zones_text(levels: list[dict], price: float, dec: int) -> str:
    """Najbliższa strefa nad i pod ceną; gdy cena stoi w strefie, mówimy to wprost."""
    inside = next((l for l in levels if l["low"] <= price <= l["high"]), None)
    above = min((l for l in levels if l["low"] > price), key=lambda l: l["low"], default=None)
    below = max((l for l in levels if l["high"] < price), key=lambda l: l["high"], default=None)
    parts = [f"zamknięcie {fmt_price(price, dec)}" + (f" w strefie {zone_text(inside, dec)}" if inside else "")]
    parts.append(f"nad nią {zone_text(above, dec)}" if above else "nad nią brak stref")
    parts.append(f"pod nią {zone_text(below, dec)}" if below else "pod nią brak stref")
    return "Strefy: " + "; ".join(parts) + "."


def nearest_zones(levels: list[dict], price: float) -> tuple[dict | None, dict | None]:
    """Strefy-wyzwalacze: nad ceną (wybicie w górę przez górną krawędź) i pod ceną (przez dolną).
    Strefa, w której stoi cena, może być obiema."""
    above = min((l for l in levels if l["high"] > price), key=lambda l: l["high"], default=None)
    below = max((l for l in levels if l["low"] < price), key=lambda l: l["low"], default=None)
    return above, below


# ---------------------------------------------------------------- c) scenariusze

def hypothetical_bar(setup: str, side: str, zone: dict, a: float) -> pd.Series:
    """Świeca sygnałowa o typowej wielkości (ATR z 5m) przy strefie – do policzenia orientacyjnych cen."""
    if setup == "A":   # wybicie: świeca przechodzi przez krawędź strefy
        edge = zone["high"] if side == "long" else zone["low"]
        low, high = edge - a / 2, edge + a / 2
    elif side == "long":   # C long: knot pod strefą, zamknięcie nad dolną krawędzią
        low, high = zone["low"] - a / 3, zone["low"] + 2 * a / 3
    else:                  # C short: knot nad strefą, zamknięcie pod górną krawędzią
        low, high = zone["high"] - 2 * a / 3, zone["high"] + a / 3
    return pd.Series({"open": (low + high) / 2, "high": high, "low": low, "close": (low + high) / 2})


def scenario(no: int, label: str, kind: str, setup: str, side: str, zone: dict | None,
             levels: list[dict], atr_ltf: float | None, sma_state: str,
             cfg: Config, inst: Instrument, fx: float, missing: str) -> dict:
    """Jedna linia scenariusza. Pominięty scenariusz też jest linią – z powodem."""
    dec = inst.price_decimals
    p = lambda x: fmt_price(x, dec)
    head = f"{no}. {label}"
    base = {"kind": kind, "setup": setup, "side": side, "zone_id": zone["id"] if zone else None, "ok": False}

    def skip(reason):
        return {**base, "kind": "skipped", "text": f"{head} ({SIDE_WORD[side]}) pominięty: {reason}."}

    if zone is None:
        return skip(missing)
    if atr_ltf is None:
        return skip("brak danych 5-minutowych")
    if cfg.USE_TREND_FILTER and sma_state in ("long", "short") and side != sma_state:
        return skip(f"filtr średnich SMA pozwala dziś tylko na {SIDE_WORD[sma_state]}")
    order = build_order(hypothetical_bar(setup, side, zone, atr_ltf), side, levels, atr_ltf, cfg, inst, fx)
    if order["tp"] is None:
        return skip("brak dalszej strefy na take profit")
    if order["rr"] < cfg.MIN_RR:
        return skip(f"R:R {fmt_price(order['rr'], 2)} poniżej minimum {fmt_price(cfg.MIN_RR, 1)} "
                    "– następna strefa jest za blisko")
    cond = condition_text(setup, side, zone["low"], zone["high"], cfg.LTF_INTERVAL, dec)
    verb = "kupna" if side == "long" else "sprzedaży"
    return {**base, "ok": True,
            "text": (f"{head}: Jeśli {cond}, to zlecenie stop {verb} po ok. {p(order['entry'])}, "
                     f"SL {p(order['sl'])}, TP {p(order['tp'])}, R:R {fmt_price(order['rr'], 2)}.")}


def scenarios(dir_state: str, levels: list[dict], price: float, atr_ltf: float | None, sma_state: str,
              cfg: Config, inst: Instrument, fx: float) -> list[dict]:
    """Preferowany scenariusz pierwszy; przeciw trendowi tylko przy strefie D (grabienie płynności)."""
    above, below = nearest_zones(levels, price)
    args = (levels, atr_ltf, sma_state, cfg, inst, fx)
    if dir_state == "down":
        return [scenario(1, "Preferowany", "preferred", "A", "short", below, *args, missing="brak strefy pod ceną"),
                scenario(2, "Przeciw trendowi", "counter", "C", "long", _d_below(levels, price), *args,
                         missing="brak strefy D pod ceną")]
    if dir_state == "up":
        return [scenario(1, "Preferowany", "preferred", "A", "long", above, *args, missing="brak strefy nad ceną"),
                scenario(2, "Przeciw trendowi", "counter", "C", "short", _d_above(levels, price), *args,
                         missing="brak strefy D nad ceną")]
    return [scenario(1, "Bez preferencji", "neutral", "A", "long", above, *args, missing="brak strefy nad ceną"),
            scenario(2, "Bez preferencji", "neutral", "A", "short", below, *args, missing="brak strefy pod ceną")]


def _d_below(levels, price):
    return max((l for l in levels if l["low"] < price and l.get("daily")), key=lambda l: l["low"], default=None)


def _d_above(levels, price):
    return min((l for l in levels if l["high"] > price and l.get("daily")), key=lambda l: l["high"], default=None)


# ---------------------------------------------------------------- d) otwarcie, e) zasady

def opening_text(day_bars: pd.DataFrame, pdc: float, gap: float, zone: dict | None, dec: int) -> str:
    """Luka (otwarcie dalej niż 0,5 × ATR dzienny od PDC) i pierwsze 3 świece względem strefy."""
    p = lambda x: fmt_price(x, dec)
    ztext = f"strefą {p(zone['low'])}–{p(zone['high'])}" if zone else None
    if day_bars.empty:   # sesja jeszcze się nie zaczęła
        text = (f"Na otwarciu: luka, jeśli pierwsza cena będzie poniżej {p(pdc - gap)} albo powyżej "
                f"{p(pdc + gap)} (PDC {p(pdc)}).")
        if ztext:
            text += f" Patrz, czy 2–3 pierwsze świece zamkną się nad, czy pod {ztext}."
        return text

    open_ = float(day_bars["open"].iloc[0])
    diff = open_ - pdc
    if abs(diff) > gap:
        text = (f"Otwarcie: luka {'w górę' if diff > 0 else 'w dół'} – {p(open_)}, "
                f"{p(abs(diff))} pkt {'nad' if diff > 0 else 'pod'} PDC {p(pdc)}.")
    else:
        text = f"Otwarcie bez luki: {p(open_)} (PDC {p(pdc)})."
    first = day_bars["close"].iloc[:3]
    if not ztext:
        return text
    if len(first) < 3:
        return text + " Pierwsze 3 świece jeszcze się nie zamknęły."
    above = int((first > zone["high"]).sum())
    below = int((first < zone["low"]).sum())
    span = f"{p(zone['low'])}–{p(zone['high'])}"
    if above == 3:
        where = f"nad strefą {span}"
    elif below == 3:
        where = f"pod strefą {span}"
    elif above == below == 0:
        where = f"w strefie {span}"
    else:
        where = f"po obu stronach strefy {span}"
    return text + f" Pierwsze 3 świece zamknęły się {where}."


def plural_trades(n: int) -> str:
    if n == 1:
        return "1 transakcja"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} transakcje"
    return f"{n} transakcji"


def rules_text(start, end, inst: Instrument, cfg: Config) -> str:
    last = "po ostatniej koniec dnia" if cfg.MAX_TRADES != 2 else "po drugiej koniec dnia"
    return (f"Zasady: szukamy wejść {window_text(start, end, inst, cfg)}; "
            f"maks. {plural_trades(cfg.MAX_TRADES)}, {last}.")


# ---------------------------------------------------------------- podsumowanie po sesji

def summary_text(day, plan_lines: list[dict], signals: list[dict], trades: list[dict], dec: int) -> str:
    """Który scenariusz z planu tego dnia się zrealizował (sygnał z tą samą strefą, wzorcem i kierunkiem)."""
    date = f"{day:%d.%m}"
    real = [s for s in signals if s["status"] != "rejected"]
    for sc in (l for l in plan_lines if l.get("ok")):
        hit = next((s for s in real if s["level_id"] == sc["zone_id"] and s["setup"] == sc["setup"]
                    and s["side"] == sc["side"]), None)
        if hit is None:
            continue
        no = sc["text"].split(".")[0]
        trade = next((t for t in trades if t["signal_id"] == hit["id"]), None)
        if trade is None:
            return f"Podsumowanie {date}: scenariusz {no} dał sygnał, ale zlecenie nie zostało aktywowane."
        r = trade["result_r"]
        how = {"tp": "take profit", "sl": "stop loss", "eod": "koniec sesji"}[trade["exit_reason"]]
        return (f"Podsumowanie {date}: zrealizował się scenariusz {no} ({SIDE_WORD[sc['side']]}) – "
                f"wynik {'+' if r > 0 else ''}{fmt_price(r, 2)} R ({how}).")
    extra = (f" Inne transakcje: {len(trades)}, razem {fmt_price(sum(t['result_r'] for t in trades), 2)} R."
             if trades else "")
    return f"Podsumowanie {date}: żaden scenariusz się nie zrealizował.{extra}"


# ---------------------------------------------------------------- całość

def build_plan(md, cfg: Config, sim: dict, now: pd.Timestamp) -> dict:
    """Plan na dziś (przed i w trakcie sesji) albo podsumowanie + plan na następną sesję."""
    inst = md.instrument
    dec = inst.price_decimals
    tz = day_tz(inst)
    local = now.tz_convert(tz)
    today = local.normalize()

    # Koniec „dnia handlowego”: zamknięcie sesji (rynki z sesją) albo koniec okienka (całodobowe).
    w_start, w_end = window_bounds(local, inst, cfg)
    if inst.is_24h:
        day_end = w_end
    else:
        day_end = pd.Timestamp(datetime.combine(local.date(), inst.close_time), tz=inst.tz)
    trading_today = inst.trades_on(local.weekday())

    if trading_today and now < day_end:
        target = today
        title = f"Plan na dziś ({WEEKDAY_NAMES[target.weekday()]} {target:%d.%m})"
    else:
        target = today + timedelta(days=1)
        while not inst.trades_on(target.weekday()):
            target += timedelta(days=1)
        when = "jutro" if (target - today).days == 1 else WEEKDAY_ACC[target.weekday()]
        title = f"Plan na {when} ({target:%d.%m})"

    ltf = md.ltf
    lines = _plan_lines(md, cfg, sim, target, now)

    # Po okienku: jedna linia podsumowania ostatniej sesji z danymi.
    summary = None
    if not ltf.empty:
        last_day = ltf.index[-1].tz_convert(tz).normalize()
        window_over = last_day < today or now >= w_end
        if window_over and inst.trades_on(last_day.weekday()):
            day_plan = _plan_lines(md, cfg, sim, last_day, now, scenarios_only=True)
            day_signals = [s for s in sim["signals"] if s["ts"].tz_convert(tz).normalize() == last_day]
            day_trades = [t for t in sim["trades"] if t["entry_ts"].tz_convert(tz).normalize() == last_day]
            summary = summary_text(last_day, day_plan, day_signals, day_trades, dec)
    if summary:
        lines.append({"kind": "summary", "text": summary})
    return {"title": title, "target_day": target.isoformat(), "lines": lines[:8]}


def _plan_lines(md, cfg: Config, sim: dict, target: pd.Timestamp, now: pd.Timestamp,
                scenarios_only: bool = False) -> list[dict]:
    """Linie planu na dzień `target` – tylko z danych dostępnych przed tym dniem (+ dziś do teraz)."""
    inst = md.instrument
    dec = inst.price_decimals
    tz = day_tz(inst)
    daily_before = md.daily[md.daily.index < target]
    levels = key_levels(md.daily, md.hourly, cfg, asof=target)
    ltf_before = md.ltf[md.ltf.index < target] if not md.ltf.empty else md.ltf
    day_bars = md.ltf[(md.ltf.index >= target) & (md.ltf.index < min(now, target + timedelta(days=1)))] \
        if not md.ltf.empty else md.ltf
    ref = ltf_before if not ltf_before.empty else daily_before
    if ref.empty or len(daily_before) < 2:
        return [{"kind": "rules", "text": "Za mało danych, żeby przygotować plan."}]
    price = float(ref["close"].iloc[-1])
    atr_ltf = float(atr(ltf_before, cfg.ATR_PERIOD).iloc[-1]) if len(ltf_before) else None
    sma_state = trend_state(daily_before, cfg)["state"]
    m30 = _resample_30m(ltf_before, tz)
    dir_ = direction(daily_before, m30, cfg.SWING_N, dec)
    scen = scenarios(dir_["state"], levels, price, atr_ltf, sma_state, cfg, inst, md.fx_pln)
    if scenarios_only:
        return scen

    above, below = nearest_zones(levels, price)
    zones = zones_text(levels, price, dec)
    pdc = float(daily_before["close"].iloc[-1])
    gap = 0.5 * float(atr(daily_before, cfg.ATR_PERIOD).iloc[-1])
    w_start, w_end = window_bounds(target, inst, cfg)
    first_bars = day_bars[day_bars.index >= w_start] if not day_bars.empty else day_bars
    nearest = min((z for z in (above, below) if z), key=lambda z: min(abs(z["low"] - price), abs(z["high"] - price)),
                  default=None)
    return [
        {"kind": "direction", "text": dir_["text"]},
        {"kind": "zones", "text": zones},
        *scen,
        {"kind": "opening", "text": opening_text(first_bars, pdc, gap, nearest, dec)},
        {"kind": "rules", "text": rules_text(w_start, w_end, inst, cfg)},
    ]


def _resample_30m(ltf: pd.DataFrame, tz: str) -> pd.DataFrame:
    if ltf.empty:
        return ltf
    local = ltf.tz_convert(tz)
    return local.resample("30min").agg({"open": "first", "high": "max", "low": "min",
                                        "close": "last"}).dropna()
