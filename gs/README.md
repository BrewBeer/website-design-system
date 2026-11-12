# GS – Głupia strategia: silnik sygnałów + interfejs

Narzędzie do **nauki i treningu na koncie demo**. Liczy kluczowe poziomy z wykresu
dziennego i godzinowego, szuka sygnałów A/B/C na wykresie 5- lub 15-minutowym
i sprawdza je w backteście. Nie łączy się z brokerem i nie składa żadnych zleceń.

> Sygnały służą do nauki i treningu na koncie demo. To nie jest rekomendacja inwestycyjna.

## Uruchomienie

Potrzebny jest Python 3.11 lub nowszy i internet (dane z Yahoo Finance).

```bash
cd gs
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt

uvicorn api.main:app --reload
```

Potem otwórz w przeglądarce **http://localhost:8000**. Pierwsza analiza uruchamia się sama.

Testy:

```bash
pytest
```

## Co widać na ekranie

Ekran główny („Sygnał”) ma: **pasek decyzji**, **plan na sesję**, wykres i drabinę najbliższych stref.

**Pasek decyzji** pokazuje zawsze jeden stan:

| Stan | Kiedy | Co widać |
|---|---|---|
| **KUP** (zielony) | aktywny sygnał long, dane świeże | wejście, stop loss, take profit, wielkość, ryzyko i możliwy zysk w zł |
| **SPRZEDAJ** (czerwony) | to samo dla short | jw. |
| **PLAN** | aktywny sygnał, ale dane opóźnione o więcej niż 1 świecę | warunek do sprawdzenia na wykresie brokera na żywo + gotowe zlecenie |
| **CZEKAJ** | brak sygnału w okienku | jedno zdanie, np. „Kup, jeśli świeca 5m zamknie się nad 18 420.” |
| **POZA OKIENKIEM** | giełda otwarta, ale wybrałeś szukanie wejść tylko w części sesji | godzina następnego okienka |
| **GIEŁDA ZAMKNIĘTA** | rynek nie działa | kiedy najbliższe otwarcie (w czasie polskim) |

Pasek zawsze mówi też, czy giełda jest otwarta i do której, a lista instrumentów ma dopisek
„· otwarte” / „· zamknięte”. Święta giełdowe nie są uwzględnione (w święto nie ma świec, więc nie ma sygnałów).

Aktywna pozycja i czekające zlecenie są widoczne także po końcu okienka – aż do wyjścia albo końca sesji.

Na darmowych danych z Yahoo (opóźnienie ok. 15 min) aplikacja **nigdy nie pokaże KUP ani SPRZEDAJ** –
zamiast tego PLAN. Przy każdym stanie widać wiek danych, np. „Dane z 10:35 (opóźnienie ok. 15 min)”.
Decyzję liczy `engine/decision.py`.

- **Pokaż zlecenie:** karta z typem zlecenia (stop), ceną aktywacji, SL, TP, wielkością i ważnością
  (do końca dnia), z przyciskiem kopiowania przy każdej wartości.
- **Plan na sesję** (`engine/plan.py`, maks. 8 linii): kierunek ze struktury swingów (wykres dzienny
  i 30 min), najbliższe strefy względem zamknięcia, dwa scenariusze „Jeśli…, to zlecenie stop… SL… TP… R:R…”
  (preferowany pierwszy; przeciw trendowi tylko przy strefie D; scenariusz z R:R poniżej minimum jest
  pominięty z powodem), na co patrzeć na otwarciu (luka, pierwsze 3 świece) i zasady dnia.
  Przed sesją i w jej trakcie to plan na dziś; po zamknięciu sesji (rynki całodobowe: po okienku)
  plan na następną sesję. Po okienku dochodzi linia podsumowania: który scenariusz się zrealizował.
  Ceny w scenariuszach są orientacyjne – liczone dla świecy o typowej wielkości (ATR z 5m).
- **Wykres** w stylu stooq: cienkie świece w jednym kolorze (wzrostowa pusta, spadkowa wypełniona),
  bieżąca i poprzednia sesja, 2 strefy nad ceną i 2 pod ceną jako półprzezroczyste pasy między dwiema
  liniami z podpisem po prawej, np. „D+H1 ×14  4136,7–4142,4”. Strefa z poziomem D ma grubsze linie.
  Aktywny sygnał: strzałka przy świecy sygnałowej i linie wejścia, SL i TP. Menu **„Więcej”** włącza
  średnie SMA, wolumen, sygnały z backtestu i odrzucone sygnały (domyślnie wyłączone).
