"""Filtr trendu, wzorce A/B/C na niższej ramie i budowa zleceń.

Każda funkcja dostaje tylko dane dostępne w danym momencie (do świecy i włącznie),
dzięki czemu backtest nie zagląda w przyszłość.
"""

from __future__ import annotations

from datetime import datetime, time as dtime, timedelta

import pandas as pd

from .config import Config
from .instruments import Instrument

PL_TZ = "Europe/Warsaw"


# ---------------------------------------------------------------- trend

def trend_state(daily_before: pd.DataFrame, cfg: Config) -> dict:
    """Stan filtra trendu z dziennych świec sprzed analizowanego dnia."""
    closes = daily_before["close"]
    if len(closes) < cfg.SMA_SLOW:
        return {"sma15": None, "sma45": None, "state": "mixed", "enough_data": False}
    sma_fast = float(closes.tail(cfg.SMA_FAST).mean())
    sma_slow = float(closes.tail(cfg.SMA_SLOW).mean())
    close = float(closes.iloc[-1])
    if close > sma_fast and close > sma_slow:
        state = "long"
    elif close < sma_fast and close < sma_slow:
        state = "short"
    else:
        state = "mixed"
    return {"sma15": sma_fast, "sma45": sma_slow, "state": state, "enough_data": True}


def trend_text(state: str, cfg: Config) -> str:
    fast, slow = cfg.SMA_FAST, cfg.SMA_SLOW   # okresy mogą być zmienione w panelu parametrów
    return {
        "long": f"Nad SMA {fast} i {slow} – tylko long",
        "short": f"Pod SMA {fast} i {slow} – tylko short",
        "mixed": "Między średnimi – oba kierunki, sygnały słabsze",
    }[state]


# ---------------------------------------------------------------- okienko

def day_tz(inst: Instrument) -> str:
    """Strefa, w której liczymy „dzień handlowy” i okienko.

    Rynek z sesją: strefa giełdy (okienko od otwarcia sesji).
    Rynek całodobowy: czas polski, bo okienko ustawia się ręcznie po polsku.
    """
    return PL_TZ if inst.is_24h else inst.tz


def window_bounds(day, inst: Instrument, cfg: Config) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Początek i koniec okienka handlu w dniu `day` (data w strefie day_tz).

    Zwraca znaczniki czasu ze strefą, więc można je wyświetlić w dowolnej strefie.
    """
    date = day.date() if hasattr(day, "date") else day
    if cfg.WINDOW_MODE == "session":
        return session_bounds(date, inst)
    if cfg.WINDOW_MODE == "custom" or inst.is_24h:   # rynek całodobowy nie ma „otwarcia”
        h1, m1 = map(int, cfg.WINDOW_START.split(":"))
        h2, m2 = map(int, cfg.WINDOW_END.split(":"))
        start = pd.Timestamp(datetime.combine(date, dtime(h1, m1)), tz=PL_TZ)
        return start, pd.Timestamp(datetime.combine(date, dtime(h2, m2)), tz=PL_TZ)
    start = pd.Timestamp(datetime.combine(date, inst.open_time), tz=inst.tz)
    return start, start + timedelta(minutes=cfg.TRADING_WINDOW)


def session_bounds(date, inst: Instrument) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Otwarcie i zamknięcie sesji w danym dniu. Rynek całodobowy: cała doba (czas polski).

    Uwaga: nie znamy świąt giełdowych – w święto aplikacja uzna giełdę za otwartą,
    ale nie będzie świec, więc nie będzie też sygnałów.
    """
    if inst.is_24h:
        start = pd.Timestamp(datetime.combine(date, dtime(0, 0)), tz=PL_TZ)
        return start, start + timedelta(days=1)
    return (pd.Timestamp(datetime.combine(date, inst.open_time), tz=inst.tz),
            pd.Timestamp(datetime.combine(date, inst.close_time), tz=inst.tz))


def market_status(now: pd.Timestamp, inst: Instrument) -> dict:
    """Czy giełda jest teraz otwarta, do której (gdy otwarta) i kiedy następne otwarcie (gdy zamknięta)."""
    tz = day_tz(inst)
    local = now.tz_convert(tz)
    if inst.trades_on(local.weekday()):
        start, end = session_bounds(local.date(), inst)
        if start <= now < end:
            return {"open": True, "opens": start, "closes": end}
    day = local.normalize()
    for _ in range(8):   # najbliższy dzień handlowy, którego sesja jeszcze się nie zaczęła
        if inst.trades_on(day.weekday()):
            start, end = session_bounds(day.date(), inst)
            if start > now:
                return {"open": False, "opens": start, "closes": end}
        day += timedelta(days=1)
    return {"open": False, "opens": None, "closes": None}


