#!/usr/bin/env python3
# ============================================================================
# T55 EXIT-QUALITY REPORT (READ-ONLY) - "are we exiting too early on BANKNIFTY?"
# Reads /data/angel_trades.json, takes every CLOSED T55 trade (real + paper),
# and shows per index: how much of each trade's PEAK profit was captured, how
# long the trade was held, and what the SAME option did in the 30 minutes
# AFTER the exit (the bot's own post-exit tracker records that).
# Exit reason is not stored on the trade, so it is INFERRED from the numbers:
#   SL 2%          points <= -1.5% of entry
#   early-momentum held < 6 min and small loss
#   trail-tier     positive points but below peak
#   flat/other     everything else
# Never writes anything. Run on the Render Shell:
#   curl -sL https://raw.githubusercontent.com/balarkt-ai/dataya-scout-reports/main/report_t55_exits.py -o report_t55_exits.py
#   python3 report_t55_exits.py          (optional: TECH=44 python3 ... ; DAYS=30 to limit)
# ============================================================================
import json, os, datetime, collections

TECH = os.environ.get("TECH", "55")
TRADES_FILE = os.environ.get("TRADES_FILE", "/data/angel_trades.json")
DAYS = int(os.environ.get("DAYS", "0") or 0)

with open(TRADES_FILE) as f:
    trades = json.load(f)


def hhmm_to_min(s):
    try:
        h, m, *rest = str(s).split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return None


rows = []
cutoff = datetime.date.today() - datetime.timedelta(days=DAYS) if DAYS else None
for t in trades:
    if str(t.get("technical", "1")) != TECH or t.get("status") != "CLOSED":
        continue
    try:
        d = datetime.date.fromisoformat(str(t.get("date", ""))[:10])
    except ValueError:
        continue
    if cutoff and d < cutoff:
        continue
    entry = float(t.get("entry_price") or 0)
    exitp = float(t.get("exit_price") or 0)
    pts = float(t.get("points") or 0)
    peak = t.get("peak_points")
    peak = float(peak) if peak is not None else None
    tin, tout = hhmm_to_min(t.get("time")), hhmm_to_min(t.get("exit_time"))
    hold = (tout - tin) if (tin is not None and tout is not None and tout >= tin) else None
    up = float(t.get("post_exit_max_up_pts") or 0)
    dn = float(t.get("post_exit_max_down_pts") or 0)
    tracked = bool(t.get("post_exit_tracking_done"))
    # inferred exit reason
    if entry and pts <= -0.015 * entry:
        reason = "SL(2%)"
    elif hold is not None and hold < 6 and pts <= 0:
        reason = "early-mom"
    elif pts > 0 and peak is not None and pts < peak:
        reason = "trail-tier"
    elif pts > 0:
        reason = "profit"
    else:
        reason = "other"
    # "left on the table" = post-exit continuation in the trade's favour (same long option, so UP = missed)
    missed = up if tracked else None
    rows.append(dict(date=d, time=t.get("time", ""), exit_time=t.get("exit_time", ""), symbol=t.get("symbol", "?"),
                     type=t.get("option_type", "?"), kind="PAPER" if t.get("trade_type") == "PAPER" else "REAL",
                     entry=entry, exit=exitp, pts=pts, peak=peak, cap=t.get("capture_pct"), hold=hold,
                     reason=reason, up=up, dn=dn, tracked=tracked, missed=missed, net=t.get("net_pnl")))

rows.sort(key=lambda r: (r["date"], r["time"]))
if not rows:
    print(f"No CLOSED T{TECH} trades found.")
    raise SystemExit(0)

print(f"T{TECH} EXIT QUALITY - {rows[0]['date']} to {rows[-1]['date']} - {len(rows)} closed trades (real + paper)\n")


def avg(xs):
    xs = [x for x in xs if x is not None]
    return (sum(xs) / len(xs)) if xs else None


def fmt(x, d=1):
    return "-" if x is None else f"{x:.{d}f}"


