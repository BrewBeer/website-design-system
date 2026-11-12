"""Jedna decyzja dla użytkownika: KUP, SPRZEDAJ, PLAN, CZEKAJ, POZA OKIENKIEM albo GIEŁDA ZAMKNIĘTA.

Najważniejsza zasada: na danych opóźnionych o więcej niż jedną świecę LTF nigdy
nie mówimy „KUP”/„SPRZEDAJ”. Wtedy pokazujemy „PLAN” – warunek do sprawdzenia
na wykresie brokera w czasie rzeczywistym, z gotowym zleceniem.
"""

from __future__ import annotations

import pandas as pd

from .backtest import fmt_price
from .config import Config
from .instruments import Instrument

PL_TZ = "Europe/Warsaw"

INTERVAL_MINUTES = {"5m": 5, "15m": 15, "1h": 60, "1d": 1440}

HEADLINES = {"buy": "KUP", "sell": "SPRZEDAJ", "plan": "PLAN", "wait": "CZEKAJ",
             "out": "POZA OKIENKIEM", "closed": "GIEŁDA ZAMKNIĘTA"}

BROKER_HINT = ("W Interactive Brokers użyj zlecenia typu bracket (wejście + SL + TP naraz). "
               "W bossie ustaw zlecenie oczekujące stop z dołączonym SL i TP. "
               "Sprawdź kroki w pomocy brokera i przećwicz je na koncie demo.")


# ---------------------------------------------------------------- świeżość danych

def data_age(last_bar_start: pd.Timestamp | None, interval: str, now: pd.Timestamp) -> dict:
    """Jak stare są dane. Świeca o starcie 10:30 (5m) kończy się 10:35 – to jest „dane z 10:35”.

    `stale` = opóźnienie większe niż jedna świeca LTF.
    """
    if last_bar_start is None:
        return {"bar_end": None, "minutes": None, "stale": True, "text": "Brak danych intraday."}
    step = INTERVAL_MINUTES.get(interval, 5)
    bar_end = last_bar_start + pd.Timedelta(minutes=step)
    minutes = max(0, int(round((now - bar_end).total_seconds() / 60)))
    local = bar_end.tz_convert(PL_TZ)
    day = "" if local.date() == now.tz_convert(PL_TZ).date() else f"{local:%d.%m} "
    return {
        "bar_end": bar_end.isoformat(),
        "minutes": minutes,
        "stale": minutes > step,
        "text": f"Dane z {day}{local.hour}:{local.minute:02d} (opóźnienie ok. {_age_words(minutes)})",
    }


def _age_words(minutes: int) -> str:
    if minutes < 120:
        return f"{minutes} min"
    if minutes < 48 * 60:
        return f"{minutes // 60} godz."
    return f"{minutes // 1440} dni"


# ---------------------------------------------------------------- warunki wejścia słowami

def condition_text(setup: str, side: str, low: float, high: float, ltf: str, dec: int) -> str:
    """Warunek wzorca A/B/C opisany zwykłym zdaniem (bez „jeśli”)."""
    p = lambda x: fmt_price(x, dec)
    if side == "long":
        return {
            "A": f"świeca {ltf} zamknie się nad {p(high)}",
            "B": f"cena cofnie się do {p(high)}, a świeca {ltf} zamknie się nad tym poziomem",
            "C": f"knot zejdzie pod {p(low)}, a świeca {ltf} zamknie się nad {p(low)}",
        }[setup]
    return {
        "A": f"świeca {ltf} zamknie się pod {p(low)}",
        "B": f"cena wróci do {p(low)}, a świeca {ltf} zamknie się pod tym poziomem",
        "C": f"knot wyjdzie nad {p(high)}, a świeca {ltf} zamknie się pod {p(high)}",
    }[setup]


