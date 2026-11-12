"""Wszystkie parametry strategii w jednym miejscu.

Nazwy pól są takie same jak w SPEC.md, żeby łatwo było je odnaleźć.
UI może je nadpisać dla pojedynczego uruchomienia, ale wartości domyślne
zawsze pochodzą z tego pliku. Instrument NIE jest parametrem: pochodzi
z rejestru w engine/instruments.py.
"""

import re
from dataclasses import dataclass, asdict, fields


@dataclass
class Config:
    # --- Dane ---
    LTF_INTERVAL: str = "5m"          # dozwolone: 5m, 15m
    LTF_DAYS: int = 30                # ile dni niższej ramy analizujemy

    # --- Poziomy (HTF) ---
    SWING_N: int = 2                  # świec po obu stronach swingu
    ATR_PERIOD: int = 14
    TOL_ATR: float = 0.25             # tolerancja grupowania × ATR dzienny
    MIN_ZONE_ATR: float = 0.1         # minimalna szerokość strefy × ATR dzienny
    MIN_TOUCHES: int = 2              # ile dotknięć robi poziom kluczowym
    MERGE_TOL: float = 0.3            # poziomy bliżej niż tyle × ATR dzienny łączymy w strefę (0 = bez łączenia)

    # --- Filtr kierunku ---
    USE_TREND_FILTER: bool = True
    SMA_FAST: int = 15
    SMA_SLOW: int = 45

    # --- Sygnały (LTF) ---
    RETEST_LOOKBACK: int = 12         # ile świec wstecz szukamy wybicia dla retestu
    TOL_LTF_ATR: float = 0.1          # tolerancja retestu × ATR z LTF

    # --- Zlecenia i ryzyko ---
    ENTRY_VALID: int = 3              # ile świec czeka zlecenie, potem anulujemy
    TP_OFFSET_ATR: float = 0.1        # TP przed strefą o 10% ATR z LTF
    MIN_RR: float = 1.5
    MAX_RISK_ATR: float = 1.5         # maks. ryzyko × ATR(14) z LTF
    MAX_TRADES: int = 2               # maks. transakcji dziennie
    KAPITAL: float = 10000.0          # zł
    RYZYKO_PROC: float = 1.0          # % kapitału na transakcję

    # --- Instrument (puste = wartość z rejestru instrumentów) ---
    TICK: float | None = None
    POINT_VALUE: float | None = None  # w walucie instrumentu, dla CFD o rozmiarze 1

    # --- Kiedy szukać wejść ---
    # session = cała sesja (gdy giełda otwarta; rynki całodobowe: cały dzień handlowy),
    # open    = pierwsze TRADING_WINDOW minut po otwarciu sesji (pierwotna zasada z SPEC),
    # custom  = własne godziny WINDOW_START–WINDOW_END w czasie polskim.
    WINDOW_MODE: str = "session"
    TRADING_WINDOW: int = 90          # minut od otwarcia (tryb „open”)
    WINDOW_START: str = "09:00"       # tryb „custom” (oraz „open” na rynkach całodobowych,
    WINDOW_END: str = "10:30"         # które nie mają otwarcia), czas polski

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_overrides(cls, overrides: dict | None) -> "Config":
        """Tworzy konfigurację z domyślnych wartości i nadpisań z UI.

        Nieznane klucze są ignorowane, a typy wymuszane zgodnie z polem.
        """
        cfg = cls()
        defaults = cls()
        for f in fields(cls):
            if not overrides or f.name not in overrides or overrides[f.name] in (None, ""):
                continue
            value = overrides[f.name]
            default = getattr(defaults, f.name)
            if isinstance(default, bool):
                value = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "tak")
            elif isinstance(default, int):
                value = int(value)
            elif isinstance(default, float) or default is None:   # TICK, POINT_VALUE: liczba albo puste
                value = float(str(value).replace(",", "."))
            else:
                value = str(value)
            setattr(cfg, f.name, value)

        if cfg.LTF_INTERVAL not in ("5m", "15m"):
            raise ValueError("Interwał LTF może być tylko 5m albo 15m.")
        # Zakresy: bez nich np. SMA 0 daje NaN, a SWING_N 0 robi swing z każdej świecy.
        for name, low, high in (("SWING_N", 1, 20), ("ATR_PERIOD", 1, 200), ("MIN_TOUCHES", 1, 100),
                                ("SMA_FAST", 1, 250), ("SMA_SLOW", 1, 250), ("RETEST_LOOKBACK", 1, 500),
                                ("ENTRY_VALID", 1, 100), ("MAX_TRADES", 0, 100), ("LTF_DAYS", 1, 365),
                                ("TRADING_WINDOW", 1, 1440)):
            if not low <= getattr(cfg, name) <= high:
                raise ValueError(f"{name} musi być w zakresie {low}–{high}.")
        for name in ("TOL_ATR", "MIN_ZONE_ATR", "MERGE_TOL", "TOL_LTF_ATR", "TP_OFFSET_ATR", "MIN_RR"):
            if getattr(cfg, name) < 0:
                raise ValueError(f"{name} nie może być ujemne.")
        if cfg.MAX_RISK_ATR <= 0 or cfg.KAPITAL <= 0 or not 0 < cfg.RYZYKO_PROC <= 100:
            raise ValueError("Kapitał i maks. ryzyko muszą być dodatnie, a ryzyko na transakcję w zakresie 0–100%.")
        if cfg.SMA_FAST >= cfg.SMA_SLOW:
            raise ValueError("Krótka średnia (SMA_FAST) musi mieć mniejszy okres niż długa (SMA_SLOW).")
        for name in ("TICK", "POINT_VALUE"):
            if getattr(cfg, name) is not None and getattr(cfg, name) <= 0:
                raise ValueError(f"{name} musi być większe od zera.")
        if cfg.WINDOW_MODE not in WINDOW_MODES:
            raise ValueError("Tryb okienka może być tylko: " + ", ".join(WINDOW_MODES) + ".")
        hm = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
        if not hm.match(cfg.WINDOW_START) or not hm.match(cfg.WINDOW_END):
            raise ValueError("Godziny okienka podaj jako GG:MM, np. 09:00.")
        if cfg.WINDOW_START >= cfg.WINDOW_END:
            raise ValueError("Początek okienka musi być wcześniejszy niż koniec.")
        return cfg