- **?** w rogu wykresu otwiera **legendę** skrótów, stanów i przycisków (ta sama jest na dole zakładki
  Ustawienia). Przy pierwszym uruchomieniu legenda otwiera się sama, raz.
- **↻** odświeża dane (same odświeżają się też co 2 minuty), **◐** przełącza tryb jasny/ciemny.

Pozostałe zakładki:
- **Wyniki:** wynik strategii za **1 miesiąc, 3 miesiące, od początku roku i rok** – krzywa kapitału
  w R (zielona nad zerem, czerwona pod), statystyki, podział na wzorce i kierunki oraz suma w zł
  przy ryzyku z Ustawień. Ujemna suma R jest pokazana wprost. Jeśli okres nie ma pełnych danych 5m,
  nad wykresem jest informacja, z ilu dni handlowych są dane (wynik liczony tylko z tych dni).
- **Dziennik:** transakcje z backtestu, sortowanie, filtry, eksport CSV, notatki (`data/journal.json`).
  Kliknięcie wiersza pokazuje transakcję na wykresie.
- **Ustawienia:** wszystkie parametry, „+ Własny symbol”. Zmienione pola mają kropkę, a wynik jest
  oznaczony jako „ustawienia własne”.

## Archiwum świec 5m (wyniki YTD i za rok)

Yahoo udostępnia świece 5m i 15m tylko z ok. **60 ostatnich dni**. Dlatego przy każdej analizie
aplikacja dopisuje pobrane świece do archiwum `data/archive/` (do 400 dni wstecz). Z każdym dniem
używania historia rośnie: po roku regularnego uruchamiania wyniki „Od początku roku” i „Rok” będą
liczone z pełnych danych. Przerwa w używaniu dłuższa niż ~60 dni zostawi lukę – aplikacja jej nie
uzupełnia i pokazuje, z ilu dni są dane. Uszkodzony plik archiwum nie jest nadpisywany.

## Dane na żywo (na później)

`engine/data.py` ma interfejs `DataSource` (metoda `candles(symbol, interval, period)`).
Teraz działa `YahooSource` (opóźnione dane). `IBSource` to pusty szkielet pod Interactive Brokers
(biblioteka `ib_async`) – po jego napisaniu `load_market_data(..., source=IBSource())` da dane na żywo,
a aplikacja zacznie pokazywać KUP/SPRZEDAJ zamiast PLAN.

## Zasady strategii w skrócie

1. **Poziomy (wykres dzienny i godzinowy):** swing high to maksimum wyższe niż 2 świece
   z każdej strony (swing low analogicznie). Swingi bliżej siebie niż 0,25 × ATR(14)
   tworzą poziom. Na koniec poziomy (D, H1, PDC, PDH, PDL) bliżej siebie niż `MERGE_TOL` = 0,3 × ATR
   łączą się w jedną strefę – liczone od pierwszego poziomu strefy, więc strefa nie rośnie łańcuchowo,
   a strefy nigdy na siebie nie nachodzą. Sygnały i TP używają połączonych stref.
   Kluczowy poziom ma co najmniej 2 dotknięcia. Zawsze kluczowe są też
   zamknięcie, maksimum i minimum poprzedniego dnia (PDC, PDH, PDL).
2. **Filtr trendu:** zamknięcie nad SMA 15 i 45 – tylko long; pod obiema – tylko short;
   pomiędzy – oba kierunki, ale sygnał jest „słabszy”.
3. **Sygnały (tylko pierwsze 90 minut sesji):**
   - **A – wybicie:** świeca zamyka się za strefą, a poprzednia zamknęła się w niej lub przed nią.
     Sam knot nie wystarcza.
   - **B – retest:** po wybiciu (do 12 świec wcześniej) cena wraca do strefy i zamyka się za nią.
   - **C – grabienie płynności:** knot przebija strefę, a świeca zamyka się z powrotem po drugiej stronie.
4. **Zlecenie:** wejście 1 tick za maksimum (long) lub minimum (short) świecy sygnałowej,
   SL 1 tick za drugim końcem świecy, TP tuż przed najbliższą strefą HTF.
   Zlecenie czeka 3 świece, potem jest anulowane.
5. **Odrzucenie sygnału:** R:R poniżej 1,5; ryzyko większe niż 1,5 × ATR z LTF;
   już 2 transakcje tego dnia; kierunek sprzeczny z trendem; brak poziomu na TP;
   trwa poprzednie zlecenie lub pozycja.
6. **Backtest:** świeca po świecy, bez zaglądania w przyszłość (poziomy dnia D tylko
   z danych do D−1). SL i TP w jednej świecy = liczymy SL. Na koniec sesji pozycja jest zamykana.

