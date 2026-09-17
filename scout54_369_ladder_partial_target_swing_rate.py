#!/usr/bin/env python3
# ============================================================================
# SCOUT54 - REAL BACKTEST: same line-to-line swing test as scout53, but with
# a PARTIAL target instead of requiring the full next line to be reached.
# ------------------------------------------------------------------------
# WHY THIS SCOUT EXISTS (2026-09-17, same day as scout51/52/53):
# scout53 showed the FULL next-line target is only reached 10%-41% of the
# time (all 18 index/timeframe/direction rows CONSISTENT between train and
# test - a clean, reliable finding) before price reverses back through the
# original crossed line. The user accepted this calculation and proposed a
# concrete adjustment: "50% target vachikonga, adhu vandhale podhum" - set
# the target at 50% of the distance to the next line instead of the full
# distance; if THAT is reached, it is enough.
#
# THE TEST (same engine as scout53, only the target distance changes):
#   For a CE cross of line idx: orig_line = levels[idx], full_target =
#   levels[idx+1]. Instead of requiring price to reach full_target, this
#   scout tests a PARTIAL target = orig_line + frac * (full_target -
#   orig_line), for frac in {100%, 75%, 50%, 25%} - 50% is the user's
#   specific ask; 25%/75%/100% are included alongside it in the same report
#   (100% just reproduces scout53's own numbers, included here purely as a
#   sanity cross-check) so the user can see the full trade-off in one run
#   instead of asking again for "what about 60%" etc. FAILED and
#   INCONCLUSIVE are defined exactly as in scout53 (close back through the
#   ORIGINAL crossed line = failed; ladder week ends first = inconclusive).
#   PE crosses are the exact mirror.
#
# This is still a pure descriptive/statistical scan - no entries, no exits,
# no P&L, nothing proposed for live/paper trading. It only answers: "if we
# only needed to reach X% of the way to the next line, how often would
# that happen before the move reversed?" Same real Angel data, ladder
# construction copied verbatim from scout51/52/53.
#
# READ-ONLY. No orders placed.
# ============================================================================
import os
import sys
import time
import json
import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
CACHE_DIR = "/tmp/scout54_cache"
SCRIP_MASTER_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
SCRIP_MASTER_CACHE = os.path.join(CACHE_DIR, "scrip_master.json")
SCRIP_MASTER_MAX_AGE_H = 24


def market_hours_guard():
    now = datetime.datetime.now(IST)
    if now.weekday() < 5 and (9 <= now.hour < 16):
        print("SAFETY STOP: market hours - run this ONLY after 15:30 IST (or on a weekend). Exiting.")
        sys.exit(1)
    return now


def get_api():
    from SmartApi import SmartConnect
    import pyotp

    api_key = os.environ["ANGEL_API_KEY"]
    client_code = os.environ["ANGEL_CLIENT_CODE"]
    mpin = os.environ["ANGEL_MPIN"]
    totp_secret = os.environ["ANGEL_TOTP_SECRET"]

    api = SmartConnect(api_key=api_key)
    totp = pyotp.TOTP(totp_secret).now()
    session = api.generateSession(client_code, mpin, totp)
    if not session or not session.get("status"):
        raise RuntimeError(f"Angel login failed: {session}")
    return api


SVMKR_INSTRUMENTS = {
    "NIFTY":     {"exchange": "NSE", "token": "99926000", "base_unit": 30.0},
    "BANKNIFTY": {"exchange": "NSE", "token": "99926009", "base_unit": 300.0},
    "SENSEX":    {"exchange": "BSE", "token": "99919000", "base_unit": 300.0},
}

MULT = [1.0, 2.0, 3.0, 4.0, 6.0, 9.0, 10.0, 12.0, 15.0]
NM = len(MULT)
NL = 2 * NM + 1
BASE_IDX = NM
INCLUDE_BASE = True

TARGET_FRACTIONS = [1.0, 0.75, 0.5, 0.25]  # user specifically asked for 0.5; others for context

TIMEFRAMES = [
    ("1-MIN", "ONE_MINUTE", 400, 25),
    ("5-MIN", "FIVE_MINUTE", 700, 60),
]


