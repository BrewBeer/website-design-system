"""Symulacja świeca po świecy i statystyki w R.

Zasady (SPEC, sekcja 6):
- poziomy i trend na dzień D liczone tylko z danych sprzed D,
- SL i TP w tej samej świecy = liczymy SL,
- pozycja otwarta na koniec sesji zamykana po ostatnim zamknięciu (eod).

Czasy w wyniku (`time`, `entry_time`, `exit_time`) to „czas wykresu”: sekundy
od 1970 w czasie lokalnym giełdy zapisane jak UTC. Tak wymaga biblioteka
Lightweight Charts, żeby oś pokazywała godziny giełdy, a nie UTC.
"""

from __future__ import annotations

import pandas as pd

from .config import Config
from .instruments import Instrument
from .levels import atr, key_levels
from .signals import (trend_state, window_bounds, detect_patterns, build_order, as_arrays,
                      reject_reason, day_tz, _breakout_up, _breakout_down)

BUSY_REASON = "Poprzednie zlecenie lub pozycja jest jeszcze aktywne"


def chart_time(ts: pd.Timestamp) -> int:
    """Czas lokalny giełdy zapisany jako sekundy „UTC” (dla wykresu)."""
    local = ts.tz_localize(None)   # zdejmujemy strefę, zostaje godzina z zegara giełdy
    return int((local - pd.Timestamp("1970-01-01")).total_seconds())


def fmt_price(x: float, decimals: int = 1) -> str:
    """18437.5 -> „18 437,5” (polski zapis, spacja jako separator tysięcy)."""
    s = f"{x:,.{decimals}f}".replace(",", " ").replace(".", ",")
    return s[:-2] if decimals == 1 and s.endswith(",0") else s


# ---------------------------------------------------------------- symulacja

