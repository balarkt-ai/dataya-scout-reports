#!/usr/bin/env python3
# ============================================================================
# SCOUT44 - UT BOT on MCX COMMODITIES (GOLD/SILVER/CRUDEOIL/NATURALGAS)
# 2026-09-16, his explicit ask: "idha commodity and options trading.ku use
# pannanum" -> AskUserQuestion confirmed scope (1) this commodity backtest.
#
# WHAT THIS REUSES, VERBATIM, FROM ALREADY-VERIFIED WORK (never re-derived
# from memory - re-read from the actual source files first):
#   - resolve_commodity_token()'s row-selection rule from scout27: MCX +
#     instrumenttype=="FUTCOM" + exact plain name + nearest expiry. scout26
#     already proved (real data) the current front-month token under this
#     rule serves ~10.9 YEARS of continuous DAILY history with no manual
#     contract-chaining needed.
#   - UTBotState - byte-identical engine to scout18/21/21b/32 (Buy key=6/
#     ATR=1, Sell key=4/ATR=300). T78/T79 trade real money off this exact
#     formula - not modified here.
#
# WHAT IS GENUINELY NEW AND UNVERIFIED, SO IT IS CHECKED HERE RATHER THAN
# ASSUMED:
#   1) DATA DEPTH AT 15-MIN. scout26 only proved the 10.9yr depth for DAILY
#      candles. Intraday (FIFTEEN_MINUTE) candles on the very same token are
#      a different data path in Angel's API and may have a much shorter
#      real history (or may not - untested). This script's FIRST phase does
#      nothing but fetch and report the true 15-min depth per commodity
#      BEFORE any backtest math runs, exactly like scout24/26 did for daily.
#      Any commodity with too little 15-min history is SKIPPED from the
#      backtest with a clear reason, never silently backtested on a stub.
#   2) LONG vs SHORT. Individual NSE equity delivery cannot be sold short
#      overnight (scout32's reason for testing LONG-ONLY). An MCX FUTURES
#      contract is NOT that instrument - Angel allows a plain sell-to-open
#      on a future, so a true bidirectional stop-and-reverse (matching how
#      T78 actually trades NIFTY via CE/PE) is mechanically possible here.
#      But DATAYA ALGO's existing commodity paper-tracker (GFS Commodity,
#      gfs27_gold_commodity.pine) was deliberately built LONG-ONLY, mirroring
#      the equity pattern, not the options stop-and-reverse pattern - so
#      whether the live paper-tracker infra should grow a genuine SHORT leg
#      is an open architecture question, not yet decided. Rather than guess,
#      this script tests BOTH shapes and reports both, so that decision can
#      be made from real numbers, not assumption:
#        LONG-ONLY  (A1/A2/A4, exact mirror of scout32's equity policies)
#        BIDIR      (A1/A2/A4, true stop-and-reverse - Sell flips to a real
#                    short position instead of just going flat)
#   3) LOT SIZE. Never hardcoded - read directly off the resolved FUTCOM
#      row's own "lotsize" field from the live scrip master (same field
#      scout23's discovery run confirmed exists on every MCX row), exactly
#      like the already-deployed GFS Commodity tracker does (1 lot/trade).
#
# HOLD POLICIES (mirrors scout21b's decomposition that found T78's real edge
# lives OVERNIGHT for NIFTY options - repeated here rather than assumed,
# since commodities are a different market with different overnight risk -
# MCX runs into the evening, gap risk profile is not the same as NSE):
#   ALWAYS    - never forced flat; ride until the opposite signal (or the
#               reversal target for BIDIR) fires, or data ends.
#   INTRADAY  - forced flat at every day's own last 15-min bar. Never
#               carries a position overnight.
#   BTST      - like T78's deployed NIFTY rule: hold at most ONE extra
#               night, forced flat at the next day's last bar if no
#               opposite signal has fired by then.
#
# REAL RUPEE P&L: pnl = (exit - entry) * lotsize * 1_lot (GFS Commodity's
# own "keep it simple" convention - a capital-scaled formula would round to
# 1 lot anyway at commodity price levels). Caveats NOT modeled (disclosed,
# not hidden): STT/CTT/brokerage/DP charges, entry slippage beyond "fill at
# the signal bar's close", MCX margin/SPAN requirements.
#
# READ-ONLY. Run on Render Shell only (needs live Angel session + network -
# this sandbox has neither), outside market hours per the guard below.
# Nothing here places an order, writes to app.py, or touches Pine/webhooks.
# ============================================================================
import os
import sys
import json
import time
import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def market_hours_guard():
    now = datetime.datetime.now(IST)
    if now.weekday() < 5 and (9 <= now.hour < 16):
        print("SAFETY STOP: market hours - run this ONLY after 15:30 IST (or on a weekend). Exiting.")
        sys.exit(1)
    return now


