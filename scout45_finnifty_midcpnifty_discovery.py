#!/usr/bin/env python3
# ============================================================================
# SCOUT45 - FINNIFTY / MIDCPNIFTY DISCOVERY (2026-09-16)
#
# HIS ASK (AskUserQuestion-confirmed scope): extend the UT Bot method (T78's
# proven engine) to OTHER index options beyond NIFTY/BANKNIFTY/SENSEX -
# specifically FINNIFTY and MIDCPNIFTY.
#
# WHY THIS IS A DISCOVERY SCRIPT, NOT A BACKTEST YET: this project's own
# memory carried an assumption from an earlier session - "FINNIFTY Tuesday /
# MIDCPNIFTY Monday weekly expiry" - that a 2026-09-16 web check just showed
# is STALE. SEBI's November 2024 rule limited each exchange to ONE weekly
# index-options expiry; NSE kept NIFTY weekly (now Tuesday, changed from
# Thursday in Sept 2025) and moved BANKNIFTY/FINNIFTY/MIDCPNIFTY to MONTHLY
# ONLY (last Tuesday of the month). Never assumed as fact, verified below
# against the ACTUAL live instrument master, same discipline as every other
# scout in this project - a web article can also be stale or approximate.
#
# WHAT THIS SCRIPT ACTUALLY NEEDS TO FIND, ALL EMPIRICALLY (no guessed
# tokens or magic numbers - this project's INSTRUMENTS dict for NIFTY/
# BANKNIFTY/SENSEX already proves the backend logic (get_atm_strike/
# get_symbol_token) is fully generic and index-agnostic; it just needs the
# right constants):
#   1) The FINNIFTY/MIDCPNIFTY SPOT INDEX token (NSE) - needed to fetch
#      historical index-level candles for a future UT Bot backtest, same as
#      MANUAL_INDEX_LOOKUP already does for NIFTY/BANKNIFTY/SENSEX (hardcoded
#      there as "99926000"/"99926009"/"99919000" - real, already-verified
#      Angel tokens, NOT guessed). This script does NOT guess the FINNIFTY/
#      MIDCPNIFTY equivalents - it searches the live instrument master for
#      every plausible name match and prints the RAW candidate rows so the
#      real one can be picked from real data, not memory.
#   2) The FINNIFTY/MIDCPNIFTY OPTIDX contract facts: how many distinct
#      expiry dates currently exist (confirms monthly-only vs weekly), lot
#      size (a 2025-03/2026-01 NSE lot-size revision is real and recent -
#      never assume an old number still holds), and the strike interval
#      (computed from the ACTUAL live strike list, not looked up from a
#      possibly-outdated article).
#
# READ-ONLY. No backtest, no order, no Pine/webhook changes. Just facts.
# Run on Render Shell (needs live Angel session + network - this sandbox has
# neither), any time (this pulls from the instrument master + a small
# candle-count probe, not the option chain live feed, so no market-hours
# guard is strictly needed - but courtesy market-hours-avoidance kept anyway
# since this shares Angel's rate limits with the live trading system).
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


CACHE_DIR = "/data/scout45_cache"
SCRIP_MASTER_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
SCRIP_MASTER_CACHE = os.path.join(CACHE_DIR, "scrip_master.json")
SCRIP_MASTER_MAX_AGE_H = 24

# Every plausible name/symbol fragment Angel might use for these two indices
# in its instrument master - tried broadly on purpose rather than a single
# guessed exact string, since the official long name ("Nifty Financial
# Services", "Nifty Midcap Select") and the popular short name (FINNIFTY,
# MIDCPNIFTY) can both appear depending on the row/segment.
FINNIFTY_PATTERNS = ["FINNIFTY", "NIFTY FIN", "NIFTYFINSERVICE", "FINSERVICE", "FIN SERVICE"]
MIDCPNIFTY_PATTERNS = ["MIDCPNIFTY", "NIFTY MID SELECT", "MIDCAP SELECT", "MIDCPSELECT", "MIDCAPSELECT"]


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


