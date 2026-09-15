#!/usr/bin/env python3
# ============================================================================
# SCOUT43 - OPTION-CHAIN OI / PCR / MAX-PAIN SNAPSHOT COLLECTOR   (READ-ONLY)
#
# WHY THIS EXISTS (his 2026-09-15 "option b" pick): Angel SmartAPI gives NO
# historical open-interest series, so an OI/PCR-based strategy cannot be
# backtested from history the way every other scout was. The only way to
# get that data is to COLLECT IT OURSELVES, one snapshot at a time, and
# backtest later once enough days exist. This script is step 1 of that
# track: it (a) empirically verifies what Angel actually returns for option
# quotes (never guess a data structure), and (b) writes one clean snapshot
# per index per run under /data (the persistent disk), plus one summary row
# per run in a CSV that a future scout can read straight back.
#
# WHAT ONE RUN DOES, per index (NIFTY, BANKNIFTY on NFO; SENSEX on BFO):
#   1. spot LTP (index token, quote-only, same tokens app.py already uses)
#   2. nearest un-expired OPTIDX expiry from Angel's own scrip master
#   3. ATM strike from the spot, +/- STRIKES_EACH_SIDE strikes, CE and PE
#   4. FULL quotes in batches of 50 tokens (Angel's per-request cap, 1 req/s)
#      -> LTP, OI, volume, bid/ask totals per contract
#   5. derived: total CE OI, total PE OI, PCR (PE/CE), MAX PAIN strike, top-3
#      CE and PE OI walls, ATM straddle price
#   6. Angel's own putCallRatio() list and optionGreek() (ATM IV) are also
#      captured, non-fatally - if either endpoint misbehaves the snapshot
#      still saves; the failure is printed, not hidden.
#
# OUTPUT: /data/oi_snapshots/<INDEX>/<YYYY-MM-DD>_<HHMM>.json (full chain)
#         /data/oi_snapshots/oi_summary.csv (one row per index per run)
#
# CADENCE (for now, manual): run it at ~15:20 IST on trading days for a
# close-of-day snapshot; an extra 09:20 and 12:00 run gives intraday OI
# change. Once 2-3 days of snapshots look clean, this same logic gets folded
# into app.py as an additive-only scheduler thread (same pattern as the GFS
# daily scanner) so it runs by itself. Never places an order. Never writes
# anything outside /data/oi_snapshots.
#
# Render Shell:
#   curl -sL https://raw.githubusercontent.com/balarkt-ai/dataya-scout-reports/main/scout43_oi_pcr_snapshot.py -o scout43_oi_pcr_snapshot.py
#   sha256sum scout43_oi_pcr_snapshot.py
#   python3 scout43_oi_pcr_snapshot.py
# Optional env: SCOUT43_INDEXES=NIFTY   SCOUT43_STRIKES=10   SCOUT43_VERBOSE=1
# ============================================================================
import os
import sys
import csv
import json
import time
import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

INDEXES = {
    # spot tokens: the same quote-only index tokens app.py / scout41 use
    "NIFTY":     {"opt_exch": "NFO", "spot_exch": "NSE", "spot_token": "99926000"},
    "BANKNIFTY": {"opt_exch": "NFO", "spot_exch": "NSE", "spot_token": "99926009"},
    "SENSEX":    {"opt_exch": "BFO", "spot_exch": "BSE", "spot_token": "99919000"},
}
STRIKES_EACH_SIDE = int(os.environ.get("SCOUT43_STRIKES", "20"))
BATCH = 50                       # Angel getMarketData cap per request
REQ_SLEEP = 1.1                  # Angel rate limit: 1 request / second
OUT_DIR = "/data/oi_snapshots"
SUMMARY_CSV = os.path.join(OUT_DIR, "oi_summary.csv")
CACHE_DIR = "/data/scout43_oi_cache"
SCRIP_MASTER_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
SCRIP_MASTER_CACHE = os.path.join(CACHE_DIR, "scrip_master.json")
SCRIP_MASTER_MAX_AGE_H = 24
VERBOSE = os.environ.get("SCOUT43_VERBOSE") == "1"