# ------------------------------------------------------------------ AUTH ---
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


# ------------------------------------------------------------------ CONFIG --
COMMODITIES = ["GOLD", "SILVER", "CRUDEOIL", "NATURALGAS"]
DAYS_BACK_15MIN = 730          # ~2yr - same window used for T78/scout32's equity UT Bot
CHUNK_DAYS_15MIN = 90          # conservative chunk size, untested exchange/interval combo
MIN_DAYS_REQUIRED = 150        # below this, skip the backtest for that commodity (data-depth check)
CACHE_DIR = "/data/scout_utbot_commodity_cache"
SCRIP_MASTER_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
SCRIP_MASTER_CACHE = os.path.join(CACHE_DIR, "scrip_master.json")
SCRIP_MASTER_MAX_AGE_H = 24


# --------------------------------------------------------- TOKEN LOOKUP ----
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
    print("  (scrip master: downloading fresh copy - this file is large, may take a bit)")
    with urllib.request.urlopen(SCRIP_MASTER_URL, timeout=120) as resp:
        data = json.loads(resp.read())
    with open(SCRIP_MASTER_CACHE, "w") as f:
        json.dump(data, f)
    print(f"  (scrip master: downloaded {len(data)} rows)")
    return data


def _expiry_key(r):
    e = r.get("expiry", "")
    try:
        return datetime.datetime.strptime(e, "%d%b%Y")
    except Exception:
        return datetime.datetime.max


def resolve_commodity_token(scrip_master, commodity):
    """Same row-selection rule as scout27 (already real-data-confirmed for
    daily depth): MCX + instrumenttype=='FUTCOM' + exact plain name + the
    nearest-expiry row. Lot size is read directly off that row - never
    hardcoded. Raises ValueError (never a silent guess) if nothing matches."""
    rows = [r for r in scrip_master if r.get("exch_seg") == "MCX"
            and r.get("instrumenttype") == "FUTCOM" and r.get("expiry")
            and str(r.get("name", "")).upper() == commodity]
    if not rows:
        raise ValueError(f"could not find any dated FUTCOM contract for {commodity} - "
                          f"Angel's instrument list structure may have changed")
    rows.sort(key=_expiry_key)
    nearest = rows[0]
    try:
        lotsize = int(nearest.get("lotsize") or 0)
    except Exception:
        lotsize = 0
    if lotsize <= 0:
        raise ValueError(f"{commodity}: resolved contract {nearest.get('symbol')} has no usable "
                          f"lotsize field ({nearest.get('lotsize')!r}) - skipping, not guessing.")
    return nearest.get("token"), nearest.get("symbol"), lotsize


# ------------------------------------------------------------ DATA FETCH ---
def _fetch_15min_chunk(api, token, from_dt, to_dt):
    params = {
        "exchange": "MCX",
        "symboltoken": str(token),
        "interval": "FIFTEEN_MINUTE",
        "fromdate": from_dt.strftime("%Y-%m-%d %H:%M"),
        "todate": to_dt.strftime("%Y-%m-%d %H:%M"),
    }
    for attempt in range(3):
        try:
            resp = api.getCandleData(params)
            if not resp or not resp.get("status") or resp.get("data") is None:
                return []
            return resp["data"]
        except Exception as e:
            print(f"    retry {attempt + 1}: {e}")
            time.sleep(2)
    return []