def order_card(signal: dict, inst: Instrument, ltf: str) -> dict:
    """Wszystko, co trzeba przepisać do platformy brokera."""
    long = signal["side"] == "long"
    dec = inst.price_decimals
    return {
        "side": signal["side"],
        "type": "Stop kupna" if long else "Stop sprzedaży",
        "type_help": ("kupno, gdy cena dojdzie do ceny aktywacji" if long
                      else "sprzedaż, gdy cena spadnie do ceny aktywacji"),
        "activation": signal["entry"], "sl": signal["sl"], "tp": signal["tp"],
        "size": signal["size"], "risk_pln": signal["risk_pln"], "reward_pln": signal["reward_pln"],
        "rr": signal["rr"],
        "validity": "Do końca dnia",
        "decimals": dec,
        "condition": condition_text(signal["setup"], signal["side"], signal["level_low"],
                                    signal["level_high"], ltf, dec),
        "broker_hint": BROKER_HINT,
    }


# ---------------------------------------------------------------- decyzja

def decide(*, active_signal: dict | None, window: dict, freshness: dict, levels: list[dict],
           last_price: float, trend: str, intraday: bool, inst: Instrument, cfg: Config) -> dict:
    """Zwraca jeden stan dla paska na górze ekranu.

    Kolejność: aktywny sygnał (KUP/SPRZEDAJ albo PLAN, gdy dane opóźnione)
    → poza okienkiem → czekaj z jednym zdaniem, na co czekamy.
    """
    ltf = cfg.LTF_INTERVAL
    dec = inst.price_decimals
    p = lambda x: fmt_price(x, dec)

    if not intraday:
        return _state("wait", "Brak danych 5-minutowych dla tego instrumentu – sygnały są niedostępne. "
                              "Widać tylko poziomy z wykresu dziennego.", freshness)

    if active_signal is not None and active_signal.get("tp") is not None:
        card = order_card(active_signal, inst, ltf)
        side = active_signal["side"]
        if freshness["stale"]:
            verb = "zlecenie stop kupna" if side == "long" else "zlecenie stop sprzedaży"
            sentence = (f"Jeśli na Twoim wykresie {card['condition']}: {verb} {p(card['activation'])}, "
                        f"SL {p(card['sl'])}, TP {p(card['tp'])}.")
            return _state("plan", sentence, freshness, side=side, order=card, signal_id=active_signal["id"])
        state = "buy" if side == "long" else "sell"
        sentence = ("Sygnał aktywny. Ustaw zlecenie stop – wejdzie, gdy cena dojdzie do ceny aktywacji."
                    if active_signal["status"] == "pending" else "Pozycja otwarta. Pilnuj SL i TP.")
        return _state(state, sentence, freshness, side=side, order=card, signal_id=active_signal["id"])

    if not window["in_window"]:
        # Giełda zamknięta to co innego niż „giełda działa, ale to nie nasze okienko”.
        return _state("out" if window.get("market_open") else "closed", window["text"], freshness)

    return _state("wait", wait_sentence(levels, last_price, trend, cfg, dec), freshness)


def wait_sentence(levels: list[dict], price: float, trend: str, cfg: Config, dec: int) -> str:
    """Jedno zdanie: najbliższy warunek, który dałby sygnał w dozwolonym kierunku."""
    p = lambda x: fmt_price(x, dec)
    ltf = cfg.LTF_INTERVAL
    allow_long = not cfg.USE_TREND_FILTER or trend != "short"
    allow_short = not cfg.USE_TREND_FILTER or trend != "long"
    above = min((l for l in levels if l["high"] > price), key=lambda l: l["high"], default=None)
    below = max((l for l in levels if l["low"] < price), key=lambda l: l["low"], default=None)

    options = []
    if allow_long and above:
        options.append((above["high"] - price, f"Kup, jeśli świeca {ltf} zamknie się nad {p(above['high'])}."))
    if allow_short and below:
        options.append((price - below["low"], f"Sprzedaj, jeśli świeca {ltf} zamknie się pod {p(below['low'])}."))
    if not options:
        return "Brak kluczowych poziomów w kierunku trendu – dziś nie ma na co czekać."
    return min(options)[1]


def _state(state: str, sentence: str, freshness: dict, *, side: str | None = None,
           order: dict | None = None, signal_id: str | None = None) -> dict:
    return {"state": state, "headline": HEADLINES[state], "sentence": sentence, "side": side,
            "order": order, "signal_id": signal_id, "freshness": freshness}