def simulate(daily: pd.DataFrame, hourly: pd.DataFrame, ltf: pd.DataFrame,
             inst: Instrument, cfg: Config, now: pd.Timestamp | None = None,
             fx_pln: float = 1.0) -> dict:
    """Przechodzi przez świece LTF dzień po dniu. Zwraca sygnały, transakcje i stan na koniec.

    „Dzień” liczymy w strefie day_tz: dla rynków z sesją to strefa giełdy,
    dla całodobowych czas polski (tam jest ustawione okienko).
    """
    result = {"signals": [], "trades": [], "days": [], "warnings": []}
    if ltf.empty:
        return result

    tz = day_tz(inst)
    # Tablice numpy zamiast odczytu przez pandas świeca po świecy (wielokrotnie szybciej).
    arr = as_arrays(ltf)
    O, H, L, C = arr["open"], arr["high"], arr["low"], arr["close"]
    ATR = atr(ltf, cfg.ATR_PERIOD).to_numpy(dtype=float)
    TS = ltf.index.as_unit("ns").asi8       # czas świecy w nanosekundach (UTC); pandas 3 domyślnie trzyma µs
    bar_at = lambda i: {"open": O[i], "high": H[i], "low": L[i], "close": C[i]}
    local_dates = ltf.index.tz_convert(tz).date
    now = now or pd.Timestamp.now(tz=tz)
    last_bar_time = ltf.index[-1]
    # Ostatni dzień jest „na żywo”, jeśli ostatnia świeca jest świeża.
    live_day = local_dates[-1] if (now - last_bar_time) < pd.Timedelta(minutes=45) else None

    tol_ltf_mult = cfg.TOL_LTF_ATR
    positions_by_day = ltf.groupby(local_dates).indices
    signal_no = 0
    trade_no = 0

    for day, idx in positions_by_day.items():
        day_ts = pd.Timestamp(day, tz=tz)
        daily_before = daily[daily.index < day_ts]
        levels = key_levels(daily, hourly, cfg, asof=day_ts)
        trend = trend_state(daily_before, cfg)
        result["days"].append({"day": day_ts, "levels": levels, "trend": trend})
        if not levels:
            continue
        if not trend["enough_data"] and not result["warnings"]:
            result["warnings"].append("Za mało danych dziennych do średnich SMA: filtr trendu nieaktywny.")

        position = None   # otwarta transakcja
        pending = None    # zlecenie czekające na wejście
        trades_today = 0
        # Okienko handlu liczymy raz na dzień (to samo co in_window dla każdej świecy).
        if inst.trades_on(day_ts.weekday()):
            w_start, w_end = window_bounds(day_ts, inst, cfg)
            w_start, w_end = w_start.value, w_end.value
        else:
            w_start = w_end = 0

        for i in idx:
            bar = bar_at(i)
            ts = None

            # 1) Zarządzanie otwartą pozycją.
            if position is not None:
                ts = ltf.index[i]
                closed = _check_exit(position, bar, ts)
                if closed:
                    result["trades"].append(closed)
                    position = None

            # 2) Zlecenie oczekujące: wygaśnięcie albo wejście.
            if pending is not None:
                ts = ts or ltf.index[i]
                sig = pending["signal"]
                if i > pending["bar_index"] + cfg.ENTRY_VALID:
                    sig["status"] = "cancelled"
                    pending = None
                elif i > pending["bar_index"] and _touched(sig, bar):
                    sig["status"] = "filled"
                    trade_no += 1
                    trades_today += 1
                    position = {"id": f"T{trade_no}", "signal": sig, "entry_ts": ts,
                                "decimals": inst.price_decimals}
                    pending = None
                    closed = _check_exit(position, bar, ts)   # SL/TP już w świecy wejścia
                    if closed:
                        result["trades"].append(closed)
                        position = None

            # 3) Nowe sygnały tylko w okienku handlu.
            if not w_start <= TS[i] < w_end:
                continue
            atr_ltf = float(ATR[i])
            for p in detect_patterns(arr, i, levels, tol_ltf_mult * atr_ltf, cfg.RETEST_LOOKBACK):
                ts = ts or ltf.index[i]
                order = build_order(bar, p["side"], levels, atr_ltf, cfg, inst, fx_pln)
                busy = position is not None or pending is not None
                reason = reject_reason(order, p["side"], trend["state"], atr_ltf,
                                       trades_today + (1 if pending else 0), cfg)
                if reason is None and busy:
                    reason = BUSY_REASON
                signal_no += 1
                sig = {
                    "id": f"S{signal_no}", "ts": ts, "time": chart_time(ts),
                    "setup": p["setup"], "side": p["side"],
                    **{k: order[k] for k in ("entry", "sl", "tp", "rr", "size", "risk_pln", "reward_pln")},
                    "status": "rejected" if reason else "pending",
                    "reject_reason": reason,
                    "weak": trend["state"] == "mixed",
                    "level_id": p["level"]["id"],
                    "level_low": p["level"]["low"], "level_high": p["level"]["high"],
                    "target_level_id": order["target_level_id"],
                }
                result["signals"].append(sig)
                if reason is None:
                    pending = {"signal": sig, "bar_index": i}

        # Koniec dnia.
        is_live = day == live_day
        if position is not None:
            if is_live:
                position["signal"]["status"] = "active"
                result["open_position"] = position
            else:
                result["trades"].append(_close(position, float(C[idx[-1]]), ltf.index[idx[-1]], "eod"))
        if pending is not None and not is_live:
            pending["signal"]["status"] = "cancelled"
        result["trades_today"] = trades_today

    return result


def _touched(sig: dict, bar) -> bool:
    """Czy cena dotknęła poziomu wejścia (zlecenie stop)."""
    if sig["side"] == "long":
        return float(bar["high"]) >= sig["entry"]
    return float(bar["low"]) <= sig["entry"]


def _check_exit(position: dict, bar, ts) -> dict | None:
    """Sprawdza SL i TP. Gdy oba w jednej świecy, liczymy SL (ostrożnie)."""
    sig = position["signal"]
    hi, lo = float(bar["high"]), float(bar["low"])
    if sig["side"] == "long":
        if lo <= sig["sl"]:
            return _close(position, sig["sl"], ts, "sl")
        if hi >= sig["tp"]:
            return _close(position, sig["tp"], ts, "tp")
    else:
        if hi >= sig["sl"]:
            return _close(position, sig["sl"], ts, "sl")
        if lo <= sig["tp"]:
            return _close(position, sig["tp"], ts, "tp")
    return None


def _close(position: dict, price: float, ts, reason: str) -> dict:
    sig = position["signal"]
    risk = abs(sig["entry"] - sig["sl"])
    move = price - sig["entry"] if sig["side"] == "long" else sig["entry"] - price
    return {
        "id": position["id"], "signal_id": sig["id"],
        "entry_time": chart_time(position["entry_ts"]), "exit_time": chart_time(ts),
        "entry_ts": position["entry_ts"], "exit_ts": ts,
        "setup": sig["setup"], "side": sig["side"],
        "entry": sig["entry"], "sl": sig["sl"], "tp": sig["tp"], "exit_price": round(price, position["decimals"]),
        "result_r": round(move / risk, 2) if risk > 0 else 0.0,
        "exit_reason": reason,
    }


