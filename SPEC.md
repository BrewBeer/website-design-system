# SPEC: Głupia strategia (GS) – silnik sygnałów + interfejs

Instrukcja dla Claude Code: zbuduj projekt dokładnie według tej specyfikacji.
Pracuj etapami (patrz sekcja 12), po każdym etapie uruchom testy i pokaż wynik.
Kod komentuj po polsku, interfejs w całości po polsku.

Narzędzie służy do NAUKI i treningu na koncie demo. Nie łączy się z brokerem
i nie składa żadnych zleceń.

---

## 1. Dane
- Instrument (parametr `SYMBOL`), domyślnie `^GDAXI` (DAX). Przykłady: `^GSPC`
  (S&P 500), `^IXIC` (Nasdaq). Dla WIG20 najpierw sprawdź, czy Yahoo ma dane;
  jeśli nie, użyj darmowych danych dziennych CSV ze stooq.pl i jasno pokaż w UI,
  że intraday nie jest dostępny.
- Wyższa rama (HTF, do poziomów): dzienne z 12 miesięcy oraz godzinowe z 60 dni.
- Niższa rama (LTF, do sygnałów): 5m (parametr `LTF_INTERVAL`, dozwolone też 15m)
  z ostatnich 30 dni.
- Ograniczenia Yahoo: 5m/15m tylko ok. 60 dni wstecz, dane mogą być opóźnione.
  Pokaż to w UI (pasek stanu danych).
- Cache danych na dysku (`data/cache/`), żeby nie pobierać tego samego co chwilę.
- Nigdy nie wymyślaj danych. Błąd pobierania = czytelny komunikat w UI.

## 2. Kluczowe poziomy (z HTF, mechanicznie i powtarzalnie)
- Swing high: maksimum wyższe niż maksima `N=2` świec po obu stronach.
  Swing low analogicznie.
- Grupuj swingi (high i low razem) różniące się o mniej niż
  `TOL = 0,25 × ATR(14)` z dziennego. Strefa = od najniższej do najwyższej ceny
  w grupie, minimalna szerokość `0,1 × ATR`.
- Poziom KLUCZOWY: co najmniej `MIN_TOUCHES = 2` dotknięcia.
- Zawsze kluczowe: zamknięcie, maksimum i minimum poprzedniego dnia.
- Dla każdego poziomu: cena dolna, górna, liczba dotknięć, data ostatniego
  dotknięcia, rama (D / H1).

## 3. Filtr kierunku (`USE_TREND_FILTER = True`)
- Na dziennym SMA 15 i SMA 45.
- Zamknięcie nad obiema: tylko long. Pod obiema: tylko short.
- Pomiędzy: oba kierunki, sygnał oznaczony jako „słabszy”.

## 4. Sygnały GS na LTF (tylko w okienku czasowym)
- `TRADING_WINDOW`: domyślnie pierwsze 90 minut po otwarciu sesji rynku.
- Świeca sygnałowa spełnia jeden z wzorców:
  - **A) Wybicie (long):** zamknięcie NAD górną granicą strefy, poprzednia świeca
    zamknęła się w strefie lub pod nią. Sam knot nie wystarcza.
  - **B) Retest (long):** w ostatnich `RETEST_LOOKBACK = 12` świecach było wybicie
    w górę; teraz minimum schodzi do strefy (≤ górna granica + `TOL_LTF`),
    a zamknięcie jest nad górną granicą.
  - **C) Grabienie płynności (long):** minimum POD dolną granicą strefy wsparcia,
    zamknięcie z powrotem NAD dolną granicą.
- Short: lustrzane odbicie wszystkich trzech reguł.

## 5. Zlecenia
- Wejście long: maksimum świecy sygnałowej + 1 tick. Short: minimum − 1 tick.
- SL long: minimum − 1 tick. Short: maksimum + 1 tick.
- Ważność zlecenia: `ENTRY_VALID = 3` świece, potem anuluj.
- TP: najbliższy kluczowy poziom HTF w kierunku transakcji (long: dolna granica
  najbliższej strefy powyżej wejścia minus 10% ATR LTF). Tylko poziomy HTF.
- Odrzuć sygnał, jeśli:
  - R:R < `MIN_RR = 1,5`,
  - ryzyko > `MAX_RISK_ATR = 1,5 × ATR(14)` z LTF,
  - tego dnia były już `MAX_TRADES = 2` transakcje,
  - kierunek sprzeczny z filtrem trendu.
  Każdy odrzucony sygnał zapisz z powodem odrzucenia (UI go pokazuje).