def _load_scrip_master():
    import urllib.request
    os.makedirs(CACHE_DIR, exist_ok=True)
    if os.path.exists(SCRIP_MASTER_CACHE):
        age_h = (time.time() - os.path.getmtime(SCRIP_MASTER_CACHE)) / 3600
        if age_h < SCRIP_MASTER_MAX_AGE_H:
            with open(SCRIP_MASTER_CACHE) as f:
                data = json.load(f)
            print(f"  (scrip master: using cache, {len(data)} rows, {age_h:.1f}h old)")
            return data
    with urllib.request.urlopen(SCRIP_MASTER_URL, timeout=120) as resp:
        data = json.loads(resp.read())
    with open(SCRIP_MASTER_CACHE, "w") as f:
        json.dump(data, f)
    print(f"  (scrip master: downloaded {len(data)} rows)")
    return data


def _fetch_chunk(api, exchange, token, interval, from_dt, to_dt):
    for attempt in range(3):
        try:
            resp = api.getCandleData({
                "exchange": exchange, "symboltoken": token, "interval": interval,
                "fromdate": from_dt.strftime("%Y-%m-%d %H:%M"), "todate": to_dt.strftime("%Y-%m-%d %H:%M"),
            })
            if not resp or not resp.get("status") or resp.get("data") is None:
                return []
            return resp["data"]
        except Exception as e:
            print(f"    retry {attempt + 1}: {e}")
            time.sleep(2)
    return []


def get_index_candles(api, exchange, token, interval, days_back, chunk_days):
    now = datetime.datetime.now()
    end = now
    start = end - datetime.timedelta(days=days_back)
    raw = []
    cur = start
    empty_streak = 0
    while cur < end:
        nxt = min(cur + datetime.timedelta(days=chunk_days), end)
        rows = _fetch_chunk(api, exchange, token, interval, cur, nxt)
        raw.extend(rows)
        print(f"    chunk {cur.date()} -> {nxt.date()}: {len(rows)} rows")
        empty_streak = empty_streak + 1 if not rows else 0
        time.sleep(0.4)
        cur = nxt
        if empty_streak >= 4:
            print(f"    4 consecutive empty chunks - stopping early, likely reached the real data floor.")
            break
    seen = set()
    bars = []
    for row in raw:
        ts = row[0]
        if ts in seen:
            continue
        seen.add(ts)
        dt = datetime.datetime.fromisoformat(ts).replace(tzinfo=None)
        bars.append((dt, float(row[1]), float(row[2]), float(row[3]), float(row[4])))
    bars.sort(key=lambda x: x[0])
    return bars


def week_key(dt):
    y, w, _ = dt.isocalendar()
    return (y, w)


def build_weekly_bases(bars):
    last_close_of_week = {}
    for dt, o, h, l, c in bars:
        last_close_of_week[week_key(dt)] = c
    weeks_sorted = sorted(last_close_of_week.keys())
    base_for_week = {}
    for i in range(1, len(weeks_sorted)):
        base_for_week[weeks_sorted[i]] = last_close_of_week[weeks_sorted[i - 1]]
    return base_for_week


def build_ladder(base_price, base_unit):
    levels = [None] * NL
    for i in range(NM):
        levels[BASE_IDX + 1 + i] = base_price + base_unit * MULT[i]
        levels[BASE_IDX - 1 - i] = base_price - base_unit * MULT[i]
    levels[BASE_IDX] = base_price
    return levels


