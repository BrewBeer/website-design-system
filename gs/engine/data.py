"""Pobieranie notowań (Yahoo przez yfinance, awaryjnie stooq.pl) z cache na dysku.

Zasada: nigdy nie wymyślamy danych. Jeśli czegoś nie da się pobrać,
rzucamy DataError z komunikatem po polsku, który UI pokaże użytkownikowi.
"""

from __future__ import annotations

import io
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import pandas as pd

from .config import Config
from .instruments import Instrument

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"
ARCHIVE_DIR = Path(__file__).resolve().parent.parent / "data" / "archive"
ARCHIVE_DAYS = 400       # ile dni świec LTF trzymamy w archiwum (rok + zapas)
MAX_LTF_DAYS = 59        # Yahoo: świece 5m/15m tylko z ok. 60 ostatnich dni

# Jak długo (w sekundach) plik w cache jest „świeży”.
CACHE_TTL = {"1d": 6 * 3600, "1h": 30 * 60, "15m": 5 * 60, "5m": 5 * 60}

# Ile danych pobieramy dla każdej ramy (ograniczenia Yahoo: 5m/15m ok. 60 dni).
PERIODS = {"1d": "1y", "1h": "60d"}

COLUMNS = ["open", "high", "low", "close", "volume"]

# Poniżej tylu świec dziennych dane uznajemy za niepełne (SMA 45 + zapas).
MIN_DAILY_BARS = 60

EMPTY_TTL = 10 * 60


class DataError(Exception):
    """Błąd danych z komunikatem gotowym do pokazania w UI."""


@dataclass
class MarketData:
    daily: pd.DataFrame
    hourly: pd.DataFrame
    ltf: pd.DataFrame
    instrument: Instrument
    fx_pln: float = 1.0           # ile zł kosztuje 1 jednostka waluty instrumentu
    delayed: bool = True
    warnings: list[str] = field(default_factory=list)
    source: str = "Yahoo"
    ltf_full: pd.DataFrame | None = None   # cała historia LTF z archiwum (do wyników 1M/3M/YTD/rok)


# ---------------------------------------------------------------- źródła danych

class DataSource(Protocol):
    """Interfejs źródła notowań. Silnik prosi tylko o świece; skąd pochodzą, to sprawa źródła.

    Każde źródło zwraca DataFrame z kolumnami open/high/low/close/volume
    i indeksem czasu UTC (początek świecy). Pusty DataFrame = brak danych.
    """

    name: str
    realtime: bool               # True = dane na żywo, False = opóźnione

    def candles(self, symbol: str, interval: str, period: str) -> pd.DataFrame:
        """interval: 5m, 15m, 1h, 1d; period: np. 30d, 60d, 1y."""
        ...


class YahooSource:
    """Darmowe, opóźnione dane z Yahoo Finance (z cache na dysku)."""

    name = "Yahoo"
    realtime = False

    def candles(self, symbol: str, interval: str, period: str) -> pd.DataFrame:
        return fetch("yahoo", symbol, interval, period, CACHE_TTL.get(interval, CACHE_TTL["1d"]))


class IBSource:
    """Miejsce na dane na żywo z Interactive Brokers (biblioteka ib_async). JESZCZE NIE ZROBIONE.

    Plan na później:
      - połączenie z TWS / IB Gateway: ib_async.IB().connect("127.0.0.1", 7497, clientId=...),
      - świece: ib.reqHistoricalData(kontrakt, durationStr=..., barSizeSetting="5 mins",
        whatToShow="TRADES", useRTH=False, keepUpToDate=True),
      - przeliczenie wyniku na DataFrame z indeksem UTC jak w YahooSource,
      - mapowanie symboli (np. ^GDAXI -> kontrakt indeksu DAX na Eurex).
    """

    name = "Interactive Brokers"
    realtime = True

    def candles(self, symbol: str, interval: str, period: str) -> pd.DataFrame:
        raise NotImplementedError("Źródło Interactive Brokers nie jest jeszcze podłączone.")


# ---------------------------------------------------------------- cache

def _cache_path(source: str, symbol: str, interval: str, period: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9]+", "_", symbol).strip("_")
    return CACHE_DIR / f"{source}_{safe}_{interval}_{period or 'max'}.csv"


