#!/usr/bin/env python3
# ============================================================================
# SCOUT52 - REAL BACKTEST: "check every angle" on the 369 ladder's ENTRY
# ------------------------------------------------------------------------
# WHY THIS SCOUT EXISTS (2026-09-17, same day as scout51):
# User manually traded today watching the live v4 369-ladder PE lines on
# TradingView (chart + Angel order book shared as screenshots) and made a
# real profit (2 trades, ~Rs 34,043 combined - NIFTY 23300 CALL +Rs28,701,
# NIFTY 23500 PUT +Rs5,343). He asked if this proves the setup is ROBUST.
#
# Claude's honest pushback (given, not hidden here): 2 manually-picked
# trades in one day is NOT statistically meaningful next to scout51's
# 900-1300+ mechanical trades/index that came back 0/6 NOT ROBUST. Also,
# the two real trades captured very SMALL point moves (2.52 and 0.81
# points in premium) - quick scalps, not the "let a strong trend run"
# behaviour the strategy is meant to test. And a human choosing 2
# "obviously good-looking" setups off a chart is a fundamentally
# different (higher information / heavily filtered) process than a
# mechanical script taking EVERY signal the rule defines - so a good
# manual day does not validate the mechanical system.
#
# User's response: test BOTH axes together, "ella angle-layum check
# pannunga" (check every angle):
#   AXIS 1 - ENTRY TIMING:
#     CROSS_CONFIRM  = the exact live v4 Pine rule (unchanged from
#                       scout51): a CROSS candle closes beyond a line,
#                       then a separate CONFIRM candle must close beyond
#                       it AND be fully clear (no wick touching) before
#                       entry fires, at the confirm candle's own close.
#     CROSS_IMMEDIATE = a new, simpler variant: entry fires the MOMENT a
#                       candle closes beyond a line (the "cross" bar
#                       itself), no confirm wait at all. This is closer
#                       to what the user described as his own mental
#                       model of the rule ("price line-ah touch pannumbodhu
#                       entry panrom").
#   AXIS 2 - HOW THE OUTCOME IS MEASURED:
#     SL_TIERS   = exactly scout51's exit engine: fixed 2% hard SL +
#                  STRONG_TREND_TIERS_BY_SYMBOL trailing lock + the daily
#                  2-HARD_SL-hit/index cap. Answers "how did OUR exit
#                  rule perform".
#     DIAGNOSTIC = NO exit rule injected at all. The position is simply
#                  held until the ladder's own natural weekly reset
#                  (matches v4's own week-rollover flatten), and for
#                  every trade we record MFE (best favourable move that
#                  ever happened, in points) and MAE (worst adverse move
#                  that ever happened, in points) along the way, plus the
#                  points value AT the natural weekly close. This isolates
#                  the RAW predictive value of the entry itself, with zero
#                  interference from any SL/target choice - directly
#                  answers "line cross panni eppadi trade aagudhu" without
#                  entangling it with an exit method that might be hiding
#                  or exaggerating the entry's real quality.
#
# All 4 combinations (2 entry timings x 2 measurement modes) are run for
# all 3 indices x both timeframes = 24 result rows, using the SAME real
# candle data fetched once per (index, timeframe) - not re-fetched per
# mode, to keep the Angel API load reasonable.
#
# EVERYTHING ELSE IS UNCHANGED FROM SCOUT51, COPIED VERBATIM WHERE
# POSSIBLE (fetch helpers, tier-trail helpers, ladder construction,
# 70/30-by-week split, fill-realism-on-gap rule, real lot-size lookup,
# SENSEX token, STRONG_TREND_TIERS_BY_SYMBOL + 2% SL re-verified fresh via
# AST-parse of the live app.py again today, separately from scout51's own
# earlier verification this same day - values matched, unchanged).
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
CACHE_DIR = "/tmp/scout52_cache"
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


# Real tokens verified against the live app.py source (never guessed):
SVMKR_INSTRUMENTS = {
    "NIFTY":     {"exchange": "NSE", "token": "99926000", "base_unit": 30.0},
    "BANKNIFTY": {"exchange": "NSE", "token": "99926009", "base_unit": 300.0},
    "SENSEX":    {"exchange": "BSE", "token": "99919000", "base_unit": 300.0},
}

