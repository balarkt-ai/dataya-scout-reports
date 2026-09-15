#!/usr/bin/env python3
"""
scout41_vix_strategy.py  -  India VIX strategy face-off (READ-ONLY backtest)
============================================================================
A genuinely NEW strategy family for DATAYA ALGO: every existing technical
works on price (OHLC) alone. This one adds a second data source - India VIX,
the option market's own fear gauge (NSE token 99926017, confirmed live by
scout41_vix_check: 749 clean daily bars, 31 spike-days over 3 years).

WHAT IS TESTED (each with the project's honest 70/30 chronological split,
ROBUST rule reused from scout39/scout_common, LOW-N flag when test N < 15).
All VIX features use ONLY completed daily VIX bars (no lookahead: a decision
taken at day D's close uses VIX up to D; an intraday day uses the PRIOR day's
VIX regime, which is fully known before the open).

POSITIONAL (daily index bars, entry at next day's OPEN):
  P1  FEAR-SPIKE REVERSAL (long only): within the last 5 sessions VIX rose
      >= X% over 3 sessions (X in 10/15/20), and today VIX closed BELOW
      yesterday (first cooling day = fear peaking). Buy next open. Stop =
      lowest low of the last 5 sessions; target = 2R (filled at target if
      touched); time exit after 10 sessions at close. One trade per episode
      (5-session cooldown after an exit). Documented vol mean-reversion.
  P2  DONCHIAN-20 BREAKOUT with a VIX GATE - the honest A/B: the SAME price
      trigger (close beyond the prior 20-day high -> long, below prior 20-day
      low -> short; exit on the opposite 10-day channel close or 15-day time
      stop) run under three gates on the day-D VIX regime:
          NONE      - control, every signal taken
          CALM_ONLY - only when VIX < its 20-day SMA (complacent, trending)
          NOT_FEAR  - everything except VIX > 1.10 x SMA20 (panic)
      If the gate does not beat NONE on TEST, the VIX adds nothing.

INTRADAY (5-min index bars, one trade per day):
  I1  OPENING-RANGE BREAKOUT (first 3 bars = 09:15-09:30): first 5-min close
      above the OR high -> long, below the OR low -> short, from 09:30 to
      13:00. Stop = the other side of the OR; target = 2R; clock-based EOD
      exit at 15:15 (same no-lookahead day-end idea as scout39). Gates on the
      PRIOR day's VIX regime: NONE (control) / CALM_ONLY / FEAR_ONLY.

Output: per index/strategy/gate train+test stats + verdict, then a GATE
EFFECT table (gated vs NONE on test) - that table is the real answer. Trades
dumped to /data/scout41_trades.csv. Points = index points, no charges.

READ-ONLY. Never places an order. Refuses to run during market hours (it
competes with the live bot for Angel API calls). Reuses scout39's loaders,
ATR/EMA, stats/verdict/split so every number is on the same footing.
"""
import os, sys, csv, json, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scout39_poc_sweep_pullback as s39

VIX_INST = {"exchange": "NSE", "token": "99926017"}
CACHE_DIR = "/data/scout41_cache"
CSV_PATH = "/data/scout41_trades.csv"

SPIKE_X = [10.0, 15.0, 20.0]
SPIKE_WINDOW = 3          # VIX % change measured over this many sessions
SPIKE_LOOKBACK = 5        # spike must have happened within these sessions
P1_STOP_LB = 5
P1_TIME = 10
P1_COOLDOWN = 5
DON_ENTRY = 20
DON_EXIT = 10
P2_TIME = 15
VIX_SMA = 20
FEAR_MULT = 1.10
OR_BARS = 3
I1_LAST_ENTRY = datetime.time(13, 0)
I1_EOD = datetime.time(15, 15)
P2_GATES = ["NONE", "CALM_ONLY", "NOT_FEAR"]
I1_GATES = ["NONE", "CALM_ONLY", "FEAR_ONLY"]


# ------------------------------------------------------------------ VIX ----
def load_vix_daily(api_maker):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, "INDIAVIX_daily.json")
    if os.path.exists(path):
        with open(path) as f:
            rows = json.load(f)
        print(f"  VIX daily cache hit: {len(rows)} bars")
    else:
        rows = s39._raw_rows(api_maker(), VIX_INST["exchange"], VIX_INST["token"], "ONE_DAY",
                             s39.DAYS_BACK_1D, s39.CHUNK_1D)
        with open(path, "w") as f:
            json.dump(rows, f)
    bars = []
    for c in rows:
        ts = c[0][:10]
        bars.append({"date": ts, "c": float(c[4])})
    bars.sort(key=lambda b: b["date"])
    return bars