print("=" * 118)
print("PER INDEX: how much of the move did we keep, how long did we hold, what happened right after the exit")
print("-" * 118)
print(f"{'index':<10} {'n':>3} {'win%':>5} {'avg pts':>8} {'avg peak':>9} {'capture%':>9} {'hold min':>9} {'SL2%':>5} {'e-mom':>6} {'trail':>6} {'post-exit UP>=10':>17} {'avg post UP':>12} {'avg post DN':>12}")
for sym in sorted({r["symbol"] for r in rows}):
    rs = [r for r in rows if r["symbol"] == sym]
    n = len(rs)
    wins = sum(1 for r in rs if r["pts"] > 0)
    tracked = [r for r in rs if r["tracked"]]
    cont = sum(1 for r in tracked if r["up"] >= 10)
    caps = [(r["pts"] / r["peak"] * 100) for r in rs if r["peak"] and r["peak"] > 0 and r["pts"] > 0]   # winners only
    print(f"{sym:<10} {n:>3} {100*wins/n:>4.0f}% {fmt(avg([r['pts'] for r in rs])):>8} {fmt(avg([r['peak'] for r in rs])):>9} "
          f"{fmt(avg(caps)):>9} {fmt(avg([r['hold'] for r in rs]), 0):>9} "
          f"{sum(1 for r in rs if r['reason']=='SL(2%)'):>5} {sum(1 for r in rs if r['reason']=='early-mom'):>6} {sum(1 for r in rs if r['reason']=='trail-tier'):>6} "
          f"{(str(cont)+'/'+str(len(tracked))) if tracked else '-':>17} {fmt(avg([r['up'] for r in tracked])):>12} {fmt(avg([r['dn'] for r in tracked])):>12}")
print()
print("capture% = WINNERS only: points kept / peak points reached while in the trade (100 = exited at the top).")
print("post-exit UP = how far the SAME option rose in the 30 min AFTER our exit (bot's own tracker) = profit we walked away from.")
print("SL2% / e-mom / trail = inferred exit reason counts (2% premium stop / early-momentum cull / trailing tier).\n")

print("=" * 118)
print("EXIT REASON x OUTCOME (all indices)")
print("-" * 118)
by_reason = collections.defaultdict(list)
for r in rows:
    by_reason[r["reason"]].append(r)
print(f"{'reason':<12} {'n':>3} {'avg pts':>8} {'avg peak':>9} {'avg hold':>9} {'post-exit UP>=10':>17} {'avg post UP':>12}")
for reason, rs in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
    tracked = [r for r in rs if r["tracked"]]
    cont = sum(1 for r in tracked if r["up"] >= 10)
    print(f"{reason:<12} {len(rs):>3} {fmt(avg([r['pts'] for r in rs])):>8} {fmt(avg([r['peak'] for r in rs])):>9} {fmt(avg([r['hold'] for r in rs]),0):>9} "
          f"{(str(cont)+'/'+str(len(tracked))) if tracked else '-':>17} {fmt(avg([r['up'] for r in tracked])):>12}")
print()

print("=" * 118)
print("PER-TRADE LIST (oldest first)   'post UP/DN' = same option's move in the 30 min after exit")
print("-" * 118)
print(f"{'date':<11} {'in':<9} {'out':<9} {'index':<10} {'ty':<3} {'kind':<6} {'entry':>8} {'exit':>8} {'pts':>7} {'peak':>7} {'cap%':>6} {'hold':>5} {'reason':<11} {'post UP':>8} {'post DN':>8} {'net Rs':>9}")
for r in rows:
    print(f"{r['date']!s:<11} {r['time']:<9} {r['exit_time']:<9} {r['symbol']:<10} {r['type']:<3} {r['kind']:<6} {r['entry']:>8.2f} {r['exit']:>8.2f} {r['pts']:>7.2f} "
          f"{fmt(r['peak'],2):>7} {fmt(r['cap'],0) if r['cap'] is not None else '-':>6} {fmt(r['hold'],0):>5} {r['reason']:<11} "
          f"{(fmt(r['up'],1) if r['tracked'] else '-'):>8} {(fmt(r['dn'],1) if r['tracked'] else '-'):>8} {('%.0f' % r['net']) if r['net'] is not None else '-':>9}")

print("\nREAD-ONLY report - nothing modified. Exit reasons are inferred from the numbers (the trade log does not store them).")