# --- Ladder constants (verbatim from magic369_ladder_alerts_v4.pine) ---
MULT = [1.0, 2.0, 3.0, 4.0, 6.0, 9.0, 10.0, 12.0, 15.0]
NM = len(MULT)
NL = 2 * NM + 1
BASE_IDX = NM
INCLUDE_BASE = True
ENTRIES_PER_LINE = 2

# --- Exit constants (re-verified fresh via AST-parse of live app.py, 2026-09-17) ---
SL_PERCENT = 0.02
STRONG_TREND_TIERS_BY_SYMBOL = {
    "NIFTY":     [(10, 4), (40, 16), (70, 28), (120, 42), (200, 60), (300, 80), (500, 120), (1000, 180)],
    "BANKNIFTY": [(24, 10), (100, 35), (150, 50), (200, 65), (300, 85), (400, 105), (600, 140), (1000, 200)],
    "SENSEX":    [(24, 10), (100, 35), (150, 50), (200, 65), (300, 85), (400, 105), (600, 140), (1000, 200)],
}

DAILY_SL_CAP = 2

ENTRY_MODES = ["CROSS_CONFIRM", "CROSS_IMMEDIATE"]
MEASURE_MODES = ["SL_TIERS", "DIAGNOSTIC"]

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


def _expiry_key(r):
    try:
        return datetime.datetime.strptime(r.get("expiry", ""), "%d%b%Y")
    except Exception:
        return datetime.datetime.max


def resolve_fut_lotsize(instruments, name):
    rows = [r for r in instruments if r.get("exch_seg") == "NFO"
            and r.get("instrumenttype") == "FUTIDX" and r.get("expiry")
            and str(r.get("name", "")).upper() == name]
    if not rows:
        print(f"  WARNING: {name}: no FUTIDX contract found in instrument master - cannot confirm a real lot size.")
        return None
    rows.sort(key=_expiry_key)
    nearest = rows[0]
    try:
        lot = int(float(nearest.get("lotsize") or 0))
    except Exception:
        lot = 0
    if lot <= 0:
        print(f"  WARNING: {name}: resolved FUTIDX contract {nearest.get('symbol')} has no usable lotsize ({nearest.get('lotsize')!r}).")
        return None
    print(f"  {name}: real lot size confirmed from {nearest.get('symbol')} (expiry {nearest.get('expiry')}) = {lot}")
    return lot


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


# --------------------------------------------------------- TIER HELPERS ----
# Copied verbatim from scout49/50/51 - not re-derived. Only used when
# measure_mode == "SL_TIERS".
def _trail_stop_long(entry_price, peak_price, sl_pct, tiers):
    profit_points = round(peak_price - entry_price, 2)
    trail_back = None
    for threshold, tb in tiers:
        if profit_points >= threshold:
            trail_back = tb
    hard_floor = entry_price * (1 - sl_pct)
    if trail_back is None:
        return hard_floor, False
    return max(hard_floor, peak_price - trail_back), True


def _trail_stop_short(entry_price, trough_price, sl_pct, tiers):
    profit_points = round(entry_price - trough_price, 2)
    trail_back = None
    for threshold, tb in tiers:
        if profit_points >= threshold:
            trail_back = tb
    hard_ceiling = entry_price * (1 + sl_pct)
    if trail_back is None:
        return hard_ceiling, False
    return min(hard_ceiling, trough_price + trail_back), True


# ------------------------------------------------------ LADDER HELPERS -----
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


# ------------------------------------------------------------ BACKTEST -----
class LadderInstance:
    """One CE or PE instance's state. `extreme` = running best-case
    excursion (peak high for CE / trough low for PE), used both by the
    SL_TIERS trailing-stop math and to report MFE. `adverse` = running
    worst-case excursion (opposite direction), used only to report MAE -
    it never feeds into any exit decision."""
    def __init__(self, is_ce):
        self.is_ce = is_ce
        self.pend_idx = None
        self.in_position = False
        self.entry_price = None
        self.entry_i = None
        self.entry_day = None
        self.extreme = None
        self.adverse = None
        self.used_count = [0] * NL