## Instrumenty (`engine/instruments.py`)

| Nazwa | Symbol Yahoo | Grupa | Strefa giełdy | Sesja | Tick | 1 pkt (CFD, rozmiar 1) |
|---|---|---|---|---|---|---|
| DAX | `^GDAXI` | Indeksy | Europe/Berlin | 09:00–17:30 | 0,1 | 1 EUR |
| S&P 500 | `^GSPC` | Indeksy | America/New_York | 09:30–16:00 | 0,1 | 1 USD |
| Nasdaq 100 | `^NDX` | Indeksy | America/New_York | 09:30–16:00 | 0,1 | 1 USD |
| WIG20 | `WIG20.WA` (+ stooq) | Indeksy | Europe/Warsaw | 09:00–17:00 | 0,1 | 1 PLN |
| EUR/USD | `EURUSD=X` | Waluty | Europe/London | całodobowo pn–pt | 0,00001 | 1 USD |
| Złoto | `GC=F` | Surowce | America/New_York | całodobowo pn–pt | 0,1 | 1 USD |
| Bitcoin | `BTC-USD` | Krypto | UTC | 24/7 | 0,01 | 1 USD |

**Tick i wartość punktu są orientacyjne – sprawdź je u swojego brokera.** Możesz je zmienić
w panelu „Parametry” (grupa „Instrument”) dla pojedynczej analizy.

Ryzyko w zł = ryzyko w punktach × wartość punktu × kurs waluty na PLN (np. `EURPLN=X` z Yahoo).

**Własny instrument:** na liście wybierz „Własny symbol…”, wpisz symbol z Yahoo (np. `PKN.WA`, `AAPL`),
kliknij „Sprawdź dane”, uzupełnij sesję, strefę, tick i wartość punktu. Własne instrumenty
zapisują się w `data/instruments_user.json`. Ostatnio wybrany instrument przeglądarka pamięta.

## Okienko czasowe

W Ustawieniach **„Kiedy szukać wejść”** (`WINDOW_MODE`) ma trzy opcje:

| Opcja | Co znaczy | Przykład dla S&P 500 |
|---|---|---|
| **Cała sesja** (domyślnie) | sygnały przez cały czas, gdy giełda jest otwarta; rynki całodobowe: cała doba | 15:30–22:00 czasu polskiego |
| **Pierwsze minuty po otwarciu** | tylko `TRADING_WINDOW` minut od otwarcia (pierwotna zasada ze SPEC, 90 min) | 15:30–17:00 |
| **Własne godziny** | `WINDOW_START`–`WINDOW_END` w czasie polskim, dla każdego rynku | np. 16:00–18:00 |

- Godziny sesji liczymy w strefie czasowej giełdy. S&P 500 otwiera się o 9:30 w Nowym Jorku, czyli
  zwykle o 15:30 w Polsce, a przez tydzień-dwa po zmianie czasu o 14:30 (Europa i USA zmieniają czas
  w różne dni).
- UI zawsze pokazuje godziny w czasie polskim z dopiskiem czasu giełdy,
  np. „15:30–22:00 czasu polskiego (9:30–16:00 Nowy Jork)”.
- Zmiana opcji zmienia też wyniki backtestu – przy „Całej sesji” jest więcej sygnałów niż przy 90 minutach.

**Własny instrument:** formularz podpowiada prawdziwe godziny giełdy (wg strefy czasowej z Yahoo:
NYSE/Nasdaq 9:30–16:00, GPW 9:00–17:00, Londyn 8:00–16:30, Xetra 9:00–17:30) i od razu pokazuje,
ile to jest w czasie polskim. Godziny wpisuje się w **czasie giełdy**, nie polskim. Wpisanie symbolu,
który już masz, wczytuje jego ustawienia – zapisanie je poprawia.

## API

| Metoda | Ścieżka | Opis |
|---|---|---|
| GET | `/api/config` | Domyślne parametry i ich opisy |
| GET | `/api/instruments` | Rejestr + własne instrumenty, z informacją, czy są dane |
| GET | `/api/instruments/check?symbol=PKN.WA` | Czy Yahoo ma dane; podpowiedź waluty i strefy |
| POST | `/api/instruments` | Dodanie własnego instrumentu |
| POST | `/api/analyze` | `{instrument: "dax", ltf_interval: "5m", params: {…}}` |
| GET | `/api/trades.csv?run_id=…` | Dziennik transakcji jako CSV |
| GET/POST | `/api/journal` | Notatki do transakcji |

