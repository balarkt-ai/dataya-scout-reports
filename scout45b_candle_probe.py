#!/usr/bin/env python3
# ============================================================================
# SCOUT45b - CONFIRM 15-MIN CANDLE DEPTH ON THE RESOLVED FINNIFTY/MIDCPNIFTY
# SPOT TOKENS (2026-09-16)
#
# scout45's real run resolved these two tokens with high confidence (not a
# guess):
#   FINNIFTY:   token 99926037 (both this and the alternate "26037" gave the
#               EXACT SAME real LTP 25262.4 - interchangeable, same feed)
#   MIDCPNIFTY: token 99926074 (same situation - "26074" gave the identical
#               LTP 14288.45; the 3rd candidate, BSE token 99919043 "MIDSEL",
#               is a DIFFERENT real-world index - S&P BSE Midcap Select, not
#               NSE's own Nifty Midcap Select that MIDCPNIFTY options track -
#               excluded on that basis, not by LTP alone)
#   Both chosen tokens follow the exact same "999-prefix AMXIDX" numbering
#   family as this project's own already-proven, already-working
#   MANUAL_INDEX_LOOKUP tokens (NIFTY=99926000, BANKNIFTY=99926009,
#   SENSEX=99919000) - not a coincidence, the same Angel index-token scheme.
#
# This script just confirms real 15-min candles actually come back for these
# two specific tokens, and roughly how far back - the same "never assume
# depth, check it" step scout44 did for commodities (which found a real
# 1.4yr floor, not the 10.9yr the daily series had). Read-only, no backtest
# math yet.
# ============================================================================
import os
import sys
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


RESOLVED_TOKENS = {
    "FINNIFTY": "99926037",
    "MIDCPNIFTY": "99926074",
}


def probe_depth(api, label, token):
    print(f"\n--- {label} (token {token}) ---")
    now = datetime.datetime.now()
    checkpoints_days = [30, 90, 180, 365, 545, 730, 1000]
    last_ok = None
    for days in checkpoints_days:
        start = now - datetime.timedelta(days=days)
        end = start + datetime.timedelta(days=3)
        try:
            resp = api.getCandleData({
                "exchange": "NSE", "symboltoken": token, "interval": "FIFTEEN_MINUTE",
                "fromdate": start.strftime("%Y-%m-%d %H:%M"), "todate": end.strftime("%Y-%m-%d %H:%M"),
            })
            rows = (resp or {}).get("data") or []
            print(f"  ~{days} days back ({start.date()} to {end.date()}): {len(rows)} bars")
            if rows:
                last_ok = days
        except Exception as e:
            print(f"  ~{days} days back: error - {e}")
        import time as _t
        _t.sleep(0.6)
    if last_ok:
        print(f"  -> real 15-min data confirmed at least {last_ok} days back for {label}.")
    else:
        print(f"  -> NO real 15-min data found at any checkpoint for {label} - token may be wrong "
              f"or this index genuinely has no intraday history under this token.")


def main():
    market_hours_guard()
    api = get_api()
    print(f"SCOUT45b - candle depth confirmation - {datetime.datetime.now().isoformat()}")
    for label, token in RESOLVED_TOKENS.items():
        probe_depth(api, label, token)
    print("\nDone. READ-ONLY - no backtest run, no order placed.")


if __name__ == "__main__":
    main()