# Tryby okienka i ich nazwy w UI.
WINDOW_MODES = {
    "session": "Cała sesja – gdy giełda jest otwarta",
    "open": "Pierwsze minuty po otwarciu sesji",
    "custom": "Własne godziny (czas polski)",
}

# Opisy parametrów dla UI: grupa i zdanie zrozumiałe dla początkującego.
# options = lista wyboru; show_if = pole widoczne tylko przy danym ustawieniu innego pola.
PARAM_INFO: dict[str, dict] = {
    "SWING_N": {"group": "Poziomy", "label": "Świece wokół swingu",
                "help": "Ile świec po każdej stronie musi być niżej (lub wyżej), żeby uznać szczyt (dołek)."},
    "ATR_PERIOD": {"group": "Poziomy", "label": "Okres ATR",
                   "help": "Z ilu świec liczymy średni zasięg ruchu (ATR)."},
    "TOL_ATR": {"group": "Poziomy", "label": "Tolerancja grupowania (× ATR)",
                "help": "Szczyty i dołki bliżej siebie niż ta odległość tworzą jedną strefę."},
    "MIN_ZONE_ATR": {"group": "Poziomy", "label": "Minimalna szerokość strefy (× ATR)",
                     "help": "Strefa nigdy nie jest węższa niż ten ułamek ATR."},
    "MIN_TOUCHES": {"group": "Poziomy", "label": "Minimalna liczba dotknięć",
                    "help": "Ile razy cena musi zawrócić przy strefie, żeby była kluczowa."},
    "MERGE_TOL": {"group": "Poziomy", "label": "Łączenie bliskich poziomów (× ATR)",
                  "help": "Poziomy bliżej siebie niż ta odległość łączymy w jedną strefę (pas na wykresie). "
                          "0 wyłącza łączenie."},
    "USE_TREND_FILTER": {"group": "Sygnały", "label": "Filtr trendu SMA",
                         "help": "Gdy włączony, nad obiema średnimi gramy tylko long, pod nimi tylko short."},
    "SMA_FAST": {"group": "Sygnały", "label": "Krótka średnia (dni)",
                 "help": "Okres szybszej średniej kroczącej z wykresu dziennego."},
    "SMA_SLOW": {"group": "Sygnały", "label": "Długa średnia (dni)",
                 "help": "Okres wolniejszej średniej kroczącej z wykresu dziennego."},
    "RETEST_LOOKBACK": {"group": "Sygnały", "label": "Okno retestu (świece)",
                        "help": "Ile świec wstecz może być wybicie, do którego cena wraca w reteście."},
    "TOL_LTF_ATR": {"group": "Sygnały", "label": "Tolerancja retestu (× ATR LTF)",
                    "help": "O ile ponad strefę może zatrzymać się cofnięcie, by nadal liczyć je jako retest."},
    "ENTRY_VALID": {"group": "Sygnały", "label": "Ważność zlecenia (świece)",
                    "help": "Po tylu świecach bez wejścia zlecenie jest anulowane."},
    "MIN_RR": {"group": "Ryzyko", "label": "Minimalne R:R",
               "help": "Minimalny stosunek zysku do ryzyka. Słabsze okazje odrzucamy."},
    "MAX_RISK_ATR": {"group": "Ryzyko", "label": "Maksymalne ryzyko (× ATR LTF)",
                     "help": "Gdy stop loss jest dalej niż tyle ATR, sygnał odrzucamy."},
    "TP_OFFSET_ATR": {"group": "Ryzyko", "label": "Odsunięcie TP (× ATR LTF)",
                      "help": "Take profit stawiamy trochę przed strefą, żeby cena na pewno go dosięgła."},
    "MAX_TRADES": {"group": "Ryzyko", "label": "Maks. transakcji dziennie",
                   "help": "Po tylu transakcjach w ciągu dnia nie bierzemy kolejnych sygnałów."},
    "KAPITAL": {"group": "Ryzyko", "label": "Kapitał (zł)",
                "help": "Wielkość konta demo, od której liczymy wielkość pozycji."},
    "RYZYKO_PROC": {"group": "Ryzyko", "label": "Ryzyko na transakcję (%)",
                    "help": "Jaki procent kapitału możesz stracić, gdy zadziała stop loss."},
    "TICK": {"group": "Instrument", "label": "Tick",
             "help": "Najmniejszy krok ceny. Wartość dla CFD o rozmiarze 1 – do sprawdzenia u Twojego brokera."},
    "POINT_VALUE": {"group": "Instrument", "label": "Wartość punktu",
                    "help": "Ile zarabiasz lub tracisz (w walucie instrumentu) na ruchu o 1 punkt przy pozycji "
                            "o rozmiarze 1. Do sprawdzenia u Twojego brokera."},
    "WINDOW_MODE": {"group": "Okienko", "label": "Kiedy szukać wejść", "options": WINDOW_MODES,
                    "help": "„Cała sesja”: sygnały przez cały czas, gdy giełda jest otwarta (waluty, złoto, "
                            "krypto: cały dzień). „Pierwsze minuty”: tylko na początku sesji, gdy ruch jest "
                            "największy. „Własne godziny”: wybierasz godziny sam."},
    "TRADING_WINDOW": {"group": "Okienko", "label": "Ile minut po otwarciu",
                       "show_if": {"WINDOW_MODE": "open"},
                       "help": "Np. 90 = szukamy wejść przez pierwsze półtorej godziny sesji."},
    "WINDOW_START": {"group": "Okienko", "label": "Od godziny (czas polski)",
                     "show_if": {"WINDOW_MODE": "custom"},
                     "help": "Początek okienka w czasie polskim."},
    "WINDOW_END": {"group": "Okienko", "label": "Do godziny (czas polski)",
                   "show_if": {"WINDOW_MODE": "custom"},
                   "help": "Koniec okienka w czasie polskim."},
    "LTF_DAYS": {"group": "Okienko", "label": "Zakres backtestu (dni)",
                 "help": "Ile ostatnich dni niższej ramy sprawdzamy w backteście (Yahoo daje maks. ok. 60)."},
}