def run_variant_369(bars, symbol, base_unit, entry_mode, measure_mode, sl_pct, tiers, entries_per_line, daily_sl_cap):
    assert entry_mode in ENTRY_MODES, entry_mode
    assert measure_mode in MEASURE_MODES, measure_mode
    base_for_week = build_weekly_bases(bars)
    # trade tuple: (entry_day, pts, hold_bars, exit_reason, option_type, mfe_pts, mae_pts, entry_price)
    trades = []
    skipped_by_cap = 0

    ce = LadderInstance(is_ce=True)
    pe = LadderInstance(is_ce=False)

    cur_week = None
    levels = None
    cur_day = None
    daily_sl_count = 0
    blocked_today = False

    def mfe_mae(inst):
        # Floored at 0: if price never actually moved favourably (or never
        # actually moved adversely) relative to entry, that excursion is 0,
        # not negative - a negative raw value here just means the running
        # extreme/adverse tracker never crossed to the "wrong" side of entry.
        if inst.is_ce:
            mfe = (inst.extreme - inst.entry_price) if inst.extreme is not None else 0.0
            mae = (inst.entry_price - inst.adverse) if inst.adverse is not None else 0.0
        else:
            mfe = (inst.entry_price - inst.extreme) if inst.extreme is not None else 0.0
            mae = (inst.adverse - inst.entry_price) if inst.adverse is not None else 0.0
        return round(max(0.0, mfe), 2), round(max(0.0, mae), 2)

    def close_position(inst, i, fill, reason, cur_i):
        nonlocal daily_sl_count
        pts = (fill - inst.entry_price) if inst.is_ce else (inst.entry_price - fill)
        mfe, mae = mfe_mae(inst)
        opt = "CE" if inst.is_ce else "PE"
        trades.append((inst.entry_day, pts, cur_i - inst.entry_i, reason, opt, mfe, mae, inst.entry_price))
        if reason == "HARD_SL":
            daily_sl_count += 1
        inst.in_position = False
        inst.entry_price = None
        inst.entry_i = None
        inst.entry_day = None
        inst.extreme = None
        inst.adverse = None

    def do_entry(inst, cross_idx, i, c, day):
        inst.used_count[cross_idx] += 1
        inst.in_position = True
        inst.entry_price = c
        inst.entry_i = i
        inst.entry_day = day
        inst.extreme = None
        inst.adverse = None

    for i, (dt, o, h, l, c) in enumerate(bars):
        prev_c = bars[i - 1][4] if i > 0 else None
        wk = week_key(dt)
        day = dt.date()

        new_week = wk != cur_week
        if new_week:
            cur_week = wk
            base_price = base_for_week.get(wk)
            levels = build_ladder(base_price, base_unit) if base_price is not None else None
            ce.used_count = [0] * NL
            pe.used_count = [0] * NL
            for inst in (ce, pe):
                if inst.in_position:
                    close_position(inst, i, c, "WEEK_ROLLOVER", i)
            ce.pend_idx = None
            pe.pend_idx = None

        if new_day := (day != cur_day):
            cur_day = day
            daily_sl_count = 0
            blocked_today = False

        if levels is None or prev_c is None:
            continue

        blocked_today = daily_sl_count >= daily_sl_cap

        # ---- 1) update running extreme/adverse for any OPEN position (always, both modes) ----
        for inst in (ce, pe):
            if not inst.in_position:
                continue
            if inst.is_ce:
                inst.extreme = h if inst.extreme is None else max(inst.extreme, h)
                inst.adverse = l if inst.adverse is None else min(inst.adverse, l)
            else:
                inst.extreme = l if inst.extreme is None else min(inst.extreme, l)
                inst.adverse = h if inst.adverse is None else max(inst.adverse, h)

        # ---- 2) EXITS - only when measure_mode == SL_TIERS ----
        if measure_mode == "SL_TIERS":
            for inst in (ce, pe):
                if not inst.in_position:
                    continue
                if inst.is_ce:
                    stop_level, tiered = _trail_stop_long(inst.entry_price, inst.extreme, sl_pct, tiers)
                    if l <= stop_level:
                        fill = o if o <= stop_level else stop_level
                        close_position(inst, i, fill, "TIER_STOP" if tiered else "HARD_SL", i)
                else:
                    stop_level, tiered = _trail_stop_short(inst.entry_price, inst.extreme, sl_pct, tiers)
                    if h >= stop_level:
                        fill = o if o >= stop_level else stop_level
                        close_position(inst, i, fill, "TIER_STOP" if tiered else "HARD_SL", i)
        # measure_mode == "DIAGNOSTIC": no exit check at all here - the position
        # only ever closes via WEEK_ROLLOVER or END_OF_DATA below, so MFE/MAE
        # accumulate over its full natural life with zero exit interference.

        # ---- 3) CROSS detection (outer line wins), same for both entry modes ----
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

        # ---- 4) ENTRY per instance - entry_mode governs how ----
        for inst, cross_idx in ((ce, ce_cross_idx), (pe, pe_cross_idx)):
            if inst.in_position or blocked_today:
                continue

            if entry_mode == "CROSS_IMMEDIATE":
                # Enter the instant a candle closes beyond a line - no confirm wait.
                if cross_idx is not None and inst.used_count[cross_idx] < entries_per_line:
                    do_entry(inst, cross_idx, i, c, day)
                continue

            # entry_mode == "CROSS_CONFIRM" - verbatim v4 Pine state machine (unchanged from scout51)
            if inst.pend_idx is not None:
                p_idx = inst.pend_idx
                p_lvl = levels[p_idx]
                confirmed = (c > p_lvl) if inst.is_ce else (c < p_lvl)
                jumped_more = cross_idx is not None and ((cross_idx > p_idx) if inst.is_ce else (cross_idx < p_idx))
                clear_of_line = (l > p_lvl) if inst.is_ce else (h < p_lvl)
                has_target = (p_idx < NL - 1) if inst.is_ce else (p_idx > 0)
                if jumped_more:
                    inst.pend_idx = cross_idx
                elif confirmed and clear_of_line:
                    if has_target and inst.used_count[p_idx] < entries_per_line:
                        do_entry(inst, p_idx, i, c, day)
                    inst.pend_idx = None
                elif confirmed:
                    pass
                else:
                    inst.pend_idx = None
            elif cross_idx is not None:
                inst.pend_idx = cross_idx

        if blocked_today and (ce_cross_idx is not None or pe_cross_idx is not None):
            skipped_by_cap += 1

    if bars:
        last_i = len(bars) - 1
        last_c = bars[-1][4]
        for inst in (ce, pe):
            if inst.in_position:
                close_position(inst, last_i, last_c, "END_OF_DATA", last_i)

    return trades, skipped_by_cap