def _matches_any(text, patterns):
    text_u = str(text or "").upper()
    return any(p in text_u for p in patterns)


def find_spot_index_candidates(master, patterns, label):
    print(f"\n--- {label}: SPOT INDEX TOKEN SEARCH (NSE, non-derivative rows) ---")
    candidates = []
    for r in master:
        if r.get("exch_seg") not in ("NSE", "BSE"):
            continue
        # Index "spot" rows in Angel's master typically carry an empty/absent
        # instrumenttype (unlike OPTIDX/FUTIDX derivative rows) - checked
        # broadly here (both empty AND any type) so nothing is missed, with
        # the instrumenttype printed so a human can tell them apart.
        name = r.get("name", "")
        symbol = r.get("symbol", "")
        if _matches_any(name, patterns) or _matches_any(symbol, patterns):
            candidates.append(r)
    if not candidates:
        print(f"  NO CANDIDATES FOUND for {label} under any of {patterns} - Angel's naming for this "
              f"index may differ from every pattern tried. Needs a manual look (search the raw scrip "
              f"master file for '{label}' by hand) rather than a guess.")
        return []
    print(f"  {len(candidates)} candidate row(s) found:")
    for r in candidates[:25]:
        print(f"    symbol={r.get('symbol')!r:30s} name={r.get('name')!r:25s} token={r.get('token')!r:10s} "
              f"exch_seg={r.get('exch_seg')!r:6s} instrumenttype={r.get('instrumenttype')!r:10s} expiry={r.get('expiry')!r}")
    if len(candidates) > 25:
        print(f"    ... and {len(candidates) - 25} more (truncated for readability)")
    return candidates


def _expiry_key(r):
    e = r.get("expiry", "")
    try:
        return datetime.datetime.strptime(e, "%d%b%Y")
    except Exception:
        return datetime.datetime.max


def analyze_optidx_contract(master, index_name, exch_seg_guess="NFO"):
    print(f"\n--- {index_name}: OPTIDX CONTRACT FACTS ---")
    rows = [r for r in master if r.get("instrumenttype") == "OPTIDX"
            and str(r.get("name", "")).upper() == index_name.upper()]
    if not rows:
        print(f"  NO OPTIDX rows found for name=='{index_name}' - trying a looser symbol-prefix match instead...")
        rows = [r for r in master if r.get("instrumenttype") == "OPTIDX"
                and str(r.get("symbol", "")).upper().startswith(index_name.upper())]
    if not rows:
        print(f"  STILL NOTHING - {index_name} may not currently have any listed OPTIDX contracts "
              f"(discontinued entirely, or a different name is used - check the spot search above "
              f"for hints on the real naming).")
        return

    exch_segs = sorted({r.get("exch_seg") for r in rows})
    print(f"  {len(rows)} total OPTIDX rows found, exch_seg(s): {exch_segs}")

    expiries = sorted({r.get("expiry") for r in rows if r.get("expiry")}, key=lambda e: _expiry_key({"expiry": e}))
    print(f"  distinct expiry dates currently listed ({len(expiries)}): {expiries}")
    if len(expiries) >= 2:
        gaps_days = []
        parsed = [_expiry_key({"expiry": e}) for e in expiries]
        for i in range(1, len(parsed)):
            gaps_days.append((parsed[i] - parsed[i - 1]).days)
        print(f"  gaps between consecutive expiries (days): {gaps_days} "
              f"-> {'looks MONTHLY (gaps ~28-35 days)' if all(g >= 20 for g in gaps_days) else 'looks WEEKLY or mixed (some gaps <20 days)'}")

    lotsizes = sorted({r.get("lotsize") for r in rows})
    print(f"  distinct lotsize value(s) found across all listed contracts: {lotsizes}")

    nearest_expiry = expiries[0] if expiries else None
    if nearest_expiry:
        near_rows = [r for r in rows if r.get("expiry") == nearest_expiry]
        strikes = sorted({round(float(r.get("strike", "-1")) / 100, 2) for r in near_rows if r.get("strike")})
        print(f"  nearest expiry {nearest_expiry}: {len(near_rows)} contract rows, "
              f"{len(strikes)} distinct strikes, range {strikes[0] if strikes else '?'} -> {strikes[-1] if strikes else '?'}")
        if len(strikes) >= 3:
            gaps = sorted({round(strikes[i + 1] - strikes[i], 2) for i in range(len(strikes) - 1)})
            print(f"  observed strike gap(s) in the nearest expiry: {gaps} "
                  f"(the smallest recurring value is almost always the true strike interval)")
        sample = sorted({r.get("symbol") for r in near_rows})[:6]
        print(f"  sample tradingsymbols: {sample}")