SUMMARY_FIELDS = ["snapshot_ts", "index", "expiry", "spot", "atm", "strike_step", "n_contracts",
                  "ce_oi_total", "pe_oi_total", "pcr", "max_pain", "ce_wall_1", "pe_wall_1",
                  "atm_ce_ltp", "atm_pe_ltp", "atm_straddle", "atm_iv_ce", "atm_iv_pe", "angel_pcr_near"]


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


# ------------------------------------------------------- SCRIP MASTER -----
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
    print("  (scrip master: downloading fresh copy - large file, may take a bit)")
    with urllib.request.urlopen(SCRIP_MASTER_URL, timeout=120) as resp:
        data = json.loads(resp.read())
    with open(SCRIP_MASTER_CACHE, "w") as f:
        json.dump(data, f)
    print(f"  (scrip master: downloaded {len(data)} rows)")
    return data


def parse_expiry(s):
    """Angel master expiry looks like '25SEP2026'. Returns date or None."""
    try:
        return datetime.datetime.strptime(str(s).strip().upper(), "%d%b%Y").date()
    except Exception:
        return None


def option_rows(master, index_name, opt_exch):
    """All OPTIDX rows for this index on this exchange, parsed. Strike in the
    master is a string in paise x100 ('2330000.000000' -> 23300.0)."""
    out = []
    for r in master:
        if r.get("exch_seg") != opt_exch or r.get("name") != index_name:
            continue
        if str(r.get("instrumenttype", "")).upper() != "OPTIDX":
            continue
        exp = parse_expiry(r.get("expiry"))
        if exp is None:
            continue
        try:
            strike = float(r.get("strike", 0)) / 100.0
        except (TypeError, ValueError):
            continue
        sym = str(r.get("symbol", ""))
        opt_type = "CE" if sym.endswith("CE") else ("PE" if sym.endswith("PE") else None)
        if opt_type is None or strike <= 0:
            continue
        out.append({"token": str(r.get("token")), "symbol": sym, "expiry": exp,
                    "strike": strike, "type": opt_type, "lotsize": r.get("lotsize")})
    return out


def nearest_expiry(rows, today):
    exps = sorted({r["expiry"] for r in rows if r["expiry"] >= today})
    return exps[0] if exps else None


def strike_step(strikes):
    s = sorted(set(strikes))
    diffs = [b - a for a, b in zip(s, s[1:]) if b - a > 0]
    return min(diffs) if diffs else None


def select_chain(rows, expiry, spot, each_side=STRIKES_EACH_SIDE):
    exp_rows = [r for r in rows if r["expiry"] == expiry]
    step = strike_step([r["strike"] for r in exp_rows])
    if not step:
        return [], None, None
    atm = round(spot / step) * step
    lo, hi = atm - each_side * step, atm + each_side * step
    chain = [r for r in exp_rows if lo - 1e-9 <= r["strike"] <= hi + 1e-9]
    chain.sort(key=lambda r: (r["strike"], r["type"]))
    return chain, atm, step


# ---------------------------------------------------------------- QUOTES ---
def _first(d, *keys, default=None):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d.get(k)
    return default