CITY = {"Europe/Berlin": "Frankfurt", "America/New_York": "Nowy Jork", "Europe/London": "Londyn",
        "Europe/Warsaw": "Warszawa", "UTC": "UTC", "America/Chicago": "Chicago", "Asia/Tokyo": "Tokio"}


def _hm(ts: pd.Timestamp) -> str:
    return f"{ts.hour}:{ts.minute:02d}"


def window_label(start: pd.Timestamp, end: pd.Timestamp, inst: Instrument) -> str:
    """„15:30–17:00 czasu polskiego (9:30–11:00 Nowy Jork)” – zawsze najpierw czas polski."""
    s_pl, e_pl = start.tz_convert(PL_TZ), end.tz_convert(PL_TZ)
    label = f"{_hm(s_pl)}–{_hm(e_pl)} czasu polskiego"
    if inst.tz == PL_TZ:
        return label + " (= czas giełdy)"
    s_ex, e_ex = start.tz_convert(inst.tz), end.tz_convert(inst.tz)
    return label + f" ({_hm(s_ex)}–{_hm(e_ex)} {CITY.get(inst.tz, inst.tz)})"


def window_text(start, end, inst: Instrument, cfg: Config) -> str:
    """Opis okienka do zdania „Szukamy wejść …”:
    „przez całą sesję, 15:30–22:00 czasu polskiego (…)”, „przez całą dobę, pn–pt”,
    „w godzinach 15:30–17:00 czasu polskiego (…)”."""
    if cfg.WINDOW_MODE == "session" and inst.is_24h:
        return "przez całą dobę, " + ("7 dni w tygodniu" if inst.session == "24x7" else "pn–pt")
    label = window_label(start, end, inst)
    return f"przez całą sesję, {label}" if cfg.WINDOW_MODE == "session" else f"w godzinach {label}"


def in_window(ts: pd.Timestamp, inst: Instrument, cfg: Config) -> bool:
    """Czy świeca zaczyna się w okienku handlu (w dniu, w którym rynek działa)."""
    local = ts.tz_convert(day_tz(inst))
    if not inst.trades_on(local.weekday()):
        return False
    start, end = window_bounds(local, inst, cfg)
    return start <= ts < end


# ---------------------------------------------------------------- wzorce

def _breakout_up(prev_close: float, close: float, lv: dict) -> bool:
    """Wybicie w górę: zamknięcie NAD strefą, poprzednie zamknięcie w strefie lub pod nią."""
    return close > lv["high"] and prev_close <= lv["high"]


def _breakout_down(prev_close: float, close: float, lv: dict) -> bool:
    return close < lv["low"] and prev_close >= lv["low"]