def vix_features(vix_bars):
    """Per date: close, sma20, chg over SPIKE_WINDOW sessions (%), cooling flag,
    regime CALM/FEAR/NEUTRAL, spike_recent (any chg>=X within SPIKE_LOOKBACK
    sessions, per X). Uses only bars up to and including that date."""
    feats = {}
    closes = [b["c"] for b in vix_bars]
    for i, b in enumerate(vix_bars):
        f = {"c": b["c"], "sma": None, "chg": None, "cooling": False, "regime": "NEUTRAL"}
        if i + 1 >= VIX_SMA:
            f["sma"] = sum(closes[i - VIX_SMA + 1:i + 1]) / VIX_SMA
            if b["c"] < f["sma"]:
                f["regime"] = "CALM"
            elif b["c"] > f["sma"] * FEAR_MULT:
                f["regime"] = "FEAR"
        if i >= SPIKE_WINDOW and closes[i - SPIKE_WINDOW] > 0:
            f["chg"] = (closes[i] / closes[i - SPIKE_WINDOW] - 1.0) * 100.0
        if i >= 1:
            f["cooling"] = closes[i] < closes[i - 1]
        f["spike_recent"] = {}
        for X in SPIKE_X:
            hit = False
            for j in range(max(SPIKE_WINDOW, i - SPIKE_LOOKBACK + 1), i + 1):
                if closes[j - SPIKE_WINDOW] > 0 and (closes[j] / closes[j - SPIKE_WINDOW] - 1.0) * 100.0 >= X:
                    hit = True
                    break
            f["spike_recent"][X] = hit
        feats[b["date"]] = f
    return feats


def prior_feat(feats, sorted_dates, date):
    """VIX features of the last VIX session strictly BEFORE `date`."""
    import bisect
    k = bisect.bisect_left(sorted_dates, date)
    if k == 0:
        return None
    return feats[sorted_dates[k - 1]]


# ------------------------------------------------------------- POSITIONAL --
def run_p1(daily, feats, X):
    """Fear-spike reversal long. Decision at day i close, entry at i+1 open."""
    trades = []
    n = len(daily)
    i = P1_STOP_LB
    cooldown_until = -1
    while i < n - 1:
        f = feats.get(daily[i]["date"])
        if f is None or i <= cooldown_until or not f["spike_recent"].get(X) or not f["cooling"]:
            i += 1
            continue
        entry_i = i + 1
        entry = daily[entry_i]["o"]
        stop = min(b["l"] for b in daily[i - P1_STOP_LB + 1:i + 1])
        if stop >= entry:
            i += 1
            continue
        target = entry + 2.0 * (entry - stop)
        exit_price, exit_i, reason = None, None, None
        for j in range(entry_i, min(entry_i + P1_TIME, n)):
            b = daily[j]
            if b["l"] <= stop and j > entry_i:
                exit_price, exit_i, reason = stop, j, "STOP"
                break
            if b["h"] >= target:
                exit_price, exit_i, reason = target, j, "TARGET"
                break
            if j == entry_i and b["l"] <= stop:      # gap/first-day stop
                exit_price, exit_i, reason = min(stop, b["o"]), j, "STOP"
                break
        if exit_price is None:
            exit_i = min(entry_i + P1_TIME - 1, n - 1)
            exit_price, reason = daily[exit_i]["c"], "TIME"
        trades.append({"strategy": f"P1_spike{int(X)}", "gate": "-", "dir": "long", "date": daily[entry_i]["date"],
                       "exit_date": daily[exit_i]["date"], "entry": entry, "exit": exit_price, "stop": stop,
                       "target": target, "pts": round(exit_price - entry, 2), "exit_reason": reason,
                       "vix": f["c"], "vix_chg": round(f["chg"], 1) if f["chg"] is not None else None})
        cooldown_until = exit_i + P1_COOLDOWN
        i = exit_i + 1
    return trades


def gate_allows(gate, regime):
    if gate == "NONE":
        return True
    if gate == "CALM_ONLY":
        return regime == "CALM"
    if gate == "NOT_FEAR":
        return regime != "FEAR"
    if gate == "FEAR_ONLY":
        return regime == "FEAR"
    raise ValueError(gate)