# ---------------------------------------------------------------- statystyki

def compute_stats(trades: list[dict]) -> dict:
    """Statystyki w R: liczba, % zyskownych, średni i suma R, obsunięcie, seria strat."""
    rs = [t["result_r"] for t in trades]
    equity, peak, max_dd, cum = [], 0.0, 0.0, 0.0
    streak = max_streak = 0
    for t in trades:
        cum += t["result_r"]
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
        equity.append({"time": t["exit_time"], "value": round(cum, 2)})
        streak = streak + 1 if t["result_r"] < 0 else 0
        max_streak = max(max_streak, streak)

    def group(key):
        out = {}
        for t in trades:
            g = out.setdefault(t[key], {"trades": 0, "wins": 0, "sum_r": 0.0})
            g["trades"] += 1
            g["wins"] += t["result_r"] > 0
            g["sum_r"] += t["result_r"]
        return {k: {"trades": v["trades"], "winrate": round(100 * v["wins"] / v["trades"], 1),
                    "sum_r": round(v["sum_r"], 2)} for k, v in sorted(out.items())}

    n = len(rs)
    return {
        "trades": n,
        "winrate": round(100 * sum(r > 0 for r in rs) / n, 1) if n else 0.0,
        "avg_r": round(sum(rs) / n, 2) if n else 0.0,
        "sum_r": round(sum(rs), 2),
        "max_dd_r": round(max_dd, 2),
        "max_loss_streak": max_streak,
        "by_setup": group("setup"),
        "by_side": group("side"),
        "equity_r": equity,
    }


# ---------------------------------------------------------------- lista „czekam na…”

def watchlist(levels: list[dict], price: float, trend: str, ltf: pd.DataFrame, cfg: Config,
              decimals: int = 1) -> list[dict]:
    """Opisowe scenariusze dla najbliższych stref nad i pod ceną."""
    allow_long = not cfg.USE_TREND_FILTER or trend != "short"
    allow_short = not cfg.USE_TREND_FILTER or trend != "long"
    above = sorted([l for l in levels if l["low"] > price], key=lambda l: l["low"])
    below = sorted([l for l in levels if l["high"] < price], key=lambda l: -l["high"])
    fp = lambda x: fmt_price(x, decimals)
    zone = lambda l: f"{fp(l['low'])}–{fp(l['high'])}"
    items = []

    # Retest strefy przebitej w ostatnich świecach.
    closes = ltf["close"].to_numpy() if not ltf.empty else []
    n = len(closes)
    for lv in levels:
        for j in range(max(1, n - cfg.RETEST_LOOKBACK), n):
            if allow_long and _breakout_up(closes[j - 1], closes[j], lv) and price > lv["high"]:
                items.append({"text": f"Retest strefy {zone(lv)}, jeśli cena wróci", "level_id": lv["id"],
                              "setup": "B", "side": "long"})
                break
            if allow_short and _breakout_down(closes[j - 1], closes[j], lv) and price < lv["low"]:
                items.append({"text": f"Retest strefy {zone(lv)} od dołu, jeśli cena wróci",
                              "level_id": lv["id"], "setup": "B", "side": "short"})
                break

    if above:
        lv = above[0]
        if allow_long:
            items.append({"text": f"Wybicie: zamknięcie świecy nad {fp(lv['high'])} (strefa {zone(lv)})",
                          "level_id": lv["id"], "setup": "A", "side": "long"})
        if allow_short:
            items.append({"text": f"Grabienie płynności: knot nad {fp(lv['high'])} i zamknięcie pod nim",
                          "level_id": lv["id"], "setup": "C", "side": "short"})
    if below:
        lv = below[0]
        if allow_short:
            items.append({"text": f"Wybicie w dół: zamknięcie świecy pod {fp(lv['low'])} (strefa {zone(lv)})",
                          "level_id": lv["id"], "setup": "A", "side": "short"})
        if allow_long:
            items.append({"text": f"Grabienie płynności: knot pod {fp(lv['low'])} i zamknięcie nad nim",
                          "level_id": lv["id"], "setup": "C", "side": "long"})
    return items