def probe_index_15min_depth(api, token, label, days_back=730):
    """Small, fast probe (NOT a full fetch) - just confirms candles come
    back at all for a plausible token before committing to a full multi-
    year pull in a later script, and gives a rough sense of the real depth
    via a handful of spaced date-range checks rather than one giant call."""
    print(f"\n--- {label}: 15-MIN CANDLE PROBE (token {token}) ---")
    now = datetime.datetime.now()
    checkpoints = [30, 180, 365, 730]
    for days in checkpoints:
        if days > days_back:
            continue
        start = now - datetime.timedelta(days=days)
        end = start + datetime.timedelta(days=3)
        try:
            resp = api.getCandleData({
                "exchange": "NSE", "symboltoken": str(token), "interval": "FIFTEEN_MINUTE",
                "fromdate": start.strftime("%Y-%m-%d %H:%M"), "todate": end.strftime("%Y-%m-%d %H:%M"),
            })
            rows = (resp or {}).get("data") or []
            print(f"  ~{days} days back ({start.date()} to {end.date()}): {len(rows)} bars returned")
        except Exception as e:
            print(f"  ~{days} days back: error - {e}")
        time.sleep(0.6)


def main():
    market_hours_guard()
    api = get_api()
    print(f"SCOUT45 - FINNIFTY/MIDCPNIFTY discovery - {datetime.datetime.now().isoformat()}\n")
    master = _load_scrip_master()

    finnifty_spot_candidates = find_spot_index_candidates(master, FINNIFTY_PATTERNS, "FINNIFTY")
    midcp_spot_candidates = find_spot_index_candidates(master, MIDCPNIFTY_PATTERNS, "MIDCPNIFTY")

    analyze_optidx_contract(master, "FINNIFTY")
    analyze_optidx_contract(master, "MIDCPNIFTY")

    # Only probe candle depth on a spot candidate if there's exactly one
    # unambiguous, clearly-index-like row (exch_seg NSE, no expiry field) -
    # otherwise this is deliberately left for a human decision rather than
    # guessing which of several candidates is the real spot token.
    for label, candidates in [("FINNIFTY", finnifty_spot_candidates), ("MIDCPNIFTY", midcp_spot_candidates)]:
        index_like = [r for r in candidates if r.get("exch_seg") == "NSE" and not r.get("expiry")]
        if len(index_like) == 1:
            probe_index_15min_depth(api, index_like[0]["token"], label)
        elif len(index_like) == 0:
            print(f"\n--- {label}: no unambiguous no-expiry NSE row found among the candidates above - "
                  f"skipping the candle probe, needs a human pick from the printed list first. ---")
        else:
            print(f"\n--- {label}: {len(index_like)} ambiguous no-expiry NSE candidates found - "
                  f"skipping the candle probe until one is confirmed correct (see rows printed above). ---")

    print("\nDone. READ-ONLY discovery complete - no backtest run yet, no order placed, no Pine/webhook "
          "touched. Real output above (not a guess) decides the next step: which row is the true spot "
          "token, whether these are genuinely monthly-only now, and the real lot size/strike interval.")


if __name__ == "__main__":
    main()
