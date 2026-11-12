"""Rejestr instrumentów: wbudowane wpisy + własne instrumenty użytkownika.

Tick i wartość punktu są ustawione dla CFD o rozmiarze 1 (1 punkt ruchu ceny
= `point_value` w walucie instrumentu). To wartości orientacyjne: UI oznacza je
jako „do sprawdzenia u Twojego brokera”.
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass, asdict, fields, replace
from datetime import time as dtime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

USER_PATH = Path(__file__).resolve().parent.parent / "data" / "instruments_user.json"

GROUPS = ["Indeksy", "Waluty", "Surowce", "Krypto", "Akcje", "Własne"]

# Rodzaje sesji: godziny („09:00-17:30”), całodobowo w dni robocze, całodobowo zawsze.
SESSION_24X5 = "24x5"
SESSION_24X7 = "24x7"
_SESSION_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$")


# Godziny sesji ciągłej popularnych giełd (czas lokalny giełdy) – podpowiedź w formularzu
# własnego instrumentu. Klucz: strefa czasowa, którą zwraca Yahoo.
EXCHANGE_HOURS = {
    "America/New_York": "09:30-16:00",    # NYSE, Nasdaq
    "America/Chicago": "08:30-15:00",
    "Europe/Warsaw": "09:00-17:00",       # GPW
    "Europe/London": "08:00-16:30",       # LSE
    "Europe/Berlin": "09:00-17:30",       # Xetra
    "Europe/Paris": "09:00-17:30",
    "Europe/Amsterdam": "09:00-17:30",
    "Europe/Zurich": "09:00-17:30",
    "Asia/Tokyo": "09:00-15:00",
}


class InstrumentError(ValueError):
    """Błąd rejestru (nieznany instrument, złe pole) z komunikatem po polsku."""


@dataclass(frozen=True)
class Instrument:
    id: str               # stały identyfikator używany przez API i UI
    name: str             # nazwa wyświetlana
    symbol: str           # symbol w Yahoo Finance
    group: str
    tz: str               # strefa czasowa giełdy
    session: str          # "HH:MM-HH:MM", "24x5" albo "24x7"
    tick: float           # najmniejszy krok ceny
    point_value: float    # wartość 1 punktu dla CFD o rozmiarze 1, w walucie instrumentu
    currency: str         # waluta instrumentu (PLN, EUR, USD…)
    stooq_symbol: str = ""   # zapasowe źródło danych dziennych (stooq.pl)
    custom: bool = False

    @property
    def is_24h(self) -> bool:
        return self.session in (SESSION_24X5, SESSION_24X7)

    @property
    def open_time(self) -> dtime | None:
        return None if self.is_24h else _parse_hm(self.session.split("-")[0])

    @property
    def close_time(self) -> dtime | None:
        return None if self.is_24h else _parse_hm(self.session.split("-")[1])

    @property
    def price_decimals(self) -> int:
        """Ile miejsc po przecinku ma cena (z wielkości ticka: 0,1 -> 1, 0,00001 -> 5)."""
        return max(0, -Decimal(str(self.tick)).normalize().as_tuple().exponent)

    def trades_on(self, weekday: int) -> bool:
        """Czy rynek działa w dany dzień tygodnia (0 = poniedziałek)."""
        return self.session == SESSION_24X7 or weekday < 5

    def to_dict(self) -> dict:
        d = asdict(self)
        d["is_24h"] = self.is_24h
        d["price_decimals"] = self.price_decimals
        return d


BUILTIN: list[Instrument] = [
    Instrument("dax", "DAX", "^GDAXI", "Indeksy", "Europe/Berlin", "09:00-17:30", 0.1, 1.0, "EUR"),
    Instrument("sp500", "S&P 500", "^GSPC", "Indeksy", "America/New_York", "09:30-16:00", 0.1, 1.0, "USD"),
    Instrument("nasdaq100", "Nasdaq 100", "^NDX", "Indeksy", "America/New_York", "09:30-16:00", 0.1, 1.0, "USD"),
    # Yahoo ma WIG20.WA (intraday), ale prawie bez świec dziennych: wtedy stooq,
    # a gdy i on zawiedzie, świece dzienne składamy z godzinowych (data.py).
    Instrument("wig20", "WIG20", "WIG20.WA", "Indeksy", "Europe/Warsaw", "09:00-17:00", 0.1, 1.0, "PLN",
               stooq_symbol="wig20"),
    Instrument("eurusd", "EUR/USD", "EURUSD=X", "Waluty", "Europe/London", SESSION_24X5, 0.00001, 1.0, "USD"),
    Instrument("gold", "Złoto", "GC=F", "Surowce", "America/New_York", SESSION_24X5, 0.1, 1.0, "USD"),
    Instrument("btc", "Bitcoin", "BTC-USD", "Krypto", "UTC", SESSION_24X7, 0.01, 1.0, "USD"),
]


# ---------------------------------------------------------------- walidacja

def _parse_hm(s: str) -> dtime:
    h, m = map(int, s.split(":"))
    return dtime(h, m)


def validate_session(session: str) -> str:
    session = session.strip().replace("–", "-").replace(" ", "")
    if session in (SESSION_24X5, SESSION_24X7):
        return session
    if not _SESSION_RE.match(session):
        raise InstrumentError("Sesja musi mieć postać GG:MM-GG:MM (np. 09:00-17:30) albo 24x5 / 24x7.")
    if _parse_hm(session.split("-")[0]) >= _parse_hm(session.split("-")[1]):
        raise InstrumentError("Godzina otwarcia sesji musi być wcześniejsza niż zamknięcia.")
    return session


def validate_tz(tz: str) -> str:
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise InstrumentError(f"Nieznana strefa czasowa „{tz}” (przykład: Europe/Warsaw).") from None
    return tz


def make_custom(data: dict) -> Instrument:
    """Buduje własny instrument z danych formularza i sprawdza każde pole."""
    symbol = str(data.get("symbol", "")).strip().upper()
    if not symbol or len(symbol) > 32 or not re.match(r"^[A-Z0-9.^=\-]+$", symbol):
        raise InstrumentError("Podaj symbol Yahoo, np. PKN.WA albo AAPL.")
    try:
        tick = float(data.get("tick", 0))
        point_value = float(data.get("point_value", 0))
    except (TypeError, ValueError):
        raise InstrumentError("Tick i wartość punktu muszą być liczbami.") from None
    if tick <= 0 or point_value <= 0:
        raise InstrumentError("Tick i wartość punktu muszą być większe od zera.")
    group = data.get("group") or "Własne"
    if group not in GROUPS:
        raise InstrumentError(f"Grupa musi być jedną z: {', '.join(GROUPS)}.")
    currency = str(data.get("currency", "")).strip().upper()
    if not re.match(r"^[A-Z]{3}$", currency):
        raise InstrumentError("Waluta to trzy litery, np. PLN, USD, EUR.")
    slug = re.sub(r"[^a-z0-9]+", "-", symbol.lower()).strip("-")
    return Instrument(
        id=f"u-{slug}", name=str(data.get("name") or symbol).strip()[:40], symbol=symbol, group=group,
        tz=validate_tz(str(data.get("tz", "")).strip()), session=validate_session(str(data.get("session", ""))),
        tick=tick, point_value=point_value, currency=currency, custom=True,
    )


# ---------------------------------------------------------------- rejestr

_LOCK = threading.Lock()   # FastAPI obsługuje zapytania w wielu wątkach naraz
_FIELDS = {f.name for f in fields(Instrument)}


def _read_raw() -> list[dict]:
    """Surowa zawartość pliku. Uszkodzony plik to błąd, a nie pusta lista:
    inaczej następny zapis nadpisałby (usunął) wszystkie zapisane instrumenty."""
    if not USER_PATH.exists():
        return []
    try:
        raw = json.loads(USER_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InstrumentError(f"Plik {USER_PATH.name} jest uszkodzony ({exc}). Popraw go albo usuń.") from None
    if not isinstance(raw, list):
        raise InstrumentError(f"Plik {USER_PATH.name} ma zły format (oczekiwana lista).")
    return raw


def load_user() -> list[Instrument]:
    """Własne instrumenty. Wpis z nieznanymi polami pomijamy, ale zostaje w pliku."""
    out = []
    for item in _read_raw():
        known = {k: v for k, v in item.items() if k in _FIELDS}
        known["custom"] = True
        try:
            out.append(Instrument(**known))
        except TypeError:   # brakuje wymaganego pola
            continue
    return out


def save_user(inst: Instrument) -> None:
    """Dodaje (albo podmienia o tym samym id) własny instrument w pliku JSON.

    Zachowuje wpisy, których nie umiemy wczytać, i zapisuje atomowo (plik tymczasowy
    + podmiana), żeby przerwany zapis nie zostawił uciętego pliku.
    """
    with _LOCK:
        raw = [item for item in _read_raw() if item.get("id") != inst.id] + [asdict(inst)]
        atomic_write_json(USER_PATH, raw)


def atomic_write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def all_instruments() -> list[Instrument]:
    return BUILTIN + load_user()


def get_instrument(instrument_id: str) -> Instrument:
    for inst in all_instruments():
        if inst.id == instrument_id:
            return inst
    raise InstrumentError(f"Nieznany instrument „{instrument_id}”. Wybierz jeden z listy.")


def with_overrides(inst: Instrument, tick: float | None, point_value: float | None) -> Instrument:
    """Instrument z tickiem i wartością punktu zmienionymi w panelu parametrów."""
    return replace(inst, tick=tick or inst.tick, point_value=point_value or inst.point_value)