def split_weeks(trades, frac=0.7):
    weeks_sorted = sorted({week_key(datetime.datetime.combine(t[0], datetime.time())) for t in trades})
    s = int(len(weeks_sorted) * frac)
    return set(weeks_sorted[:s]), set(weeks_sorted[s:])


def verdict_of(tr, te, tr_avg, te_avg):
    if not tr or not te:
        return "UNEVALUABLE"
    if tr_avg > 0 and te_avg > 0 and te_avg >= 0.3 * tr_avg:
        return "ROBUST"
    return "NOT ROBUST"


def run_one(name, tf_label, bars, lotsize, base_unit, tiers, entry_mode, measure_mode):
    trades, skipped_by_cap = run_variant_369(bars, name, base_unit, entry_mode, measure_mode, SL_PERCENT, tiers,
                                              ENTRIES_PER_LINE, DAILY_SL_CAP)
    mode_label = f"{entry_mode}/{measure_mode}"
    if not trades:
        return {"index": name, "tf": tf_label, "mode": mode_label, "train_n": 0, "train_avg": 0.0, "test_n": 0,
                "test_avg": 0.0, "trades_per_week": 0.0, "avg_hold_bars": 0.0, "verdict": "UNEVALUABLE",
                "rupee_note": "no trades fired", "exit_mix": "n/a", "skipped_by_cap": skipped_by_cap,
                "avg_mfe": 0.0, "avg_mae": 0.0, "pct_mfe_tier1": 0.0, "pct_mae_2pct": 0.0}

    def wk_of(d):
        return week_key(datetime.datetime.combine(d, datetime.time()))

    train_weeks, test_weeks = split_weeks(trades, frac=0.7)
    tr = [t for t in trades if wk_of(t[0]) in train_weeks]
    te = [t for t in trades if wk_of(t[0]) in test_weeks]
    tr_pts = [t[1] for t in tr]
    te_pts = [t[1] for t in te]
    tra = sum(tr_pts) / len(tr_pts) if tr_pts else 0.0
    tea = sum(te_pts) / len(te_pts) if te_pts else 0.0
    hold = sum(t[2] for t in trades) / len(trades)
    all_weeks = train_weeks | test_weeks
    per_week = len(trades) / len(all_weeks) if all_weeks else 0.0
    n_hardsl = sum(1 for t in trades if t[3] == "HARD_SL")
    n_tier = sum(1 for t in trades if t[3] == "TIER_STOP")
    n_rollover = sum(1 for t in trades if t[3] == "WEEK_ROLLOVER")
    n_eod = sum(1 for t in trades if t[3] == "END_OF_DATA")
    n_ce = sum(1 for t in trades if t[4] == "CE")
    n_pe = sum(1 for t in trades if t[4] == "PE")
    verdict = verdict_of(tr_pts, te_pts, tra, tea)
    rupee_note = f"{tra * lotsize:+.0f} / {tea * lotsize:+.0f} Rs per trade (train/test, x{lotsize} lot)" if lotsize else "lot size unresolved - points only"

    avg_mfe = sum(t[5] for t in trades) / len(trades)
    avg_mae = sum(t[6] for t in trades) / len(trades)
    tier1_threshold = tiers[0][0] if tiers else None
    pct_mfe_tier1 = (sum(1 for t in trades if tier1_threshold is not None and t[5] >= tier1_threshold)
                      / len(trades) * 100) if tier1_threshold else 0.0
    pct_mae_2pct = sum(1 for t in trades if t[6] >= (t[7] * SL_PERCENT)) / len(trades) * 100

    return {
        "index": name, "tf": tf_label, "mode": mode_label, "train_n": len(tr), "train_avg": tra,
        "test_n": len(te), "test_avg": tea, "trades_per_week": per_week,
        "avg_hold_bars": hold, "verdict": verdict, "rupee_note": rupee_note,
        "exit_mix": f"SL={n_hardsl} TIER={n_tier} ROLLOVER={n_rollover} EOD={n_eod} (CE={n_ce}/PE={n_pe})",
        "skipped_by_cap": skipped_by_cap,
        "avg_mfe": avg_mfe, "avg_mae": avg_mae,
        "pct_mfe_tier1": pct_mfe_tier1, "pct_mae_2pct": pct_mae_2pct,
    }