def run_p2(daily, feats, gate):
    """Donchian-20 breakout, both sides, VIX-gated on day-i regime; entry i+1 open."""
    trades = []
    n = len(daily)
    i = DON_ENTRY
    while i < n - 1:
        f = feats.get(daily[i]["date"])
        regime = f["regime"] if f else "NEUTRAL"
        if f is None or f["sma"] is None or not gate_allows(gate, regime):
            i += 1
            continue
        hi20 = max(b["h"] for b in daily[i - DON_ENTRY:i])
        lo20 = min(b["l"] for b in daily[i - DON_ENTRY:i])
        c = daily[i]["c"]
        if c > hi20:
            d = "long"
        elif c < lo20:
            d = "short"
        else:
            i += 1
            continue
        entry_i = i + 1
        entry = daily[entry_i]["o"]
        exit_price, exit_i, reason = None, None, None
        for j in range(entry_i, min(entry_i + P2_TIME, n)):
            lo10 = min(b["l"] for b in daily[j - DON_EXIT:j])
            hi10 = max(b["h"] for b in daily[j - DON_EXIT:j])
            cj = daily[j]["c"]
            if j > entry_i and ((d == "long" and cj < lo10) or (d == "short" and cj > hi10)):
                exit_price, exit_i, reason = cj, j, "CHANNEL"
                break
        if exit_price is None:
            exit_i = min(entry_i + P2_TIME - 1, n - 1)
            exit_price, reason = daily[exit_i]["c"], "TIME"
        pts = (exit_price - entry) if d == "long" else (entry - exit_price)
        trades.append({"strategy": "P2_donchian", "gate": gate, "dir": d, "date": daily[entry_i]["date"],
                       "exit_date": daily[exit_i]["date"], "entry": entry, "exit": exit_price, "stop": None,
                       "target": None, "pts": round(pts, 2), "exit_reason": reason, "vix": f["c"], "vix_chg": None,
                       "regime": regime})
        i = exit_i + 1
    return trades


# --------------------------------------------------------------- INTRADAY --
def run_i1(bars5, feats, vix_dates, gate):
    """Opening-range breakout, one trade/day, gated on the PRIOR day's VIX regime."""
    trades = []
    by_day = {}
    for b in bars5:
        by_day.setdefault(b["date"], []).append(b)
    for date in sorted(by_day):
        pf = prior_feat(feats, vix_dates, date)
        regime = pf["regime"] if pf else "NEUTRAL"
        if pf is None or pf["sma"] is None or not gate_allows(gate, regime):
            continue
        day = by_day[date]
        if len(day) <= OR_BARS + 1:
            continue
        or_hi = max(b["h"] for b in day[:OR_BARS])
        or_lo = min(b["l"] for b in day[:OR_BARS])
        if or_hi <= or_lo:
            continue
        pos = None
        for k in range(OR_BARS, len(day)):
            b = day[k]
            t = b["dt"].time()
            if pos is None:
                if t >= I1_LAST_ENTRY:
                    break
                if b["c"] > or_hi:
                    pos = {"dir": "long", "entry": b["c"], "stop": or_lo, "k": k}
                    pos["target"] = pos["entry"] + 2.0 * (pos["entry"] - or_lo)
                elif b["c"] < or_lo:
                    pos = {"dir": "short", "entry": b["c"], "stop": or_hi, "k": k}
                    pos["target"] = pos["entry"] - 2.0 * (or_hi - pos["entry"])
                continue
            # manage the open position on this bar
            exit_price, reason = None, None
            if pos["dir"] == "long":
                if b["l"] <= pos["stop"]:
                    exit_price, reason = pos["stop"], "STOP"
                elif b["h"] >= pos["target"]:
                    exit_price, reason = pos["target"], "TARGET"
            else:
                if b["h"] >= pos["stop"]:
                    exit_price, reason = pos["stop"], "STOP"
                elif b["l"] <= pos["target"]:
                    exit_price, reason = pos["target"], "TARGET"
            if exit_price is None and t >= I1_EOD:
                exit_price, reason = b["c"], "EOD"
            if exit_price is not None:
                pts = (exit_price - pos["entry"]) if pos["dir"] == "long" else (pos["entry"] - exit_price)
                trades.append({"strategy": "I1_orb", "gate": gate, "dir": pos["dir"], "date": date, "exit_date": date,
                               "entry": pos["entry"], "exit": exit_price, "stop": pos["stop"], "target": pos["target"],
                               "pts": round(pts, 2), "exit_reason": reason, "vix": pf["c"], "vix_chg": None,
                               "regime": regime})
                pos = None
                break
        if pos is not None:      # data ended mid-day - close at last bar, flagged
            b = day[-1]
            pts = (b["c"] - pos["entry"]) if pos["dir"] == "long" else (pos["entry"] - b["c"])
            trades.append({"strategy": "I1_orb", "gate": gate, "dir": pos["dir"], "date": date, "exit_date": date,
                           "entry": pos["entry"], "exit": b["c"], "stop": pos["stop"], "target": pos["target"],
                           "pts": round(pts, 2), "exit_reason": "EOD_LASTBAR", "vix": pf["c"], "vix_chg": None,
                           "regime": regime})
    return trades


# ------------------------------------------------------------------ MAIN ---
def evaluate(trades, cut):
    tr = [t for t in trades if t["date"] < cut]
    te = [t for t in trades if t["date"] >= cut]
    st_tr, st_te = s39.stats(tr), s39.stats(te)
    return st_tr, st_te, s39.verdict(st_tr, st_te)


