"""Run:  python test_calc.py   (independent hand-checked cases)"""
from datetime import date
from decimal import Decimal as D
import calc

BASE = {"period_start": "2026-01-01", "period_end": "2026-12-31", "levy_pct": 0}

def doc(lines, **kw):
    return calc.compute_document({**BASE, **kw, "lines": lines})

def ln(**k):
    d = {"reg": "KAA 001A", "type": calc.DELETION, "basis": calc.PRO_RATA, "eff": "2026-07-01",
         "si": 1000000, "rate": 4}
    d.update(k); return d

# 1. deletion pro-rata: premium 40,000.00 ; unexpired 1 Jul..31 Dec = 184 ; 40000*184/365 = 20164.3835 -> 20164.38
r = doc([ln()]); assert r["ok"], r["errors"]; assert r["total"] == D("20164"), r["total"]
assert r["rows"][0]["unexp"] == 184 and r["rows"][0]["total_days"] == 365

# 2. annual basis -> full premium
r = doc([ln(basis=calc.ANNUAL)]); assert r["total"] == D("40000.00")

# 3. cancellation effective on inception -> full refund ; effective on last day -> 1/365
assert doc([ln(eff="2026-01-01")])["total"] == D("40000.00")
assert doc([ln(eff="2026-12-31")])["total"] == D("110")   # 40000/365 = 109.589..

# NOTE: refunds and levies are rounded to WHOLE NUMBERS (ROUNDING_DP = 0)
# 4. revision of SI: old 1,000,000@4% = 40,000 ; new 800,000@4% = 32,000 ; diff 8,000 ; 8000*184/365 = 4032.88 -> 4033
r = doc([ln(type=calc.REVISION, new_si=800000)]); assert r["total"] == D("4033"), r["total"]
#    revision with rate change: new 800,000@3.5% = 28,000 ; diff 12,000 ; *184/365 = 6049.3151
r = doc([ln(type=calc.REVISION, new_si=800000, new_rate=3.5)]); assert r["total"] == D("6049"), r["total"]
#    revision that raises SI is NOT a refund
r = doc([ln(type=calc.REVISION, new_si=1200000)]); assert not r["ok"]

# 5. rounding: HALF_UP, no float noise.  SI 1,234,567.89 @ 3.75% = 46,296.29588 -> 46,296.30 premium
r = doc([ln(si=1234567.89, rate=3.75, basis=calc.ANNUAL)]); assert r["rows"][0]["old_prem"] == D("46296.30")
# classic float trap: 0.285 style values stay exact
assert calc.money(D("2.675")) == D("2.68")

# 6. fleet: total equals sum of rounded lines
fleet = [ln(reg="A", si=850000, rate=3.25, eff="2026-03-15"),
         ln(reg="B", si=2300000, rate=2.75, eff="2026-09-30", type=calc.CANCELLATION),
         ln(reg="C", si=1500000, rate=4.5, type=calc.REVISION, new_si=1200000, eff="2026-05-01")]
r = doc(fleet); assert r["ok"]
assert r["total"] == sum(x["refund"] for x in r["rows"])
# hand check A: prem 27,625.00 ; days 15 Mar..31 Dec = 292 ; 27625*292/365 = 22,100.00 exactly
assert r["rows"][0]["refund"] == D("22100.00"), r["rows"][0]["refund"]
# hand check B: prem 63,250.00 ; 30 Sep..31 Dec = 93 ; 63250*93/365 = 16,115.7534 -> 16,115.75
assert r["rows"][1]["refund"] == D("16116"), r["rows"][1]["refund"]
# hand check C: 67,500 - 54,000 = 13,500 ; 1 May..31 Dec = 245 ; 13500*245/365 = 9,061.6438 -> 9,061.64
assert r["rows"][2]["refund"] == D("9062"), r["rows"][2]["refund"]
assert r["total"] == D("47278"), r["total"]

# 7. claim registered => vehicle excluded from total
f2 = [dict(x) for x in fleet]; f2[1]["claim"] = calc.CLAIM_REGISTERED
r = doc(f2); assert r["total"] == D("31162") and r["excluded_count"] == 1, r["total"]