- Wielkość pozycji: `(KAPITAL × RYZYKO_PROC) / (ryzyko w pkt × wartość punktu)`,
  domyślnie `RYZYKO_PROC = 1%`. Wartość punktu to parametr instrumentu.

## 6. Backtest
- Świeca po świecy, bez look-ahead: poziomy na dany dzień tylko z danych sprzed
  tego dnia.
- SL i TP w tej samej świecy = liczymy SL.
- Pozycje otwarte na koniec sesji zamykane po ostatnim zamknięciu.
- Statystyki: liczba transakcji, % zyskownych, średni R, suma R, maksymalne
  obsunięcie w R, najdłuższa seria strat, podział na A/B/C oraz long/short,
  krzywa kapitału w R.
- Jeśli suma R jest ujemna, UI mówi to wprost. Nie dopasowuj parametrów
  automatycznie pod lepszy wynik.

## 7. Parametry
Wszystkie parametry w jednym pliku `engine/config.py` (dataclass z wartościami
domyślnymi). UI pozwala je zmieniać dla pojedynczego uruchomienia, a przycisk
„Przywróć domyślne” wraca do wartości z pliku.

---

## 8. Architektura

```
gs/
  engine/
    config.py        parametry
    data.py          pobieranie + cache (yfinance, stooq)
    levels.py        swingi, strefy, poziomy
    signals.py       wzorce A/B/C, filtr trendu, zlecenia
    backtest.py      symulacja, statystyki
  api/
    main.py          FastAPI, serwuje też pliki z web/
  web/
    index.html
    styles.css
    app.js
  data/
    cache/
    journal.json     notatki z dziennika
  tests/
    test_levels.py
    test_signals.py  syntetyczne świece dla A, B, C, long i short
    test_backtest.py
  requirements.txt
  README.md          instrukcja uruchomienia po polsku
```

Uruchomienie: `uvicorn api.main:app --reload`, potem `http://localhost:8000`.

### Endpointy
- `GET  /api/config` – domyślne parametry i lista instrumentów.
- `POST /api/analyze` – body: `{symbol, ltf_interval, params}`; odpowiedź:
  kontrakt JSON poniżej.
- `GET  /api/trades.csv?run_id=…` – dziennik transakcji z backtestu.
- `GET  /api/journal`, `POST /api/journal` – notatki użytkownika do transakcji
  (zapisywane w `data/journal.json`).

### Kontrakt JSON `/api/analyze`
```json
{
  "run_id": "string",
  "meta": {"symbol": "^GDAXI", "ltf": "5m", "data_from": "ISO", "data_to": "ISO",
           "delayed": true, "warnings": ["…"]},
  "trend": {"sma15": 0, "sma45": 0, "state": "long|short|mixed"},
  "candles": [{"time": 1730880000, "open": 0, "high": 0, "low": 0, "close": 0, "volume": 0}],
  "levels": [{"id": "L1", "low": 0, "high": 0, "touches": 3, "last_touch": "ISO",
              "frame": "D|H1|PDC|PDH|PDL"}],
  "signals": [{"id": "S1", "time": 0, "setup": "A|B|C", "side": "long|short",
               "entry": 0, "sl": 0, "tp": 0, "rr": 0, "size": 0,
               "status": "active|pending|filled|cancelled|rejected",
               "reject_reason": null, "weak": false, "level_id": "L1"}],
  "watchlist": [{"text": "Retest strefy 18 420–18 450, jeśli cena wróci",
                 "level_id": "L1", "setup": "B", "side": "long"}],
  "backtest": {"trades": 0, "winrate": 0, "avg_r": 0, "sum_r": 0, "max_dd_r": 0,
               "max_loss_streak": 0, "by_setup": {}, "by_side": {},
               "equity_r": [{"time": 0, "value": 0}]},
  "trades": [{"id": "T1", "signal_id": "S1", "entry_time": 0, "exit_time": 0,
              "result_r": 0, "exit_reason": "tp|sl|eod"}]
}
```

---

## 9. Interfejs: kierunek wizualny

Charakter: **klasyczny terminal maklerski przepisany językiem modernizmu**.
Wzór: modernistyczna typografia informacyjna (siatka, porządek, czytelność),
a nie efekciarski „dark trading UI”. Spokojne, papierowe tło, jedna wyraźna
oś wizualna. Odwaga skupiona w jednym miejscu (drabina poziomów, sekcja 10),
reszta cicha i zdyscyplinowana.