def _num(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def fetch_quotes(api, exch, tokens, mode="FULL"):
    """Batches of 50, 1 req/s. Returns {token: raw_quote_dict}. Prints the
    key set of the first fetched item ONCE so the structure is verified
    empirically, and lists any tokens Angel reported as unfetched."""
    out = {}
    printed = False
    for i in range(0, len(tokens), BATCH):
        batch = tokens[i:i + BATCH]
        resp = None
        for attempt in range(3):
            try:
                resp = api.getMarketData(mode, {exch: batch})
                break
            except Exception as e:
                print(f"    getMarketData retry {attempt + 1}: {e}")
                time.sleep(2)
        time.sleep(REQ_SLEEP)
        if not resp or not resp.get("status"):
            print(f"    batch {i // BATCH + 1}: NO DATA -> {str(resp)[:200]}")
            continue
        data = resp.get("data") or {}
        fetched = data.get("fetched") or []
        unfetched = data.get("unfetched") or []
        if fetched and not printed:
            print(f"    raw quote keys (verified from live response): {sorted(fetched[0].keys())}")
            printed = True
        for q in fetched:
            tok = str(_first(q, "symbolToken", "symboltoken", "token", default=""))
            if tok:
                out[tok] = q
        if unfetched:
            print(f"    batch {i // BATCH + 1}: {len(unfetched)} unfetched -> {str(unfetched)[:200]}")
    return out


def spot_ltp(api, exch, token):
    q = fetch_quotes(api, exch, [token], mode="LTP")
    if token not in q:
        raise RuntimeError(f"spot LTP not returned for {exch}:{token}")
    return _num(_first(q[token], "ltp", "lastTradedPrice"))


# --------------------------------------------------------------- DERIVED ---
def max_pain(rows):
    """rows: list of dicts with strike, type, oi. Classic max pain: the strike
    where the total notional loss of all option BUYERS (= writers' gain) is
    smallest. Returns (strike, pain_value) or (None, None)."""
    strikes = sorted({r["strike"] for r in rows})
    if not strikes:
        return None, None
    best = None
    for s in strikes:
        pain = 0.0
        for r in rows:
            if r["type"] == "CE":
                pain += r["oi"] * max(0.0, s - r["strike"])
            else:
                pain += r["oi"] * max(0.0, r["strike"] - s)
        if best is None or pain < best[1]:
            best = (s, pain)
    return best


def summarise(index_name, expiry, spot, atm, step, rows):
    ce = [r for r in rows if r["type"] == "CE"]
    pe = [r for r in rows if r["type"] == "PE"]
    ce_oi = sum(r["oi"] for r in ce)
    pe_oi = sum(r["oi"] for r in pe)
    pcr = round(pe_oi / ce_oi, 4) if ce_oi > 0 else None
    mp, _ = max_pain(rows)
    ce_walls = [r["strike"] for r in sorted(ce, key=lambda r: -r["oi"])[:3]]
    pe_walls = [r["strike"] for r in sorted(pe, key=lambda r: -r["oi"])[:3]]
    atm_ce = next((r for r in ce if abs(r["strike"] - atm) < 1e-9), None)
    atm_pe = next((r for r in pe if abs(r["strike"] - atm) < 1e-9), None)
    atm_ce_ltp = atm_ce["ltp"] if atm_ce else None
    atm_pe_ltp = atm_pe["ltp"] if atm_pe else None
    straddle = round(atm_ce_ltp + atm_pe_ltp, 2) if (atm_ce_ltp is not None and atm_pe_ltp is not None) else None
    return {
        "index": index_name, "expiry": expiry.isoformat(), "spot": spot, "atm": atm, "strike_step": step,
        "n_contracts": len(rows), "ce_oi_total": ce_oi, "pe_oi_total": pe_oi, "pcr": pcr,
        "max_pain": mp, "ce_walls": ce_walls, "pe_walls": pe_walls,
        "ce_wall_1": ce_walls[0] if ce_walls else None, "pe_wall_1": pe_walls[0] if pe_walls else None,
        "atm_ce_ltp": atm_ce_ltp, "atm_pe_ltp": atm_pe_ltp, "atm_straddle": straddle,
    }


# ---------------------------------------------------- ANGEL PCR / GREEKS ---
def angel_pcr(api):
    """Angel's own PCR endpoint (per futures symbol, e.g. NIFTY30SEP26FUT).
    Non-fatal: returns [] on any failure."""
    try:
        resp = api.putCallRatio()
        if resp and resp.get("status") and isinstance(resp.get("data"), list):
            return resp["data"]
        print(f"  (putCallRatio: no data -> {str(resp)[:200]})")
    except Exception as e:
        print(f"  (putCallRatio failed, non-fatal: {e})")
    return []


def angel_greeks(api, index_name, expiry):
    """optionGreek for one expiry. Angel wants '25SEP2026'. Non-fatal."""
    try:
        resp = api.optionGreek({"name": index_name, "expirydate": expiry.strftime("%d%b%Y").upper()})
        if resp and resp.get("status") and isinstance(resp.get("data"), list):
            return resp["data"]
        print(f"  (optionGreek: no data -> {str(resp)[:200]})")
    except Exception as e:
        print(f"  (optionGreek failed, non-fatal: {e})")
    return []


def atm_iv(greeks, atm):
    ce = pe = None
    for g in greeks:
        try:
            if abs(_num(_first(g, "strikePrice", "strike")) - atm) > 1e-6:
                continue
        except Exception:
            continue
        iv = _num(_first(g, "impliedVolatility", "iv"), default=None)
        t = str(_first(g, "optionType", "type", default="")).upper()
        if t == "CE":
            ce = iv
        elif t == "PE":
            pe = iv
    return ce, pe


def near_pcr_for(pcr_rows, index_name):
    """Angel's PCR list -> the entry for this index's nearest FUT, if any."""
    cands = []
    for row in pcr_rows:
        sym = str(_first(row, "tradingSymbol", "tradingsymbol", "symbol", default=""))
        if sym.startswith(index_name) and sym.endswith("FUT"):
            body = sym[len(index_name):-3]
            # NIFTY30SEP26FUT -> '30SEP26' ; guard against e.g. NIFTYNXT50 / BANKNIFTY prefix clashes
            if index_name == "NIFTY" and (sym.startswith("NIFTYNXT") or sym.startswith("NIFTYIT")):
                continue
            try:
                d = datetime.datetime.strptime(body, "%d%b%y").date()
            except ValueError:
                continue
            cands.append((d, _num(_first(row, "pcr"), default=None)))
    cands.sort()
    return cands[0][1] if cands else None


# ------------------------------------------------------------------ SAVE ---
def save_snapshot(index_name, snap_ts, payload):
    d = os.path.join(OUT_DIR, index_name)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, snap_ts.strftime("%Y-%m-%d_%H%M") + ".json")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=1, default=str)
    os.replace(tmp, path)
    return path