def fmt(st):
    return f"n={st['n']:<4} win={st['win']:<5} avg={st['avg']:<8} pf={st['pf']}"


def main():
    if s39.market_hours_now() and os.environ.get("SCOUT41_ALLOW_MARKET_HOURS") != "1":
        print("Market hours - refusing to run (competes with the live bot for Angel API calls). Run after 16:00 IST.")
        return
    print("=== scout41: India VIX strategy face-off (read-only) ===\n")
    api_maker = s39.get_api
    print("Loading India VIX daily ...")
    vix = load_vix_daily(api_maker)
    feats = vix_features(vix)
    vix_dates = sorted(feats.keys())
    regimes = [feats[d]["regime"] for d in vix_dates if feats[d]["sma"] is not None]
    print(f"  VIX sessions={len(vix)}  regime mix: CALM={regimes.count('CALM')} NEUTRAL={regimes.count('NEUTRAL')} FEAR={regimes.count('FEAR')}\n")

    all_trades = []
    results = []
    for name, inst in s39.INDEX_INSTRUMENTS.items():
        print(f"---- {name} ----")
        daily = s39.load_daily(api_maker, name, inst)
        bars5 = s39.load_5min(api_maker, name, inst)
        cut_d = s39.split_by_day(daily)
        cut_5 = s39.split_by_day(bars5)
        print(f"  daily bars={len(daily)} (cut {cut_d})   5-min bars={len(bars5)} (cut {cut_5})")
        for X in SPIKE_X:
            tr = run_p1(daily, feats, X)
            for t in tr: t["index"] = name
            all_trades += tr
            results.append((name, f"P1_spike{int(X)}", "-", *evaluate(tr, cut_d)))
        for g in P2_GATES:
            tr = run_p2(daily, feats, g)
            for t in tr: t["index"] = name
            all_trades += tr
            results.append((name, "P2_donchian", g, *evaluate(tr, cut_d)))
        for g in I1_GATES:
            tr = run_i1(bars5, feats, vix_dates, g)
            for t in tr: t["index"] = name
            all_trades += tr
            results.append((name, "I1_orb", g, *evaluate(tr, cut_5)))
        print()

    print("=" * 100)
    print("RESULTS (points; 70/30 chronological split; ROBUST = train avg>0, test avg>0, test>=0.3x train)")
    print("=" * 100)
    for name, strat, gate, st_tr, st_te, v in results:
        print(f"{name:<9} {strat:<12} {gate:<9} TRAIN {fmt(st_tr)}   TEST {fmt(st_te)}   -> {v}")

    print("\n" + "=" * 100)
    print("GATE EFFECT on TEST (gated minus NONE control, same trigger) - the real VIX question")
    print("=" * 100)
    by = {(r[0], r[1], r[2]): r for r in results}
    for name in s39.INDEX_INSTRUMENTS:
        for strat, gates in (("P2_donchian", P2_GATES), ("I1_orb", I1_GATES)):
            base = by.get((name, strat, "NONE"))
            if not base:
                continue
            b_te = base[4]
            for g in gates[1:]:
                r = by.get((name, strat, g))
                if not r:
                    continue
                g_te = r[4]
                d_avg = round(g_te["avg"] - b_te["avg"], 2)
                d_n = g_te["n"] - b_te["n"]
                tag = "HELPS" if d_avg > 0 and g_te["avg"] > 0 else ("neutral" if abs(d_avg) < 0.5 else "HURTS")
                print(f"{name:<9} {strat:<12} {g:<9} test avg {g_te['avg']:>8} vs NONE {b_te['avg']:>8}  diff {d_avg:>7}  trades {g_te['n']} vs {b_te['n']} ({d_n:+})  -> {tag}")

    robust = [r for r in results if r[5].startswith("ROBUST")]
    print(f"\nROBUST combos: {len(robust)}/{len(results)}  (LOW-N ones are chance-level - read the N)")
    for r in robust:
        print(f"  {r[0]} {r[1]} {r[2]}  test n={r[4]['n']} avg={r[4]['avg']}  {r[5]}")

    try:
        os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
        keys = ["index", "strategy", "gate", "dir", "date", "exit_date", "entry", "exit", "stop", "target", "pts",
                "exit_reason", "vix", "vix_chg", "regime"]
        with open(CSV_PATH, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            for t in all_trades:
                w.writerow(t)
        print(f"\nTrades written: {CSV_PATH} ({len(all_trades)} rows)")
    except Exception as e:
        print(f"\n(csv not written: {e})")
    print("\nREAD-ONLY run complete - no orders were placed.")


if __name__ == "__main__":
    main()