### Kolory (tokeny CSS na `:root`)
| Token | Jasny | Ciemny | Rola |
|---|---|---|---|
| `--paper` | `#ECEEEA` | `#191B1D` | tło strony |
| `--sheet` | `#F8F9F6` | `#222527` | tło paneli |
| `--ink` | `#1C1F22` | `#E6E8E3` | tekst główny |
| `--graphite` | `#5D646B` | `#9AA1A7` | tekst drugorzędny, siatka |
| `--cobalt` | `#2B4A8B` | `#7E9BD6` | akcent interfejsu, focus, zaznaczenia |
| `--long` | `#1E6E52` | `#5DBE95` | świece wzrostowe, sygnały long |
| `--short` | `#B23A2E` | `#E57A6E` | świece spadkowe, sygnały short |
| `--zone` | `#C49A2C` | `#D9B456` | strefy poziomów (z przezroczystością) |

- Tryb ciemny przez `@media (prefers-color-scheme: dark)` oraz przełącznik
  w nagłówku (`data-theme` na `<html>`), wybór zapamiętany w localStorage.
- Kolor nigdy nie jest jedynym nośnikiem znaczenia: long/short mają też
  kształt znacznika (trójkąt w górę / w dół) i etykietę tekstową.

### Typografia
- Jedna rodzina: **Archivo** (Google Fonts, osie wagi i szerokości), fallback
  `"Helvetica Neue", Arial, sans-serif`.
- Liczby zawsze `font-variant-numeric: tabular-nums`, żeby kolumny cen się
  nie „trzęsły”.
- Skala: 13 / 15 / 18 / 24 / 48 px. 48 px w szerokości rozciągniętej
  (`font-stretch: 125%`) tylko dla bieżącej ceny w nagłówku.
- Wagi: 400 i 600. Sentence case wszędzie, bez wersalików i bez rozstrzelonych
  etykiet nad sekcjami.

### Kształt
- Siatka 8 px. Panele oddzielone odstępami i jednolitym tłem `--sheet`,
  bez cieni i gradientów.
- Zaokrąglenia: 2 px dla pól i przycisków, 0 dla paneli.
- Ruch tylko w odpowiedzi na akcję użytkownika (otwarcie panelu, wybór
  transakcji). Szanuj `prefers-reduced-motion`.

---

## 10. Interfejs: układ i funkcje

```
+--------------------------------------------------------------------------+
| GS   [DAX ▾] [5m ▾]   18 437,5  +0,42%    dane opóźnione   [Analizuj] [◐] |
+------------------------------------------------------+-------------------+
|                                                      |  Drabina poziomów |
|                 WYKRES ŚWIECOWY                      |  18 610  ▒▒  D ×3 |
|   strefy poziomów, znaczniki A/B/C, linie wejścia,   |  18 520  ▒   PDH  |
|   SL i TP dla wybranego sygnału                      |  ── cena 18 437 ──|
|                                                      |  18 420  ▒▒▒ H1×4 |
|                                                      |  18 300  ▒   PDL  |
+------------------------------------------------------+-------------------+
| Teraz: aktywny sygnał / czekam na…   | Backtest: statystyki + krzywa R   |
+--------------------------------------+-----------------------------------+
| Dziennik transakcji (tabela, filtr A/B/C, long/short, eksport CSV)       |
+--------------------------------------------------------------------------+
| Parametry (panel wysuwany z boku)                                        |
+--------------------------------------------------------------------------+
```

### Nagłówek
- Wybór instrumentu i interwału, bieżąca cena (48 px, rozciągnięta), zmiana
  dzienna, stan filtra trendu („Nad SMA 15 i 45 – tylko long”), pasek stanu
  danych (zakres dat, opóźnienie, ostrzeżenia).
- Przycisk „Analizuj” uruchamia `/api/analyze`; w trakcie pokazuje stan
  „Liczę…” i blokuje się, by nie wysłać dwóch zapytań.

### Wykres
- Biblioteka **TradingView Lightweight Charts**, wersja przypięta dokładnie:
  `https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js`
  (lub ta sama wersja lokalnie w `web/vendor/`).
- Świece w kolorach `--long` / `--short`, wolumen jako histogram na dole.
- Strefy poziomów jako półprzezroczyste pasy `--zone`; grubość krawędzi
  rośnie z liczbą dotknięć.