## Parametry (`engine/config.py`)

| Parametr | Domyślnie | Co oznacza |
|---|---|---|
| `LTF_INTERVAL` | `5m` | Interwał sygnałów: 5m albo 15m |
| `LTF_DAYS` | 30 | Ile ostatnich dni sprawdza backtest (Yahoo: maks. ok. 60) |
| `SWING_N` | 2 | Ile świec po każdej stronie musi być niżej/wyżej od swingu |
| `ATR_PERIOD` | 14 | Okres ATR (średni zasięg świecy) |
| `TOL_ATR` | 0,25 | Swingi bliżej niż tyle × ATR dzienny łączą się w strefę |
| `MIN_ZONE_ATR` | 0,1 | Minimalna szerokość strefy × ATR dzienny |
| `MIN_TOUCHES` | 2 | Ile dotknięć robi strefę kluczową |
| `MERGE_TOL` | 0,3 | Poziomy bliżej niż tyle × ATR dzienny łączą się w jedną strefę (0 = bez łączenia) |
| `USE_TREND_FILTER` | tak | Czy filtr SMA 15/45 ogranicza kierunek |
| `SMA_FAST`, `SMA_SLOW` | 15, 45 | Okresy średnich z wykresu dziennego |
| `RETEST_LOOKBACK` | 12 | Ile świec wstecz może być wybicie dla retestu (B) |
| `TOL_LTF_ATR` | 0,1 | O ile × ATR LTF cofnięcie może nie dojść do strefy w reteście |
| `ENTRY_VALID` | 3 | Po ilu świecach bez wejścia zlecenie jest anulowane |
| `TP_OFFSET_ATR` | 0,1 | TP przed strefą o tyle × ATR LTF |
| `MIN_RR` | 1,5 | Minimalny stosunek zysku do ryzyka |
| `MAX_RISK_ATR` | 1,5 | Maksymalne ryzyko × ATR(14) z LTF |
| `MAX_TRADES` | 2 | Maksymalnie transakcji dziennie |
| `KAPITAL` | 10 000 zł | Kapitał konta demo |
| `RYZYKO_PROC` | 1% | Część kapitału ryzykowana na transakcję |
| `WINDOW_MODE` | `session` | Kiedy szukać wejść: `session` (cała sesja), `open` (pierwsze minuty), `custom` (własne godziny) |
| `TRADING_WINDOW` | 90 min | Tryb `open`: ile minut od otwarcia sesji |
| `WINDOW_START`, `WINDOW_END` | 09:00, 10:30 | Tryb `custom` (i `open` na rynkach całodobowych): godziny w czasie polskim |
| `TICK`, `POINT_VALUE` | z rejestru | Nadpisanie ticka i wartości punktu wybranego instrumentu |

Wielkość pozycji = `(KAPITAL × RYZYKO_PROC) / (ryzyko w punktach × wartość punktu × kurs na PLN)`.

## Dane i ich ograniczenia

- Źródło: Yahoo Finance (biblioteka `yfinance`). Dane są **opóźnione** (zwykle ok. 15 minut)
  i mogą mieć luki. Interwały 5m/15m są dostępne tylko z ok. 60 ostatnich dni.
- Pobrane dane trafiają do `data/cache/` i są ponownie używane przez kilka minut
  (dzienne przez kilka godzin), żeby nie pobierać ich przy każdym kliknięciu.
- **WIG20:** Yahoo (`WIG20.WA`) ma dane godzinowe i 5-minutowe, ale prawie nie ma dziennych.
  Program próbuje wtedy stooq.pl. Gdy stooq jest niedostępny (obecnie wymaga weryfikacji
  przeglądarki), świece dzienne są składane z prawdziwych godzinowych danych Yahoo z roku.
  UI pokazuje to w ostrzeżeniach.
- Program nigdy nie wymyśla danych: błąd pobierania pokazuje czytelny komunikat.

## Czas na wykresie

Biblioteka Lightweight Charts nie obsługuje stref czasowych, więc API wysyła czas
**giełdy** zapisany jak UTC. Dzięki temu oś pokazuje godziny sesji (np. 9:00 dla DAX),
niezależnie od strefy czasowej komputera.

## Struktura

```
gs/
  engine/   config.py, data.py, levels.py, signals.py, backtest.py
  api/      main.py (FastAPI, serwuje też web/)
  web/      index.html, styles.css, app.js, vendor/lightweight-charts 4.1.3
  data/     cache/, journal.json
  tests/    test_levels.py, test_signals.py, test_backtest.py, test_api.py
```