# ---------------------------------------------------- THE SWING SCAN -------
def scan_partial_target_swings(bars, base_unit, target_fraction):
    """Same cross detection as scout53. Target is now orig_line + frac *
    (full_next_line - orig_line) instead of requiring the full next line."""
    base_for_week = build_weekly_bases(bars)
    results = []

    cur_week = None
    levels = None

    for i, (dt, o, h, l, c) in enumerate(bars):
        prev_c = bars[i - 1][4] if i > 0 else None
        wk = week_key(dt)

        if wk != cur_week:
            cur_week = wk
            base_price = base_for_week.get(wk)
            levels = build_ladder(base_price, base_unit) if base_price is not None else None

        if levels is None or prev_c is None:
            continue

        ce_cross_idx = None
        for idx in range(NL - 1, -1, -1):
            if not INCLUDE_BASE and idx == BASE_IDX:
                continue
            lvl = levels[idx]
            if c > lvl and prev_c <= lvl:
                ce_cross_idx = idx
                break
        pe_cross_idx = None
        for idx in range(0, NL):
            if not INCLUDE_BASE and idx == BASE_IDX:
                continue
            lvl = levels[idx]
            if c < lvl and prev_c >= lvl:
                pe_cross_idx = idx
                break

        if ce_cross_idx is not None and ce_cross_idx < NL - 1:
            orig_line = levels[ce_cross_idx]
            full_target = levels[ce_cross_idx + 1]
            partial_target = orig_line + target_fraction * (full_target - orig_line)
            outcome, resolve_bars = _resolve_swing(bars, i, "CE", orig_line, partial_target, wk)
            results.append((wk, "CE", outcome, resolve_bars))

        if pe_cross_idx is not None and pe_cross_idx > 0:
            orig_line = levels[pe_cross_idx]
            full_target = levels[pe_cross_idx - 1]
            partial_target = orig_line - target_fraction * (orig_line - full_target)
            outcome, resolve_bars = _resolve_swing(bars, i, "PE", orig_line, partial_target, wk)
            results.append((wk, "PE", outcome, resolve_bars))

    return results


def _resolve_swing(bars, cross_i, direction, orig_line, target, cross_week):
    for j in range(cross_i, len(bars)):
        dt, o, h, l, c = bars[j]
        if week_key(dt) != cross_week:
            return "INCONCLUSIVE", j - cross_i
        if direction == "CE":
            if h >= target:
                return "SUCCESS", j - cross_i
            if j > cross_i and c <= orig_line:
                return "FAILED", j - cross_i
        else:
            if l <= target:
                return "SUCCESS", j - cross_i
            if j > cross_i and c >= orig_line:
                return "FAILED", j - cross_i
    return "INCONCLUSIVE", len(bars) - 1 - cross_i


def split_weeks_generic(rows, frac=0.7):
    weeks_sorted = sorted({wk for wk, direction, outcome, rb in rows})
    s = int(len(weeks_sorted) * frac)
    return set(weeks_sorted[:s]), set(weeks_sorted[s:])


def summarize(rows, label):
    if not rows:
        print(f"    {label}: no cross events with a target to test.")
        return None
    train_weeks, test_weeks = split_weeks_generic(rows, frac=0.7)

    def pct(subset):
        n = len(subset)
        if n == 0:
            return 0, 0.0, 0.0, 0.0
        succ = sum(1 for wk, d, o, rb in subset if o == "SUCCESS")
        fail = sum(1 for wk, d, o, rb in subset if o == "FAILED")
        inc = sum(1 for wk, d, o, rb in subset if o == "INCONCLUSIVE")
        return n, succ / n * 100, fail / n * 100, inc / n * 100

    all_n, all_s, all_f, all_i = pct(rows)
    tr = [r for r in rows if r[0] in train_weeks]
    te = [r for r in rows if r[0] in test_weeks]
    tr_n, tr_s, tr_f, tr_i = pct(tr)
    te_n, te_s, te_f, te_i = pct(te)
    avg_resolve = sum(rb for wk, d, o, rb in rows if o != "INCONCLUSIVE") / max(1, sum(1 for wk, d, o, rb in rows if o != "INCONCLUSIVE"))
    consistent = "CONSISTENT" if (tr_n and te_n and abs(tr_s - te_s) <= 15) else "INCONSISTENT"
    print(f"    N={all_n:<6} SUCCESS={all_s:>5.1f}%  FAILED={all_f:>5.1f}%  INCONCLUSIVE={all_i:>4.1f}%  "
          f"avg_bars={avg_resolve:>5.1f}  |  train={tr_s:>5.1f}% test={te_s:>5.1f}% -> {consistent}")
    return all_s


