#!/usr/bin/env python3
# ============================================================================
# T55 TRADE HISTORY - SEGMENT-WISE BREAKUP (READ-ONLY report)
# Reads /data/angel_trades.json (the bot's own trade log), takes every CLOSED
# T55 trade from its first trade up to the END OF LAST WEEK (Sunday), and
# prints: totals, by index (NIFTY/BANKNIFTY/SENSEX), by kind (real vs paper),
# by CE/PE, by index+type, by week, and a per-trade list at the end.
# Never writes anything. Run on the Render Shell:
#   curl -sL https://raw.githubusercontent.com/balarkt-ai/dataya-scout-reports/main/report_t55_breakdown.py -o report_t55_breakdown.py
#   python3 report_t55_breakdown.py            (optional: TECH=44 python3 ... for another technical)
# ============================================================================
import json, os, datetime, collections

TECH = os.environ.get("TECH", "55")
TRADES_FILE = os.environ.get("TRADES_FILE", "/data/angel_trades.json")
today = datetime.date.today()
last_sunday = today - datetime.timedelta(days=today.weekday() + 1)   # end of last week
if os.environ.get("THROUGH"):
    last_sunday = datetime.date.fromisoformat(os.environ["THROUGH"])

with open(TRADES_FILE) as f:
    trades = json.load(f)

rows = []
for t in trades:
    if str(t.get("technical", "1")) != TECH or t.get("status") != "CLOSED":
        continue
    try:
        d = datetime.date.fromisoformat(str(t.get("date", ""))[:10])
    except ValueError:
        continue
    if d > last_sunday:
        continue
    rows.append({
        "date": d, "time": t.get("time", ""), "symbol": t.get("symbol", "?"), "type": t.get("option_type", "?"),
        "kind": "PAPER" if t.get("trade_type") == "PAPER" else "REAL",
        "qty": t.get("quantity", 0), "entry": t.get("entry_price"), "exit": t.get("exit_price"),
        "points": t.get("points") or 0.0, "gross": t.get("pnl") or 0.0,
        "charges": t.get("charges") or 0.0, "net": t.get("net_pnl") if t.get("net_pnl") is not None else (t.get("pnl") or 0.0),
    })
rows.sort(key=lambda r: (r["date"], r["time"]))

if not rows:
    print(f"No CLOSED T{TECH} trades found up to {last_sunday}.")
    raise SystemExit(0)

first, last = rows[0]["date"], rows[-1]["date"]
print(f"T{TECH} TRADE HISTORY - {first} (first trade) to {last_sunday} (end of last week) - {len(rows)} closed trades")
print(f"Trades file: {TRADES_FILE} | last trade in range: {last}\n")


def stats(rs):
    n = len(rs)
    wins = sum(1 for r in rs if r["net"] > 0)
    gross = sum(r["gross"] for r in rs)
    net = sum(r["net"] for r in rs)
    ch = sum(r["charges"] for r in rs)
    gp = sum(r["net"] for r in rs if r["net"] > 0)
    gl = -sum(r["net"] for r in rs if r["net"] < 0)
    pf = (gp / gl) if gl > 0 else float("inf")
    pts = sum(r["points"] for r in rs)
    return n, wins, gross, ch, net, pf, pts


def table(title, groups):
    print("=" * 100)
    print(title)
    print("-" * 100)
    print(f"{'segment':<24} {'trades':>6} {'wins':>5} {'win%':>6} {'gross Rs':>12} {'charges':>9} {'NET Rs':>12} {'PF':>6} {'avg net':>9} {'pts':>8}")
    for name, rs in groups:
        if not rs:
            continue
        n, w, g, c, net, pf, pts = stats(rs)
        pfs = f"{pf:.2f}" if pf != float("inf") else "inf"
        print(f"{name:<24} {n:>6} {w:>5} {100*w/n:>5.0f}% {g:>12,.0f} {c:>9,.0f} {net:>12,.0f} {pfs:>6} {net/n:>9,.0f} {pts:>8.1f}")
    print()


by = lambda key: [(k, [r for r in rows if key(r) == k]) for k in sorted({key(r) for r in rows})]

table("OVERALL", [("ALL", rows)])
table("BY KIND (real money vs paper)", by(lambda r: r["kind"]))
table("BY INDEX", by(lambda r: r["symbol"]))
table("BY OPTION TYPE", by(lambda r: r["type"]))
table("BY INDEX + TYPE", by(lambda r: f"{r['symbol']} {r['type']}"))
table("BY INDEX + KIND", by(lambda r: f"{r['symbol']} {r['kind']}"))

# weekly (Mon-Sun)
def week_of(d):
    mon = d - datetime.timedelta(days=d.weekday())
    return mon.isoformat()
table("BY WEEK (Monday date)", by(lambda r: week_of(r["date"])))

# monthly
table("BY MONTH", by(lambda r: r["date"].strftime("%Y-%m")))

print("=" * 100)
print("PER-TRADE LIST (oldest first)")
print("-" * 100)
print(f"{'date':<11} {'time':<9} {'index':<10} {'type':<4} {'kind':<6} {'qty':>5} {'entry':>9} {'exit':>9} {'pts':>8} {'net Rs':>10}")
for r in rows:
    print(f"{r['date']!s:<11} {r['time']:<9} {r['symbol']:<10} {r['type']:<4} {r['kind']:<6} {r['qty']:>5} {r['entry']:>9} {r['exit']:>9} {r['points']:>8.2f} {r['net']:>10,.0f}")
print("\nREAD-ONLY report - nothing modified. 'net' = after the bot's own charge estimate; 'kind' REAL = actual broker order.")