def as_arrays(bars: pd.DataFrame) -> dict:
    """Kolumny świec jako tablice numpy – szybki dostęp świeca po świecy w symulacji."""
    return {k: bars[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close")}


def detect_patterns(bars, i: int, levels: list[dict], tol_ltf: float, lookback: int) -> list[dict]:
    """Zwraca wzorce, które świeca `i` tworzy z którymkolwiek poziomem.

    `bars`: DataFrame ze świecami albo gotowy wynik as_arrays() (szybciej w pętli).
    Dla jednej świecy i jednego poziomu w danym kierunku zwracamy najwyżej jeden
    wzorzec (pierwszeństwo: A, potem B, potem C).
    """
    if i < 1:
        return []
    arr = bars if isinstance(bars, dict) else as_arrays(bars)
    h, l, c = arr["high"][i], arr["low"][i], arr["close"][i]
    closes = arr["close"]
    prev_close = closes[i - 1]
    found = []

    for lv in levels:
        # ----- long
        setup = None
        if _breakout_up(prev_close, c, lv):
            setup = "A"
        elif l <= lv["high"] + tol_ltf and c > lv["high"] and _recent_breakout(closes, i, lv, lookback, "up"):
            setup = "B"
        elif l < lv["low"] and c > lv["low"] and prev_close >= lv["low"]:
            setup = "C"
        if setup:
            found.append({"setup": setup, "side": "long", "level": lv})

        # ----- short (lustrzane odbicie)
        setup = None
        if _breakout_down(prev_close, c, lv):
            setup = "A"
        elif h >= lv["low"] - tol_ltf and c < lv["low"] and _recent_breakout(closes, i, lv, lookback, "down"):
            setup = "B"
        elif h > lv["high"] and c < lv["high"] and prev_close <= lv["high"]:
            setup = "C"
        if setup:
            found.append({"setup": setup, "side": "short", "level": lv})

    return _best_per_side(found)


def _recent_breakout(closes, i: int, lv: dict, lookback: int, direction: str) -> bool:
    """Czy w ostatnich `lookback` świecach (bez bieżącej) było wybicie przez strefę."""
    for j in range(max(1, i - lookback), i):
        if direction == "up" and _breakout_up(closes[j - 1], closes[j], lv):
            return True
        if direction == "down" and _breakout_down(closes[j - 1], closes[j], lv):
            return True
    return False


def _best_per_side(found: list[dict]) -> list[dict]:
    """Jeśli świeca tworzy wzorce z kilkoma poziomami, bierzemy najsilniejszy poziom."""
    best: dict[str, dict] = {}
    for f in found:
        cur = best.get(f["side"])
        if cur is None or f["level"]["touches"] > cur["level"]["touches"]:
            best[f["side"]] = f
    return list(best.values())


# ---------------------------------------------------------------- zlecenia

def build_order(bar: pd.Series, side: str, levels: list[dict], atr_ltf: float,
                cfg: Config, inst: Instrument, fx_pln: float = 1.0) -> dict:
    """Wejście, SL, TP, R:R i wielkość pozycji dla świecy sygnałowej.

    Ceny są zaokrąglane do siatki ticka instrumentu. Wielkość pozycji liczymy
    z wartości punktu instrumentu przeliczonej na złotówki (`fx_pln`).
    Zwraca słownik; gdy nie ma poziomu na TP, `tp` jest None.
    """
    tick = inst.tick
    dec = inst.price_decimals
    on_tick = lambda p: round(round(p / tick) * tick, dec)
    if side == "long":
        entry = float(bar["high"]) + tick
        sl = float(bar["low"]) - tick
        above = [lv for lv in levels if lv["low"] > entry]
        target = min(above, key=lambda lv: lv["low"]) if above else None
        tp = target["low"] - cfg.TP_OFFSET_ATR * atr_ltf if target else None
    else:
        entry = float(bar["low"]) - tick
        sl = float(bar["high"]) + tick
        below = [lv for lv in levels if lv["high"] < entry]
        target = max(below, key=lambda lv: lv["high"]) if below else None
        tp = target["high"] + cfg.TP_OFFSET_ATR * atr_ltf if target else None

    entry, sl = on_tick(entry), on_tick(sl)
    tp = on_tick(tp) if tp is not None else None
    risk = abs(entry - sl)
    reward = abs(tp - entry) if tp is not None else 0.0
    # TP po złej stronie wejścia (strefa tuż przy cenie) traktujemy jak brak zysku.
    if tp is not None and ((side == "long" and tp <= entry) or (side == "short" and tp >= entry)):
        reward = 0.0
    rr = reward / risk if risk > 0 else 0.0
    point_pln = inst.point_value * fx_pln
    size = (cfg.KAPITAL * cfg.RYZYKO_PROC / 100) / (risk * point_pln) if risk > 0 else 0.0
    return {"entry": entry, "sl": sl, "tp": tp,
            "rr": round(rr, 2), "size": round(size, 2), "risk_pts": round(risk, dec + 1),
            "risk_pln": round(risk * size * point_pln, 2),
            "reward_pln": round(reward * size * point_pln, 2),   # możliwy zysk, gdy cena dojdzie do TP
            "decimals": dec,
            "target_level_id": target["id"] if target else None}


def reject_reason(order: dict, side: str, trend: str, atr_ltf: float,
                  trades_today: int, cfg: Config) -> str | None:
    """Powód odrzucenia sygnału albo None, gdy sygnał przechodzi wszystkie filtry."""
    if cfg.USE_TREND_FILTER and trend in ("long", "short") and side != trend:
        return f"Kierunek sprzeczny z filtrem trendu (dziś tylko {trend})"
    if trades_today >= cfg.MAX_TRADES:
        return f"Limit {cfg.MAX_TRADES} transakcji dziennie już wykorzystany"
    if order["tp"] is None:
        return "Brak poziomu HTF w kierunku transakcji na take profit"
    if order["risk_pts"] > cfg.MAX_RISK_ATR * atr_ltf:
        d = order.get("decimals", 1)
        return (f"Ryzyko {_pl(order['risk_pts'], d)} pkt większe niż "
                f"{_pl(cfg.MAX_RISK_ATR, 1)} × ATR LTF ({_pl(cfg.MAX_RISK_ATR * atr_ltf, d)} pkt)")
    if order["rr"] < cfg.MIN_RR:
        return f"R:R {_pl(order['rr'], 2)} poniżej minimum {_pl(cfg.MIN_RR, 1)}"
    return None


def _pl(x: float, digits: int) -> str:
    """Liczba z przecinkiem dziesiętnym, jak w polskim zapisie."""
    return f"{x:.{digits}f}".replace(".", ",")