# ------------------------------------------------------------ SMOKE TEST ---
def run_synthetic_smoke_test():
    import math
    bars = []
    d0 = datetime.datetime(2024, 1, 1, 9, 15)
    i = 0
    for day_offset in range(56):
        day = d0 + datetime.timedelta(days=day_offset)
        if day.weekday() >= 5:
            continue
        for m in range(120):
            center = 25000.0 + 60.0 * day_offset
            price = center + 380.0 * math.sin(i / 18.0) + 40.0 * math.sin(i / 4.0)
            o, h, l, c = price - 3, price + 6, price - 6, price
            dt = day.replace(hour=9, minute=15) + datetime.timedelta(minutes=m)
            bars.append((dt, o, h, l, c))
            i += 1

    prev_success = None
    for frac in TARGET_FRACTIONS:
        rows = scan_partial_target_swings(bars, 30.0, frac)
        assert rows, f"SMOKE TEST FAILED: frac={frac} produced zero cross events"
        succ = sum(1 for wk, d, o, rb in rows if o == "SUCCESS") / len(rows) * 100
        print(f"  [smoke] frac={frac:.2f}: N={len(rows)} SUCCESS={succ:.1f}%")
        if prev_success is not None:
            # a SMALLER target must never be HARDER to reach than a bigger one on the same data
            assert succ >= prev_success - 0.01, \
                f"SANITY FAILED: frac={frac} (smaller target) SUCCESS={succ:.1f}% is lower than the larger target's {prev_success:.1f}%"
        prev_success = succ

    # frac=1.0 here must exactly match scout53's own full-target definition (same cross/fail/inconclusive rules)
    rows_full = scan_partial_target_swings(bars, 30.0, 1.0)
    outcomes = {}
    for wk, d, o, rb in rows_full:
        outcomes[o] = outcomes.get(o, 0) + 1
    assert outcomes == {"SUCCESS": 751, "FAILED": 51, "INCONCLUSIVE": 1}, \
        f"REGRESSION FAILED vs scout53's own smoke test at frac=1.0: got {outcomes}"
    print(f"  [regression] frac=1.0 matches scout53's own smoke-test outcome exactly: {outcomes}")
    print("SMOKE TEST OK - partial-target swing scanner verified across all fractions.\n")


# ------------------------------------------------------------------ MAIN ---
def main():
    run_synthetic_smoke_test()
    market_hours_guard()
    api = get_api()
    print(f"SCOUT54 - 369 Ladder: PARTIAL target swing completion rate (25%/50%/75%/100% of the way to the next "
          f"line) - {datetime.datetime.now().isoformat()}")
    print("User's request: set the target at 50% of the distance to the next line instead of the full line - how")
    print("often does THAT get reached before price reverses back through the original crossed line? 25%/75%/100%")
    print("included alongside for the full trade-off picture, using every real historical cross.\n")

    for name, inst in SVMKR_INSTRUMENTS.items():
        for tf_label, interval, days_back, chunk_days in TIMEFRAMES:
            print(f"\n--- {name} / {tf_label} (base_unit={inst['base_unit']}) ---")
            bars = get_index_candles(api, inst["exchange"], inst["token"], interval, days_back, chunk_days)
            if len(bars) < 2000:
                print(f"  SKIP: only {len(bars)} bars - not enough for a meaningful multi-week sample.")
                continue
            days_sorted = sorted({dt.date() for dt, o, h, l, c in bars})
            print(f"  {len(bars)} real bars across {len(days_sorted)} days, {days_sorted[0]} -> {days_sorted[-1]}")

            for frac in TARGET_FRACTIONS:
                rows = scan_partial_target_swings(bars, inst["base_unit"], frac)
                tag = "<<< USER'S 50% TARGET" if frac == 0.5 else ""
                print(f"  target={frac*100:.0f}% of next-line distance {tag}")
                summarize(rows, f"{name} {tf_label} {frac}")

    print("\nDone. READ-ONLY, descriptive scan only - nothing modified, no orders placed, no strategy proposed here.")
    print("HOW TO READ THIS: look at the 50% row for each index/timeframe. If SUCCESS is comfortably above 50%")
    print("AND train/test are CONSISTENT (close to each other), a half-distance target is a real, repeatable")
    print("tendency worth designing a trade around. If it is still below 50%, or train/test diverge, halving the")
    print("target did not fix the underlying problem scout53 found - it only makes losing swings resolve faster.")


if __name__ == "__main__":
    main()