# 8. levies
r = doc(fleet, levy_pct=0.45)
# per-vehicle: 22100.00->99.45 ; 16115.75->72.52 (72.522) ; 9061.64->40.78 (40.779)
assert [x["levy"] for x in r["rows"]] == [D("99"), D("73"), D("41")], [x["levy"] for x in r["rows"]]
assert r["levies"] == D("213") and r["total"] == D("47278") + D("213")
assert r["total"] == sum(x["refund"] + x["levy"] for x in r["rows"])

# 9. leap year (366 days) and 365-day option
r = doc([ln(eff="2024-07-01")], period_start="2024-01-01", period_end="2024-12-31")
assert r["rows"][0]["total_days"] == 366 and r["rows"][0]["unexp"] == 184
assert r["total"] == calc.rnd(D("40000") * 184 / 366)
r = doc([ln(eff="2024-07-01")], period_start="2024-01-01", period_end="2024-12-31", day_basis=calc.DAYS_365)
assert r["total"] == calc.rnd(D("40000") * 184 / 365)

# 10. validation
assert not doc([ln(eff="2025-12-31")])["ok"]       # before inception
assert not doc([ln(si=0)])["ok"] and not doc([ln(reg="")])["ok"] and not doc([])["ok"]
assert not doc([ln()], period_end="2025-01-01")["ok"]
assert not doc([ln(si="abc")])["ok"]
assert doc([ln(si="1,000,000")])["ok"]              # comma input accepted

# 11. Excess Protector + PVT (each 0.25% of sum insured, same basis / time factor, each rounded to a whole number)
# deletion 1 Jul: basic 40000*184/365 = 20164.38 -> 20164 ; EP 2500*184/365 = 1260.27 -> 1260 ; PVT same -> 1260
r = doc([ln(ep=True, pvt=True)]); x = r["rows"][0]
assert (x["basic_refund"], x["ep_refund"], x["pvt_refund"]) == (D("20164"), D("1260"), D("1260")), x
assert x["ep_prem"] == D("2500.00") and x["pvt_prem"] == D("2500.00")
assert x["refund"] == D("22684") and r["total"] == D("22684") and r["has_extras"]
#    only one of them ticked
r = doc([ln(ep=True)]); assert r["rows"][0]["ep_refund"] == D("1260") and r["rows"][0]["pvt_refund"] == 0 and r["total"] == D("21424")
r = doc([ln(pvt=True)]); assert r["rows"][0]["pvt_refund"] == D("1260") and r["rows"][0]["ep_refund"] == 0
#    not ticked -> unchanged behaviour
assert doc([ln()])["total"] == D("20164") and not doc([ln()])["has_extras"]
#    annual basis: full 2,500 each
r = doc([ln(basis=calc.ANNUAL, ep=True, pvt=True)]); assert r["total"] == D("45000")
#    revision 1,000,000 -> 800,000: EP/PVT 2500 -> 2000, diff 500 * 184/365 = 252.05 -> 252 each ; basic 4033
r = doc([ln(type=calc.REVISION, new_si=800000, ep=True, pvt=True)]); x = r["rows"][0]
assert (x["basic_refund"], x["ep_refund"], x["pvt_refund"]) == (D("4033"), D("252"), D("252")) and r["total"] == D("4537"), x
#    levies are charged on the rounded gross (basic + EP + PVT): 22684 * 0.45% = 102.078 -> 102
r = doc([ln(ep=True, pvt=True)], levy_pct=0.45); assert r["rows"][0]["levy"] == D("102") and r["total"] == D("22786")
#    claim registered excludes basic, EP and PVT together
r = doc([ln(ep=True, pvt=True, claim=calc.CLAIM_REGISTERED)])
assert r["total"] == 0 and r["rows"][0]["calc_refund"] == D("22684")
#    fleet totals add up by component
r = doc([ln(reg="A", ep=True), ln(reg="B", pvt=True, si=2000000), ln(reg="C")])
assert r["subtotal"] == r["basic_total"] + r["ep_total"] + r["pvt_total"] == sum(x["refund"] for x in r["rows"])
print("ALL CALCULATION TESTS PASSED")