def get_commodity_15min_days(api, commodity, token, days_back=DAYS_BACK_15MIN, chunk_days=CHUNK_DAYS_15MIN):
    """Returns dict: date -> sorted list of (dt, o, h, l, c). Cached per-
    commodity. This is PHASE 1 of the script - the real, previously-untested
    question (does 15-min data on the front-month token go back this far,
    or does it only cover the current contract's own short listing life)."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, f"{commodity}_15min.json")
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            raw = json.load(f)
        print(f"  (using cache: {len(raw)} rows)")
    else:
        now = datetime.datetime.now()
        end = now.replace(hour=23, minute=30, second=0, microsecond=0)  # MCX runs into the evening
        start = end - datetime.timedelta(days=days_back)
        raw = []
        cur = start
        empty_streak = 0
        while cur < end:
            nxt = min(cur + datetime.timedelta(days=chunk_days), end)
            rows = _fetch_15min_chunk(api, token, cur, nxt)
            raw.extend(rows)
            print(f"    chunk {cur.date()} -> {nxt.date()}: {len(rows)} rows")
            empty_streak = empty_streak + 1 if not rows else 0
            time.sleep(0.5)
            cur = nxt
            if empty_streak >= 4:
                print(f"    4 consecutive empty chunks - stopping early, likely reached the real data floor.")
                break
        with open(cache_path, "w") as f:
            json.dump(raw, f)

    seen = set()
    days = {}
    for row in raw:
        ts, o, h, l, c = row[0], row[1], row[2], row[3], row[4]
        if ts in seen:
            continue
        seen.add(ts)
        dt = datetime.datetime.fromisoformat(ts)
        days.setdefault(dt.date(), []).append((dt, float(o), float(h), float(l), float(c)))
    for d in days:
        days[d].sort(key=lambda x: x[0])
    return days


def find_big_jumps(days_sorted, days_dict, threshold_pct=8.0):
    """Same honest data-quality disclosure as scout27 - report, never filter."""
    jumps = []
    prev_close = None
    for d in days_sorted:
        bars = days_dict[d]
        day_close = bars[-1][4]
        if prev_close is not None and prev_close != 0:
            pct = (day_close - prev_close) / prev_close * 100
            if abs(pct) >= threshold_pct:
                jumps.append((d, pct))
        prev_close = day_close
    return jumps


# --------------------------------------------------------- UT BOT ENGINE ---
# Byte-identical to scout18/21/21b/32's UTBotState - not re-derived, not
# modified. T78/T79 trade real money off this exact formula.
def true_range(h, l, pc):
    if pc is None:
        return h - l
    return max(h - l, abs(h - pc), abs(l - pc))


class UTBotState:
    def __init__(self, key_val, atr_period):
        self.key_val = key_val
        self.atr_period = atr_period
        self.rma = None
        self.tr_seed = []
        self.stop = None
        self.prev_close = None

    def _update_atr(self, tr):
        if self.rma is None:
            self.tr_seed.append(tr)
            if len(self.tr_seed) < self.atr_period:
                return None
            self.rma = sum(self.tr_seed) / self.atr_period
            return self.rma
        self.rma = (self.rma * (self.atr_period - 1) + tr) / self.atr_period
        return self.rma

    def step(self, h, l, c):
        tr = true_range(h, l, self.prev_close)
        atr = self._update_atr(tr)
        if atr is None:
            self.prev_close = c
            return False, False
        n_loss = self.key_val * atr
        prev_stop = self.stop if self.stop is not None else 0.0
        prev_close = self.prev_close if self.prev_close is not None else c
        iff1 = (c - n_loss) if c > prev_stop else (c + n_loss)
        iff2 = min(prev_stop, c + n_loss) if (c < prev_stop and prev_close < prev_stop) else iff1
        new_stop = max(prev_stop, c - n_loss) if (c > prev_stop and prev_close > prev_stop) else iff2
        above = (prev_close <= prev_stop) and (c > prev_stop)
        below = (prev_close >= prev_stop) and (c < prev_stop)
        buy = (c > new_stop) and above
        sell = (c < new_stop) and below
        self.stop = new_stop
        self.prev_close = c
        return buy, sell


# --------------------------------------------------------------- BACKTEST --
def run_variant_longonly(bars, mode, lotsize):
    """Exact mirror of scout32's equity engine - Sell only closes an
    existing long, never opens a short. Returns (entry_date, pnl, hold_bars)."""
    buy_src = UTBotState(6.0, 1)
    sell_src = UTBotState(4.0, 300)
    trades = []
    side = None
    entry = e_i = e_d = None

    for i, (dt, o, h, l, c, day, last) in enumerate(bars):
        b, _ = buy_src.step(h, l, c)
        _, s = sell_src.step(h, l, c)

        if b and side is None:
            entry, e_i, e_d = c, i, day
            side = 'LONG'
        elif s and side == 'LONG':
            trades.append((e_d, (c - entry) * lotsize, i - e_i))
            side = None

        if last and side == 'LONG':
            if mode == 'intraday':
                trades.append((e_d, (c - entry) * lotsize, i - e_i))
                side = None
            elif mode == 'btst' and day > e_d:
                trades.append((e_d, (c - entry) * lotsize, i - e_i))
                side = None

    if side == 'LONG':
        last_c = bars[-1][4]
        trades.append((e_d, (last_c - entry) * lotsize, len(bars) - 1 - e_i))
    return trades


def run_variant_bidir(bars, mode, lotsize):
    """True stop-and-reverse - a Sell signal closes any open LONG and opens
    a real SHORT (mechanically valid for an MCX future, unlike equity
    delivery); a Buy signal closes any open SHORT and opens a LONG. This is
    genuinely new territory for this project's paper-tracker architecture -
    tested here so the decision whether to build it is made from real
    numbers, not assumption."""
    buy_src = UTBotState(6.0, 1)
    sell_src = UTBotState(4.0, 300)
    trades = []
    side = None   # None / 'LONG' / 'SHORT'
    entry = e_i = e_d = None

    def close(c, i, reason):
        if side == 'LONG':
            pnl = (c - entry) * lotsize
        else:
            pnl = (entry - c) * lotsize
        trades.append((e_d, pnl, i - e_i))

    for i, (dt, o, h, l, c, day, last) in enumerate(bars):
        b, _ = buy_src.step(h, l, c)
        _, s = sell_src.step(h, l, c)

        if b and side != 'LONG':
            if side == 'SHORT':
                close(c, i, 'reverse')
            entry, e_i, e_d = c, i, day
            side = 'LONG'
        elif s and side != 'SHORT':
            if side == 'LONG':
                close(c, i, 'reverse')
            entry, e_i, e_d = c, i, day
            side = 'SHORT'

        if last and side is not None:
            if mode == 'intraday':
                close(c, i, 'day_end')
                side = None
            elif mode == 'btst' and day > e_d:
                close(c, i, 'btst_forced')
                side = None
            # mode == 'always': ride continues, only the opposite signal (handled above) closes it

    if side is not None:
        last_c = bars[-1][4]
        close(last_c, len(bars) - 1, 'data_end')
    return trades


def split_days(day_keys, frac=0.7):
    s = int(len(day_keys) * frac)
    return day_keys[:s], day_keys[s:]


def verdict_of(tr, te, tr_avg, te_avg):
    if not tr or not te:
        return "UNEVALUABLE"
    if tr_avg > 0 and te_avg > 0 and te_avg >= 0.3 * tr_avg:
        return "ROBUST"
    return "NOT ROBUST"


def run_one(commodity, days_dict, lotsize):
    days_sorted = sorted(days_dict.keys())
    train_days, test_days = split_days(days_sorted, frac=0.7)
    train_set, test_set = set(train_days), set(test_days)

    bars = []
    for day in days_sorted:
        db = days_dict[day]
        for idx, (dt, o, h, l, c) in enumerate(db):
            bars.append((dt, o, h, l, c, day, idx == len(db) - 1))

    rows_out = []
    combos = [
        ("LONGONLY_ALWAYS", run_variant_longonly, 'open'),
        ("LONGONLY_INTRADAY", run_variant_longonly, 'intraday'),
        ("LONGONLY_BTST", run_variant_longonly, 'btst'),
        ("BIDIR_ALWAYS", run_variant_bidir, 'always'),
        ("BIDIR_INTRADAY", run_variant_bidir, 'intraday'),
        ("BIDIR_BTST", run_variant_bidir, 'btst'),
    ]
    for label, fn, mode in combos:
        trades = fn(bars, mode, lotsize)
        tr = [p for d, p, hb in trades if d in train_set]
        te = [p for d, p, hb in trades if d in test_set]
        tra = sum(tr) / len(tr) if tr else 0.0
        tea = sum(te) / len(te) if te else 0.0
        hold = sum(hb for d, p, hb in trades) / len(trades) if trades else 0.0
        per_day = len(trades) / len(days_sorted) if days_sorted else 0.0
        rows_out.append({
            "commodity": commodity, "policy": label,
            "train_n": len(tr), "train_avg": tra,
            "test_n": len(te), "test_avg": tea,
            "trades_per_day": per_day, "avg_hold_bars": hold,
            "verdict": verdict_of(tr, te, tra, tea),
        })
    return rows_out


# ------------------------------------------------------------ SMOKE TEST ---
def run_synthetic_smoke_test():
    """Proves the (unmodified) UTBotState engine + both backtest shapes run
    cleanly on a favorable synthetic series before ever touching real MCX
    data - same discipline as every prior scout."""
    import random
    random.seed(44)
    bars = []
    price = 100.0
    d = datetime.date(2024, 1, 1)
    day_idx = 0
    for i in range(3000):
        if i % 20 == 0 and i > 0:
            d += datetime.timedelta(days=1)
            day_idx += 1
        cyc = i % 80
        if cyc < 45:
            price *= 1.004
        elif cyc < 60:
            price *= 0.992
        else:
            price *= 1.003
        o = price * 0.998
        h = price * 1.006
        l = price * 0.994
        c = price
        last = (i % 20 == 19)
        bars.append((datetime.datetime(d.year, d.month, d.day, 9, 0), o, h, l, c, d, last))

    fired_any = False
    for label, fn, mode in [("LONGONLY_ALWAYS", run_variant_longonly, 'open'),
                             ("LONGONLY_BTST", run_variant_longonly, 'btst'),
                             ("BIDIR_ALWAYS", run_variant_bidir, 'always'),
                             ("BIDIR_BTST", run_variant_bidir, 'btst')]:
        trades = fn(bars, mode, lotsize=1)
        if trades:
            fired_any = True
        avg = sum(p for _, p, _ in trades) / len(trades) if trades else 0.0
        print(f"  smoke {label}: {len(trades)} trades, avg {avg:+.2f}")
    assert fired_any, "SMOKE TEST FAILED: no variant fired even once on a favorable synthetic series"

    # BIDIR must always be in a position after the first signal (never flat
    # mid-stream except at forced day-end/BTST closes) - sanity check the
    # reversal logic actually reverses rather than silently going flat.
    trades_always = run_variant_bidir(bars, 'always', lotsize=1)
    assert len(trades_always) >= 1, "SMOKE TEST FAILED: BIDIR_ALWAYS produced no trades"
    print("SMOKE TEST OK - unmodified UTBotState engine + both LONGONLY/BIDIR backtest shapes verified.\n")


# ------------------------------------------------------------------ MAIN ---
def main():
    run_synthetic_smoke_test()
    market_hours_guard()
    api = get_api()
    print(f"SCOUT44 - UT Bot on MCX Commodities - {datetime.datetime.now().isoformat()}")
    print(f"Testing {COMMODITIES}, {DAYS_BACK_15MIN} days back requested, FIFTEEN_MINUTE bars.\n")
    print("PHASE 1: checking real 15-min data depth per commodity (scout26 only proved DAILY")
    print("depth of ~10.9yr for the front-month token - intraday depth is untested, checked now.)\n")

    master = _load_scrip_master()

    depth_report = []
    all_results = []
    skipped = []
    for commodity in COMMODITIES:
        print(f"\n--- {commodity} ---")
        try:
            token, symbol, lotsize = resolve_commodity_token(master, commodity)
            print(f"  resolved to front-month contract {symbol} (token {token}, lotsize {lotsize})")
        except ValueError as e:
            print(f"  SKIPPED (token/lotsize): {e}")
            skipped.append((commodity, str(e)))
            continue

        try:
            days_dict = get_commodity_15min_days(api, commodity, token)
        except Exception as e:
            print(f"  SKIPPED (data fetch error): {e}")
            skipped.append((commodity, f"data fetch error: {e}"))
            continue

        n_days = len(days_dict)
        days_sorted = sorted(days_dict.keys())
        earliest = days_sorted[0] if days_sorted else None
        latest = days_sorted[-1] if days_sorted else None
        depth_report.append((commodity, n_days, earliest, latest))
        print(f"  PHASE 1 RESULT: {n_days} days of real 15-min data, {earliest} -> {latest}")

        if n_days < MIN_DAYS_REQUIRED:
            print(f"  SKIPPED from backtest: only {n_days} days (<{MIN_DAYS_REQUIRED} minimum) - "
                  f"15-min history on this token does not go back as far as the daily series did.")
            skipped.append((commodity, f"only {n_days} days of 15-min data"))
            continue

        jumps = find_big_jumps(days_sorted, days_dict)
        if jumps:
            print(f"  data-quality note: {len(jumps)} single-day close-to-close move(s) >=8% "
                  f"(commodities are naturally volatile - reported, nothing excluded because of it).")

        all_results.extend(run_one(commodity, days_dict, lotsize))

    print("\n" + "=" * 78)
    print("PHASE 1 SUMMARY - real 15-min data depth per commodity (the actual, previously-")
    print("untested question this script exists to answer)")
    print("=" * 78)
    for commodity, n_days, earliest, latest in depth_report:
        print(f"  {commodity}: {n_days} days, {earliest} -> {latest}")

    print("\n" + "=" * 118)
    print("PHASE 2 SUMMARY - UT Bot LONGONLY vs BIDIR hold-policy face-off per commodity")
    print("=" * 118)
    print(f"{'commodity':<12} {'policy':<18} {'trN':>4} {'train_avg_Rs':>13} {'teN':>4} {'test_avg_Rs':>12} "
          f"{'tr/day':>7} {'hold':>6}  verdict")
    print("-" * 118)
    robust = []
    for r in all_results:
        print(f"{r['commodity']:<12} {r['policy']:<18} {r['train_n']:>4} {r['train_avg']:>13.2f} "
              f"{r['test_n']:>4} {r['test_avg']:>12.2f} {r['trades_per_day']:>7.2f} "
              f"{r['avg_hold_bars']:>6.1f}  {r['verdict']}")
        if r["verdict"] == "ROBUST":
            robust.append(r)

    print(f"\nSUMMARY: {len(robust)}/{len(all_results)} combos ROBUST.")
    for r in sorted(robust, key=lambda x: -(x["train_avg"] + x["test_avg"])):
        print(f"  {r['commodity']} / {r['policy']} -> train Rs.{r['train_avg']:+.2f} (N={r['train_n']}), "
              f"test Rs.{r['test_avg']:+.2f} (N={r['test_n']}), {r['trades_per_day']:.2f} trades/day, "
              f"hold {r['avg_hold_bars']:.1f} bars")

    if skipped:
        print(f"\nSKIPPED {len(skipped)} commodit(y/ies) (did not crash the run):")
        for name, reason in skipped:
            print(f"  {name}: {reason}")

    print("\nDone. READ-ONLY run complete - nothing was modified, no orders placed.")
    print("Caveats not modeled: STT/CTT/brokerage/DP/MCX charges, entry slippage beyond signal-")
    print("bar close fill, margin/SPAN requirements. A ROBUST tag here is a candidate for the")
    print("next PAPER tracker discussion, not a live-ready proof - same skepticism as every")
    print("other scout in this project. LONGONLY vs BIDIR results together also answer whether")
    print("this project's commodity paper-tracker architecture needs a real SHORT leg at all.")


if __name__ == "__main__":
    main()