- SMA 15 i SMA 45 (z dziennego) jako dwie cienkie linie, przełączane.
- Znaczniki sygnałów: trójkąt + litera A/B/C. Odrzucone sygnały wyszarzone,
  domyślnie ukryte (przełącznik „Pokaż odrzucone”).
- Kliknięcie sygnału: rysuje linie wejścia (`--long`/`--short` przerywana),
  SL (`--short`), TP (`--cobalt`), z cenami na osi i opisem R:R.
- Przełączniki widoczności: A, B, C, long, short, poziomy, średnie.

### Drabina poziomów (element wyróżniający)
- Pionowa skala cen obok wykresu, zsynchronizowana z jego widocznym zakresem.
- Każdy poziom to wiersz: cena (tabular-nums), pasek o długości proporcjonalnej
  do liczby dotknięć, źródło (D, H1, PDC, PDH, PDL).
- Bieżąca cena jako pozioma linia przesuwająca się po drabinie; poziomy
  najbliższe cenie (powyżej i poniżej) wyróżnione `--cobalt`.
- Najechanie na poziom podświetla jego strefę na wykresie i odwrotnie.

### Panel „Teraz”
- Aktywny sygnał: wzorzec, kierunek, wejście, SL, TP, R:R, wielkość pozycji,
  ryzyko w zł, licznik transakcji dnia („1 z 2”).
- Lista „Czekam na…” z `watchlist`.
- Poza okienkiem czasowym komunikat: „Poza okienkiem handlu. Następne okno:
  jutro 9:00–10:30.”
- Pusty stan: „Brak sygnału. To też dobry dzień – nie łamiesz zasad.”

### Backtest
- Statystyki w prostych wierszach (etykieta + liczba), krzywa kapitału w R
  jako mały wykres liniowy. Ujemna suma R pokazana wprost, z kolorem
  `--short` i zdaniem „W tej konfiguracji strategia traci.”

### Dziennik transakcji
- Tabela: data, wzorzec, kierunek, wejście, SL, TP, wynik w R, powód wyjścia,
  notatka. Sortowanie po kolumnach, filtry, eksport CSV.
- Kliknięcie wiersza przewija wykres do transakcji i zaznacza ją.
- Pole notatki do każdej transakcji („Co widziałem, co zrobiłbym inaczej”),
  zapisywane przez `/api/journal`.

### Parametry
- Panel wysuwany z prawej: wszystkie parametry z sekcji 7, pogrupowane
  (Poziomy, Sygnały, Ryzyko, Okienko). Każde pole z krótkim opisem
  zrozumiałym dla początkującego, np. „Minimalny stosunek zysku do ryzyka”.
- „Przywróć domyślne” i „Analizuj z tymi ustawieniami”.
- Zmiana parametrów oznacza wynik jako „ustawienia własne”, żeby było jasne,
  że to nie wartości bazowe.

### Jakość
- Responsywnie: poniżej 900 px drabina przechodzi pod wykres, panele
  układają się w jedną kolumnę.
- Pełna obsługa klawiatury, widoczny focus (`--cobalt`, 2 px).
- Kontrast tekstu min. 4,5:1 w obu trybach.
- Bez frameworka: czysty HTML, CSS i JS (moduły ES). Jeden plik CSS
  z tokenami na górze.
- W stopce zawsze: „Sygnały służą do nauki i treningu na koncie demo.
  To nie jest rekomendacja inwestycyjna.”

---

## 11. Kryteria odbioru
1. `pytest` przechodzi; testy pokrywają wzorce A, B, C dla long i short oraz
   przypadek „knot nad strefą bez zamknięcia = brak wybicia”.
2. Backtest nie używa danych z przyszłości (test: poziomy dnia D liczone
   z danych do D−1).
3. UI działa po `uvicorn api.main:app --reload` bez dodatkowej konfiguracji.
4. Błąd danych (zły symbol, brak internetu) daje czytelny komunikat, a nie
   pusty wykres.
5. Oba tryby kolorystyczne czytelne, układ działa na szerokości 375 px.

## 12. Kolejność pracy
1. Silnik: `data.py`, `levels.py` + testy.
2. `signals.py` + testy na syntetycznych świecach.
3. `backtest.py` + testy.
4. API.
5. Interfejs: najpierw wykres i drabina, potem panele, na końcu parametry.
6. README z instrukcją uruchomienia i opisem parametrów.