def find_big_jumps(bars, threshold_pct=8.0):
    days = {}
    for dt, o, h, l, c in bars:
        days[dt.date()] = c
    days_sorted = sorted(days.keys())
    jumps = []
    prev = None
    for d in days_sorted:
        if prev is not None and prev != 0:
            pct = (days[d] - prev) / prev * 100
            if abs(pct) >= threshold_pct:
                jumps.append((d, pct))
        prev = days[d]
    return jumps


# ------------------------------------------------------------ SMOKE TEST ---
def _make_smoke_bars():
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
    return bars


def run_synthetic_smoke_test():
    bars = _make_smoke_bars()

    # --- Regression check: CROSS_CONFIRM + SL_TIERS must reproduce scout51's
    # own verified smoke-test result exactly (same bars, same engine logic) ---
    trades, skipped = run_variant_369(bars, "NIFTY", 30.0, "CROSS_CONFIRM", "SL_TIERS", SL_PERCENT,
                                       STRONG_TREND_TIERS_BY_SYMBOL["NIFTY"], ENTRIES_PER_LINE, DAILY_SL_CAP)
    assert len(trades) == 323, f"REGRESSION FAILED vs scout51: expected 323 trades, got {len(trades)}"
    avg = sum(t[1] for t in trades) / len(trades)
    assert abs(avg - 11.43) < 0.01, f"REGRESSION FAILED vs scout51: expected avg +11.43, got {avg:+.2f}"
    reasons = {}
    for t in trades:
        reasons[t[3]] = reasons.get(t[3], 0) + 1
    assert reasons.get("TIER_STOP") == 319 and reasons.get("HARD_SL") == 4, \
        f"REGRESSION FAILED vs scout51: expected TIER_STOP=319/HARD_SL=4, got {reasons}"
    print(f"  [regression] CROSS_CONFIRM+SL_TIERS matches scout51 exactly: {len(trades)} trades, avg {avg:+.2f}, {reasons}")

    # --- New engine paths must also fire trades and produce sane MFE/MAE ---
    for entry_mode in ENTRY_MODES:
        for measure_mode in MEASURE_MODES:
            trades, skipped = run_variant_369(bars, "NIFTY", 30.0, entry_mode, measure_mode, SL_PERCENT,
                                               STRONG_TREND_TIERS_BY_SYMBOL["NIFTY"], ENTRIES_PER_LINE, DAILY_SL_CAP)
            assert trades, f"SMOKE TEST FAILED: {entry_mode}/{measure_mode} fired zero trades"
            for t in trades:
                mfe, mae = t[5], t[6]
                assert mfe >= 0.0, f"{entry_mode}/{measure_mode}: negative MFE {mfe} - excursion math is wrong"
                assert mae >= 0.0, f"{entry_mode}/{measure_mode}: negative MAE {mae} - excursion math is wrong"
            if measure_mode == "DIAGNOSTIC":
                reasons = {}
                for t in trades:
                    reasons[t[3]] = reasons.get(t[3], 0) + 1
                assert "HARD_SL" not in reasons and "TIER_STOP" not in reasons, \
                    f"{entry_mode}/DIAGNOSTIC: an SL/TIER exit fired even though no exit rule should exist - {reasons}"
            avg_mfe = sum(t[5] for t in trades) / len(trades)
            avg_mae = sum(t[6] for t in trades) / len(trades)
            print(f"  [{entry_mode}/{measure_mode}] {len(trades)} trades, avg_mfe={avg_mfe:.1f} avg_mae={avg_mae:.1f}, skipped_by_cap={skipped}")

    # CROSS_IMMEDIATE must fire at least as many (usually more) raw entries per
    # line than CROSS_CONFIRM on the same data, since it skips the confirm filter.
    tr_confirm, _ = run_variant_369(bars, "NIFTY", 30.0, "CROSS_CONFIRM", "DIAGNOSTIC", SL_PERCENT,
                                     STRONG_TREND_TIERS_BY_SYMBOL["NIFTY"], ENTRIES_PER_LINE, DAILY_SL_CAP)
    tr_immediate, _ = run_variant_369(bars, "NIFTY", 30.0, "CROSS_IMMEDIATE", "DIAGNOSTIC", SL_PERCENT,
                                       STRONG_TREND_TIERS_BY_SYMBOL["NIFTY"], ENTRIES_PER_LINE, DAILY_SL_CAP)
    assert len(tr_immediate) >= len(tr_confirm), \
        f"SANITY FAILED: CROSS_IMMEDIATE ({len(tr_immediate)}) fired fewer trades than CROSS_CONFIRM ({len(tr_confirm)}) - confirm filter should only ever reduce entries, never add them"
    print(f"  [sanity] CROSS_IMMEDIATE ({len(tr_immediate)} trades) >= CROSS_CONFIRM ({len(tr_confirm)} trades) on the same data - OK")

    print("SMOKE TEST OK - all 4 entry/measure combinations verified, regression-matched against scout51.\n")