def _read_cache(path: Path, ttl: int) -> pd.DataFrame | None:
    if not path.exists() or time.time() - path.stat().st_mtime > ttl:
        return None
    df = pd.read_csv(path, index_col=0)
    # Pusty wynik pamiętamy krócej (10 min): brak danych bywa chwilowy.
    if df.empty and time.time() - path.stat().st_mtime > EMPTY_TTL:
        return None
    df.index = pd.to_datetime(df.index, utc=True)
    return df.astype(float)


def _write_cache(path: Path, df: pd.DataFrame) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if df.empty:
        # Zapamiętujemy też „brak danych”, żeby nie pytać Yahoo przy każdym kliknięciu.
        pd.DataFrame(columns=COLUMNS).to_csv(path)
        return
    out = df.copy()
    out.index = out.index.tz_convert("UTC")
    out.to_csv(path)


# ---------------------------------------------------------------- źródła

def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Ujednolica kolumny (małe litery) i indeks (czas UTC), usuwa puste świece."""
    df = df.rename(columns=str.lower)
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = 0.0
    df = df[COLUMNS].astype(float).dropna(subset=["open", "high", "low", "close"])
    df["volume"] = df["volume"].fillna(0.0)
    idx = pd.to_datetime(df.index)
    df.index = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    return df[~df.index.duplicated(keep="last")].sort_index()


def _yahoo(symbol: str, interval: str, period: str) -> pd.DataFrame:
    import yfinance as yf  # import tutaj, żeby testy nie wymagały sieci

    try:
        df = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=False)
    except Exception as exc:  # yfinance rzuca różne wyjątki przy braku sieci
        raise DataError(f"Nie udało się pobrać danych z Yahoo ({symbol}, {interval}). "
                        f"Sprawdź połączenie z internetem. Szczegóły: {exc}") from exc
    if df is None or df.empty:
        return pd.DataFrame(columns=COLUMNS)
    return _normalize(df)


def _stooq_daily(stooq_symbol: str) -> pd.DataFrame:
    import requests

    url = f"https://stooq.pl/q/d/l/?s={stooq_symbol}&i=d"
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "gs-learning-tool"})
        resp.raise_for_status()
    except Exception as exc:
        raise DataError(f"Nie udało się pobrać danych dziennych ze stooq.pl. Szczegóły: {exc}") from exc
    text = resp.text.strip()
    if not text.startswith("Data,"):
        raise DataError("Stooq.pl nie zwrócił pliku CSV (strona wymaga teraz weryfikacji przeglądarki).")
    df = pd.read_csv(io.StringIO(text))
    df = df.rename(columns={"Data": "date", "Otwarcie": "open", "Najwyzszy": "high",
                            "Najnizszy": "low", "Zamkniecie": "close", "Wolumen": "volume"})
    df = df.set_index(pd.to_datetime(df["date"]))
    return _normalize(df.tail(260))


def fetch(source: str, symbol: str, interval: str, period: str, ttl: int) -> pd.DataFrame:
    """Pobiera dane z cache albo ze źródła. Zwraca DataFrame z indeksem UTC."""
    path = _cache_path(source, symbol, interval, period)
    cached = _read_cache(path, ttl)
    if cached is not None:
        return cached
    df = _stooq_daily(symbol) if source == "stooq" else _yahoo(symbol, interval, period)
    _write_cache(path, df)
    return df


def _daily_from_hourly(hourly: pd.DataFrame, tz: str) -> pd.DataFrame:
    """Składa świece dzienne z godzinowych (dzień = data w strefie giełdy)."""
    if hourly.empty:
        return hourly
    local = hourly.tz_convert(tz)
    daily = local.resample("1D").agg({"open": "first", "high": "max", "low": "min",
                                      "close": "last", "volume": "sum"}).dropna()
    daily.index = daily.index.tz_convert("UTC")
    return daily


# ---------------------------------------------------------------- waluta i symbole

def fx_to_pln(currency: str) -> float:
    """Kurs waluty instrumentu na PLN (ostatnie zamknięcie z Yahoo, np. EURPLN=X)."""
    if currency == "PLN":
        return 1.0
    df = fetch("yahoo", f"{currency}PLN=X", "1d", "5d", CACHE_TTL["1d"])
    if df.empty:
        raise DataError(f"Nie udało się pobrać kursu {currency}/PLN z Yahoo, więc nie da się policzyć "
                        "ryzyka w zł. Sprawdź połączenie z internetem.")
    return float(df["close"].iloc[-1])


def has_data(symbol: str) -> bool:
    """Szybkie sprawdzenie, czy Yahoo ma jakiekolwiek dane dzienne dla symbolu."""
    try:
        return not fetch("yahoo", symbol, "1d", PERIODS["1d"], CACHE_TTL["1d"]).empty
    except DataError:
        return False


def check_symbol(symbol: str) -> dict:
    """Informacje o symbolu Yahoo do formularza własnego instrumentu.

    Zwraca ostatnią cenę i to, co Yahoo wie o walucie i strefie czasowej giełdy.
    """
    import yfinance as yf

    symbol = symbol.strip().upper()
    daily = fetch("yahoo", symbol, "1d", PERIODS["1d"], CACHE_TTL["1d"])
    if daily.empty:
        raise DataError(f"Yahoo nie ma danych dla symbolu „{symbol}”. Sprawdź pisownię "
                        "(akcje z GPW mają końcówkę .WA, np. PKN.WA).")
    info = {}
    try:
        fi = yf.Ticker(symbol).fast_info
        info = {"currency": fi.get("currency"), "tz": fi.get("timezone")}
    except Exception:
        pass   # brak metadanych to nie błąd: użytkownik uzupełni pola sam
    intraday = not fetch("yahoo", symbol, "5m", "5d", CACHE_TTL["5m"]).empty
    # Yahoo oddaje ceny z szumem float (135.10000610351562): zostawiamy 6 cyfr znaczących.
    return {"symbol": symbol, "last_price": float(f"{daily['close'].iloc[-1]:.6g}"),
            "currency": (info.get("currency") or "").upper() or None, "tz": info.get("tz"),
            "daily_bars": len(daily), "intraday": intraday}


# ---------------------------------------------------------------- całość

def load_market_data(inst: Instrument, cfg: Config, source: DataSource | None = None) -> MarketData:
    """Pobiera dzienne (12 mies.), godzinowe (60 dni) i LTF dla instrumentu z rejestru."""
    source = source or YahooSource()
    warnings = [] if source.realtime else [
        f"Dane z {source.name} są opóźnione (zwykle ok. 15 min) i mogą mieć luki."]
    ltf_days = max(1, min(int(cfg.LTF_DAYS), 59))
    if cfg.LTF_DAYS > 59:
        warnings.append("Yahoo udostępnia dane 5m/15m tylko z ok. 60 dni, zakres przycięto do 59 dni.")

    daily = source.candles(inst.symbol, "1d", PERIODS["1d"])
    # Yahoo potrafi oddać tylko kilka świec dziennych (np. WIG20.WA): to za mało na SMA 45.
    if len(daily) < MIN_DAILY_BARS:
        daily, note = _fallback_daily(inst, daily, source)
        if note:
            warnings.append(note)
    hourly = source.candles(inst.symbol, "1h", PERIODS["1h"])
    # Zawsze bierzemy maksimum, jakie daje źródło (Yahoo: 59 dni), i dopisujemy je do archiwum
    # na dysku. Dzięki temu z każdym dniem używania mamy dłuższą historię do wyników YTD i roku.
    fresh = source.candles(inst.symbol, cfg.LTF_INTERVAL, f"{MAX_LTF_DAYS}d")
    ltf_full, note = update_archive(source.name, inst.symbol, cfg.LTF_INTERVAL, fresh)
    if note:
        warnings.append(note)
    ltf = ltf_full[ltf_full.index >= ltf_full.index[-1] - pd.Timedelta(days=ltf_days)] \
        if not ltf_full.empty else ltf_full

    if daily.empty:
        raise DataError(f"Brak danych dziennych dla {inst.name} ({inst.symbol}). "
                        "Sprawdź symbol i połączenie z internetem.")
    if hourly.empty:
        warnings.append("Brak danych godzinowych: poziomy liczone tylko z wykresu dziennego.")
    if ltf.empty:
        warnings.append(f"Brak darmowych danych intraday dla {inst.name}: widać tylko poziomy "
                        "z wykresu dziennego, bez sygnałów i backtestu.")

    fx = fx_to_pln(inst.currency)

    # Indeksy przeliczamy na czas giełdy: dzień handlowy = data lokalna.
    daily = daily.copy()
    daily.index = daily.index.tz_convert(inst.tz)
    hourly = hourly.copy()
    if not hourly.empty:
        hourly.index = hourly.index.tz_convert(inst.tz)
    ltf = ltf.copy()
    ltf_full = ltf_full.copy()
    if not ltf.empty:
        ltf.index = ltf.index.tz_convert(inst.tz)
        ltf_full.index = ltf_full.index.tz_convert(inst.tz)

    # Dane dzienne Yahoo bywają oznaczone północą UTC: normalizujemy do daty.
    daily.index = pd.DatetimeIndex([pd.Timestamp(d.date(), tz=inst.tz) for d in daily.index])
    daily = daily[~daily.index.duplicated(keep="last")]

    return MarketData(daily=daily, hourly=hourly, ltf=ltf, instrument=inst, fx_pln=fx,
                      delayed=not source.realtime, warnings=warnings, source=source.name,
                      ltf_full=ltf_full)


# ---------------------------------------------------------------- archiwum świec LTF

def _archive_path(source_name: str, symbol: str, interval: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9]+", "_", f"{source_name}_{symbol}").strip("_")
    return ARCHIVE_DIR / f"{safe}_{interval}.csv"


def update_archive(source_name: str, symbol: str, interval: str,
                   fresh: pd.DataFrame) -> tuple[pd.DataFrame, str | None]:
    """Dopisuje świeże świece do archiwum i zwraca całą historię (maks. ARCHIVE_DAYS dni).

    Świeże dane wygrywają z archiwalnymi (ostatnia, niedokończona świeca zostaje poprawiona).
    Uszkodzonego pliku NIE nadpisujemy: wtedy używamy tylko świeżych danych i ostrzegamy.
    """
    path = _archive_path(source_name, symbol, interval)
    old = pd.DataFrame(columns=COLUMNS)
    if path.exists():
        try:
            old = pd.read_csv(path, index_col=0)
            old.index = pd.to_datetime(old.index, utc=True)
            old = old[COLUMNS].astype(float)
        except Exception:
            return fresh, (f"Archiwum świec {path.name} jest uszkodzone – używam tylko ostatnich dni. "
                           "Popraw albo usuń ten plik.")
    if fresh.empty and old.empty:
        return fresh, None
    parts = [df for df in (old, fresh) if not df.empty]
    merged = pd.concat(parts) if len(parts) > 1 else parts[0]
    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    merged = merged[merged.index >= merged.index[-1] - pd.Timedelta(days=ARCHIVE_DAYS)]
    if not fresh.empty:
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        merged.to_csv(tmp)
        tmp.replace(path)   # zapis atomowy: przerwany zapis nie zostawia uciętego pliku
    return merged, None


def _fallback_daily(inst: Instrument, daily: pd.DataFrame, source: DataSource) -> tuple[pd.DataFrame, str]:
    """Plan awaryjny dla niepełnych danych dziennych: najpierw stooq, potem świece z godzinowych."""
    problems = []
    if inst.stooq_symbol:
        try:
            df = fetch("stooq", inst.stooq_symbol, "1d", "", CACHE_TTL["1d"])
            return df, f"Yahoo nie ma pełnych danych dziennych {inst.name}, dzienne pochodzą ze stooq.pl."
        except DataError as exc:
            problems.append(str(exc))
    composed = _daily_from_hourly(source.candles(inst.symbol, "1h", "1y"), inst.tz)
    if len(composed) > len(daily):
        problems.append(f"Świece dzienne {inst.name} złożono z godzinowych danych Yahoo.")
        return composed, " ".join(problems)
    return daily, " ".join(problems)