def append_summary(row):
    os.makedirs(OUT_DIR, exist_ok=True)
    new = not os.path.exists(SUMMARY_CSV)
    with open(SUMMARY_CSV, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SUMMARY_FIELDS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(row)


# ------------------------------------------------------------------ MAIN ---
def run_index(api, master, index_name, cfg, snap_ts, pcr_rows):
    print(f"\n[{index_name}]")
    spot = spot_ltp(api, cfg["spot_exch"], cfg["spot_token"])
    print(f"  spot LTP: {spot}")
    rows = option_rows(master, index_name, cfg["opt_exch"])
    if not rows:
        raise RuntimeError(f"no OPTIDX rows for {index_name} on {cfg['opt_exch']} in scrip master")
    expiry = nearest_expiry(rows, snap_ts.date())
    if expiry is None:
        raise RuntimeError(f"no un-expired expiry found for {index_name}")
    chain, atm, step = select_chain(rows, expiry, spot)
    print(f"  expiry {expiry} | strike step {step} | ATM {atm} | {len(chain)} contracts "
          f"(+/-{STRIKES_EACH_SIDE} strikes)")
    if not chain:
        raise RuntimeError(f"empty chain for {index_name} {expiry}")

    quotes = fetch_quotes(api, cfg["opt_exch"], [r["token"] for r in chain])
    print(f"  quotes returned: {len(quotes)}/{len(chain)}")
    out_rows = []
    for r in chain:
        q = quotes.get(r["token"])
        if q is None:
            continue
        out_rows.append({
            "token": r["token"], "symbol": r["symbol"], "strike": r["strike"], "type": r["type"],
            "ltp": _num(_first(q, "ltp")),
            "oi": _num(_first(q, "opnInterest", "openInterest", "oi")),
            "volume": _num(_first(q, "tradeVolume", "volume")),
            "tot_buy_qty": _num(_first(q, "totBuyQuan")),
            "tot_sell_qty": _num(_first(q, "totSellQuan")),
            "net_change": _num(_first(q, "netChange")),
            "pct_change": _num(_first(q, "percentChange")),
            "exch_trade_time": _first(q, "exchTradeTime", "exchFeedTime"),
        })
    if VERBOSE and quotes:
        print("  first raw quote:", json.dumps(next(iter(quotes.values())), default=str)[:600])

    summary = summarise(index_name, expiry, spot, atm, step, out_rows)
    greeks = angel_greeks(api, index_name, expiry)
    time.sleep(REQ_SLEEP)
    iv_ce, iv_pe = atm_iv(greeks, atm)
    summary["atm_iv_ce"], summary["atm_iv_pe"] = iv_ce, iv_pe
    summary["angel_pcr_near"] = near_pcr_for(pcr_rows, index_name)
    summary["snapshot_ts"] = snap_ts.isoformat()

    zero_oi = sum(1 for r in out_rows if r["oi"] <= 0)
    print(f"  CE OI {summary['ce_oi_total']:,.0f} | PE OI {summary['pe_oi_total']:,.0f} | PCR {summary['pcr']} | "
          f"MAX PAIN {summary['max_pain']} | CE walls {summary['ce_walls']} | PE walls {summary['pe_walls']}")
    print(f"  ATM {atm}: CE {summary['atm_ce_ltp']} + PE {summary['atm_pe_ltp']} = straddle {summary['atm_straddle']} | "
          f"ATM IV CE/PE {iv_ce}/{iv_pe} | Angel PCR(near FUT) {summary['angel_pcr_near']}")
    if zero_oi:
        print(f"  NOTE: {zero_oi}/{len(out_rows)} contracts came back with OI<=0 (far strikes / stale) - kept as-is")

    path = save_snapshot(index_name, snap_ts, {"summary": summary, "chain": out_rows,
                                                "greeks": greeks, "angel_pcr_raw": pcr_rows})
    append_summary(summary)
    print(f"  saved {path}")
    return summary


def main():
    snap_ts = datetime.datetime.now(IST).replace(microsecond=0)
    wanted = os.environ.get("SCOUT43_INDEXES")
    names = [n.strip().upper() for n in wanted.split(",") if n.strip()] if wanted else list(INDEXES)
    print(f"SCOUT43 - OI/PCR/MAX-PAIN snapshot - {snap_ts.isoformat()} - indexes {names}")
    if snap_ts.weekday() >= 5:
        print("NOTE: weekend - quotes will be Friday's close; snapshot still saved (labelled by today).")

    api = get_api()
    master = _load_scrip_master()
    pcr_rows = angel_pcr(api)
    time.sleep(REQ_SLEEP)
    if pcr_rows:
        print(f"  putCallRatio: {len(pcr_rows)} rows; first: {json.dumps(pcr_rows[0], default=str)[:200]}")

    done, failed = [], []
    for name in names:
        cfg = INDEXES.get(name)
        if not cfg:
            print(f"\n[{name}] unknown index - skipped"); failed.append((name, "unknown")); continue
        try:
            done.append(run_index(api, master, name, cfg, snap_ts, pcr_rows))
        except Exception as e:
            print(f"  FAILED {name}: {e}")
            failed.append((name, str(e)))

    print("\n" + "=" * 100)
    print(f"{'index':<10} {'expiry':<11} {'spot':>10} {'atm':>8} {'PCR':>7} {'maxpain':>8} {'CEwall':>8} {'PEwall':>8} {'straddle':>9}")
    for s in done:
        print(f"{s['index']:<10} {s['expiry']:<11} {s['spot']:>10.2f} {s['atm']:>8.0f} {str(s['pcr']):>7} "
              f"{str(s['max_pain']):>8} {str(s['ce_wall_1']):>8} {str(s['pe_wall_1']):>8} {str(s['atm_straddle']):>9}")
    if failed:
        print(f"\nFAILED: {failed}")
    print(f"\nSummary CSV: {SUMMARY_CSV}")
    print("Done. READ-ONLY run - no orders placed; only /data/oi_snapshots was written.")


if __name__ == "__main__":
    main()