# ------------------------------------------------------------------ MAIN ---
def main():
    run_synthetic_smoke_test()
    market_hours_guard()
    api = get_api()
    print(f"SCOUT52 - 369 Ladder: Cross-Immediate vs Cross+Confirm entry, x Diagnostic (no-exit MFE/MAE) vs "
          f"2% SL+Strong-Tiers backtest - {datetime.datetime.now().isoformat()}")
    print("Checking every angle per your request: 2 entry timings x 2 measurement modes x 3 indices x 2 timeframes = 24 rows.\n")

    print("Fetching real instrument master for lot-size confirmation...")
    try:
        instruments = _load_scrip_master()
    except Exception as e:
        print(f"  WARNING: could not download instrument master ({e}) - lot sizes will be unresolved, points-only report.")
        instruments = []

    lotsizes = {}
    for name in SVMKR_INSTRUMENTS:
        lotsizes[name] = resolve_fut_lotsize(instruments, name)

    all_results = []
    for name, inst in SVMKR_INSTRUMENTS.items():
        tiers = STRONG_TREND_TIERS_BY_SYMBOL[name]
        for tf_label, interval, days_back, chunk_days in TIMEFRAMES:
            print(f"\n--- {name} / {tf_label} (base_unit={inst['base_unit']}) ---")
            bars = get_index_candles(api, inst["exchange"], inst["token"], interval, days_back, chunk_days)
            if len(bars) < 2000:
                print(f"  SKIP: only {len(bars)} bars - not enough for a meaningful multi-week sample.")
                continue
            days_sorted = sorted({dt.date() for dt, o, h, l, c in bars})
            print(f"  {len(bars)} real bars across {len(days_sorted)} days, {days_sorted[0]} -> {days_sorted[-1]}")
            jumps = find_big_jumps(bars)
            if jumps:
                print(f"  data-quality note: {len(jumps)} single-day close-to-close move(s) >=8% (reported, nothing excluded).")

            for entry_mode in ENTRY_MODES:
                for measure_mode in MEASURE_MODES:
                    all_results.append(run_one(name, tf_label, bars, lotsizes.get(name), inst["base_unit"], tiers,
                                                entry_mode, measure_mode))

    def print_table(title, rows):
        print("\n" + "=" * 150)
        print(title)
        print("=" * 150)
        print(f"{'index':<10} {'tf':<7} {'entry':<15} {'trN':>4} {'train_pts':>10} {'teN':>4} {'test_pts':>9} "
              f"{'tr/wk':>6} {'hold':>6} {'verdict':<10} {'avg_mfe':>8} {'avg_mae':>8} {'%mfe>=t1':>9} {'%mae>=2%':>9}  exit_mix")
        print("-" * 150)
        robust = []
        for r in rows:
            entry_only = r["mode"].split("/")[0]
            print(f"{r['index']:<10} {r['tf']:<7} {entry_only:<15} {r['train_n']:>4} {r['train_avg']:>10.2f} "
                  f"{r['test_n']:>4} {r['test_avg']:>9.2f} {r['trades_per_week']:>6.2f} {r['avg_hold_bars']:>6.1f} "
                  f"{r['verdict']:<10} {r['avg_mfe']:>8.1f} {r['avg_mae']:>8.1f} {r['pct_mfe_tier1']:>8.1f}% {r['pct_mae_2pct']:>8.1f}%  "
                  f"{r['exit_mix']}   [{r['rupee_note']}]   (skipped-by-cap: {r['skipped_by_cap']})")
            if r["verdict"] == "ROBUST":
                robust.append(r)
        print(f"\n{title}: {len(robust)}/{len(rows)} combos ROBUST.")
        return robust

    sl_tiers_rows = [r for r in all_results if r["mode"].endswith("/SL_TIERS")]
    diagnostic_rows = [r for r in all_results if r["mode"].endswith("/DIAGNOSTIC")]

    all_robust = []
    all_robust += print_table("TABLE A - WITH OUR 2% SL + STRONG PROFIT TIERS EXIT (matches scout51's engine, now also testing CROSS_IMMEDIATE entry)", sl_tiers_rows)
    all_robust += print_table("TABLE B - DIAGNOSTIC: NO EXIT RULE AT ALL, held to natural week-end, pure entry follow-through (MFE/MAE)", diagnostic_rows)

    print("\n" + "=" * 150)
    print(f"GRAND TOTAL: {len(all_robust)}/{len(all_results)} of all 24 combos ROBUST.")
    for r in sorted(all_robust, key=lambda x: -(x["train_avg"] + x["test_avg"])):
        print(f"  {r['index']} / {r['tf']} / {r['mode']} -> train {r['train_avg']:+.2f} pts (N={r['train_n']}), "
              f"test {r['test_avg']:+.2f} pts (N={r['test_n']}), {r['trades_per_week']:.2f} trades/week, "
              f"avg_mfe={r['avg_mfe']:.1f} avg_mae={r['avg_mae']:.1f}, {r['rupee_note']}")

    print("\nDone. READ-ONLY run complete - nothing modified, no orders placed.")
    print("HOW TO READ THIS REPORT:")
    print("  Table A tells you: 'if the script itself managed the exit (2% SL + strong tiers), would this be profitable'.")
    print("  Table B tells you: 'regardless of any exit rule, does crossing a 369 ladder line have any raw predictive")
    print("  edge at all' - avg_mfe/avg_mae show how far price typically ran favourably/adversely after each entry,")
    print("  and %mfe>=t1 / %mae>=2% show how often a real profit-tier / real hard-SL distance was ever reached.")
    print("  CROSS_IMMEDIATE vs CROSS_CONFIRM within each table isolates whether waiting for the live v4 confirm")
    print("  candle helps, hurts, or makes no difference versus reacting the instant a line is crossed.")


if __name__ == "__main__":
    main()
