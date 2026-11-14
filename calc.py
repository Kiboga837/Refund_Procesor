"""
Refund calculation engine (no UI, no AI, pure exact decimal arithmetic).

RULES (kept in one place so they are easy to audit / change)
-----------------------------------------------------------
1.  Annual premium  = Sum Insured x Rate% / 100, rounded to 2 dp (HALF_UP).
    (This mirrors the premium actually charged on the policy.)
2.  Premium base for the refund
      - Cancellation of policy / Deletion of vehicle : the vehicle's annual premium
      - Revision of sum insured : old annual premium - new annual premium
        (the new rate defaults to the old rate if left blank)
3.  Time factor
      - "Annual"   : 1  (the full premium base is refunded, no time apportionment)
      - "Pro-rata" : unexpired days / days in policy period
          * days in policy period = (expiry - inception) + 1   (both dates inclusive)
          * unexpired days        = (expiry - effective date) + 1
            (the effective date is the FIRST day the vehicle is no longer covered,
             e.g. cancelled with effect from 01/07 -> cover ran to 30/06)
          * optional "365-day year" denominator instead of actual days.
4.  Basic premium refund per vehicle = premium base x time factor, rounded ONCE to
    a whole number (HALF_UP). The fraction is never rounded before the final multiplication.
5.  Excess Protector (E.P.) and P.V.T, when ticked for a vehicle: annual charge = sum insured x 0.25% each
    (EP_RATE / PVT_RATE). They follow the SAME reason, basis and time factor as the basic premium
    (for a revision they are based on old sum insured minus new sum insured). Each of basic, E.P.
    and P.V.T is rounded separately to a whole number, then added: gross refund per vehicle.
5b. Levies per vehicle = rounded gross refund (basic + E.P. + P.V.T) x levy% / 100, rounded separately.
6.  Total = sum of the rounded gross refunds + sum of the rounded levies, so every
    printed figure adds up exactly. Rounding precision is ROUNDING_DP (0 = whole numbers).
7.  A vehicle for which Claims has registered a claim in the current period is
    EXCLUDED (refund and levy both 0), per the slip: "NO REFUND IS TO BE PASSED IF
    A CLAIM HAS BEEN REGISTERED WITHIN THE CURRENT PERIOD OF INSURANCE".
"""
from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation, getcontext

getcontext().prec = 34
ROUNDING_DP = 0          # decimals for refund & levy amounts: 0 = whole shillings, 2 = cents
CENT = Decimal("0.01")
UNIT = Decimal(1).scaleb(-ROUNDING_DP)
ZERO = Decimal("0")
HUNDRED = Decimal("100")

CANCELLATION = "Cancellation of policy"
DELETION = "Deletion of vehicle(s)"
REVISION = "Revision of sum insured"
REASONS = [CANCELLATION, DELETION, REVISION]
SHORT_REASON = {CANCELLATION: "Cancellation", DELETION: "Deletion", REVISION: "Revision of SI"}

PRO_RATA = "Pro-rata"
ANNUAL = "Annual"
BASES = [PRO_RATA, ANNUAL]

DAYS_ACTUAL = "Actual days in policy period"
DAYS_365 = "365-day year"
DAY_BASES = [DAYS_ACTUAL, DAYS_365]

CLAIM_NONE = "No claim registered"
CLAIM_REGISTERED = "Claim registered"
CLAIM_OPTIONS = [CLAIM_NONE, CLAIM_REGISTERED]

CURRENCY = "KES"

# Optional covers refunded together with the basic premium. Each is charged at this % of the SUM INSURED
# and is refunded on the same reason / basis / time factor as the basic premium.
EP_RATE = Decimal("0.25")        # Excess Protector
PVT_RATE = Decimal("0.25")       # Political Violence & Terrorism (P.V.T)


# ----------------------------------------------------------------- helpers
def _blank(x) -> bool:
    if x is None:
        return True
    if isinstance(x, float) and math.isnan(x):
        return True
    if isinstance(x, str) and x.strip() == "":
        return True
    return False


def to_dec(x) -> Decimal | None:
    """Convert user input to Decimal safely. Returns None if blank."""
    if _blank(x):
        return None
    try:
        if isinstance(x, float):
            return Decimal(repr(x))          # shortest exact repr, no binary noise
        return Decimal(str(x).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f"'{x}' is not a valid number")


def money(x: Decimal) -> Decimal:
    """Round to cents (used for the annual premium, which is charged in cents)."""
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def rnd(x: Decimal) -> Decimal:
    """Round a refund / levy amount (ROUNDING_DP decimals, half-up)."""
    return x.quantize(UNIT, rounding=ROUND_HALF_UP)


def fmt(x: Decimal | None) -> str:
    """Format a premium / sum insured (always cents)."""
    return "" if x is None else f"{x:,.2f}"


def fmt_amt(x: Decimal | None) -> str:
    """Format a refund / levy / total amount at the rounding precision (whole numbers by default)."""
    return "" if x is None else f"{x:,.{ROUNDING_DP}f}"


def parse_date(x) -> date | None:
    if _blank(x):
        return None
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    if type(x).__name__ == "datetime64":            # numpy datetime64
        try:
            return datetime.utcfromtimestamp(x.astype("datetime64[s]").astype("int64")).date()
        except Exception:                            # noqa: BLE001
            return None
    s = str(x).strip()
    for fmt_, cut in (("%Y-%m-%d", 10), ("%d/%m/%Y", 10), ("%d-%m-%Y", 10), ("%d/%m/%y", 8)):
        try:
            return datetime.strptime(s[:cut], fmt_).date()
        except ValueError:
            continue
    return None


def dmy(x) -> str:
    d = parse_date(x)
    return d.strftime("%d/%m/%Y") if d else ""


def period_days(start: date, end: date, day_basis: str = DAYS_ACTUAL) -> int:
    if end < start:
        raise ValueError("Policy expiry date is before the inception date")
    if day_basis == DAYS_365:
        return 365
    return (end - start).days + 1


def unexpired_days(effective: date, end: date) -> int:
    return max(0, (end - effective).days + 1)


# ---------------------------------------------------------------- one line
def compute_line(line: dict, start: date, end: date, day_basis: str, levy_pct: Decimal = ZERO) -> dict:
    """Compute one vehicle. Never raises: problems are returned in row['errors']."""
    errors: list[str] = []
    rtype = line.get("type")
    basis = line.get("basis")
    eff = parse_date(line.get("eff"))
    row = {
        "reg": (line.get("reg") or "").strip(),
        "desc": (line.get("desc") or "").strip(),
        "type": rtype, "basis": basis, "eff": eff,
        "si": None, "rate": None, "new_si": None, "new_rate": None,
        "old_prem": None, "new_prem": None, "base": None,
        "unexp": None, "total_days": None, "factor": None,
        "calc_refund": ZERO, "refund": ZERO, "levy": ZERO, "line_total": ZERO,
        "ep": bool(line.get("ep")), "pvt": bool(line.get("pvt")),
        "ep_prem": None, "pvt_prem": None, "ep_new_prem": None, "pvt_new_prem": None,
        "basic_refund": ZERO, "ep_refund": ZERO, "pvt_refund": ZERO,
        "excluded": line.get("claim") == CLAIM_REGISTERED,
        "claim": line.get("claim") or CLAIM_NONE,
        "errors": errors,
    }
    if not row["reg"]:
        errors.append("Registration number is required")
    if rtype not in REASONS:
        errors.append("Select the reason (cancellation / deletion / revision)")
    if basis not in BASES:
        errors.append("Select the basis (Pro-rata / Annual)")

    try:
        si, rate = to_dec(line.get("si")), to_dec(line.get("rate"))
        new_si, new_rate = to_dec(line.get("new_si")), to_dec(line.get("new_rate"))
    except ValueError as e:
        errors.append(str(e))
        return row

    if si is None or si <= 0:
        errors.append("Sum insured must be greater than 0")
    if rate is None or rate <= 0:
        errors.append("Rate must be greater than 0")
    if errors:
        return row
    row["si"], row["rate"] = si, rate

    old_prem = money(si * rate / HUNDRED)
    row["old_prem"] = old_prem

    if rtype == REVISION:
        if new_si is None or new_si < 0:
            errors.append("Enter the new (revised) sum insured")
            return row
        if new_rate is None or new_rate == 0:
            new_rate = rate
        if new_rate < 0:
            errors.append("New rate cannot be negative")
            return row
        row["new_si"], row["new_rate"] = new_si, new_rate
        new_prem = money(new_si * new_rate / HUNDRED)
        row["new_prem"] = new_prem
        base = old_prem - new_prem
        if base <= 0:
            errors.append("Revision gives no refund (new premium is not lower than old premium)")
            return row
    else:
        base = old_prem
    row["base"] = base

    if basis == ANNUAL:
        factor = Decimal(1)
    else:
        if eff is None:
            raw = line.get("eff")
            errors.append("Effective date is required for a pro-rata refund" if _blank(raw)
                          else f"Could not read the effective date '{raw}'")
            return row
        if eff < start or eff > end:
            errors.append(f"Effective date {dmy(eff)} is outside the policy period "
                          f"({dmy(start)} to {dmy(end)}) - check the policy period dates above the table")
            return row
        total = period_days(start, end, day_basis)
        unexp = unexpired_days(eff, end)
        row["total_days"], row["unexp"] = total, unexp
        factor = min(Decimal(unexp) / Decimal(total), Decimal(1))
    row["factor"] = factor

    basic = rnd(base * factor)
    ep = pvt = ZERO
    new_si_for_extras = row["new_si"] if rtype == REVISION else None
    for key, flag, pct in (("ep", row["ep"], EP_RATE), ("pvt", row["pvt"], PVT_RATE)):
        if not flag:
            continue
        prem = money(si * pct / HUNDRED)
        extra_base = prem
        row[f"{key}_prem"] = prem
        if new_si_for_extras is not None:
            new_prem_x = money(new_si_for_extras * pct / HUNDRED)
            row[f"{key}_new_prem"] = new_prem_x
            extra_base = max(prem - new_prem_x, ZERO)
        amount = rnd(extra_base * factor)
        if key == "ep":
            ep = amount
        else:
            pvt = amount

    calc_gross = basic + ep + pvt
    row["calc_refund"] = calc_gross                      # before any claim exclusion
    if row["excluded"]:
        basic = ep = pvt = ZERO
    row["basic_refund"], row["ep_refund"], row["pvt_refund"] = basic, ep, pvt
    row["refund"] = basic + ep + pvt                     # gross refund for the vehicle
    row["levy"] = rnd(row["refund"] * levy_pct / HUNDRED)
    row["line_total"] = row["refund"] + row["levy"]
    return row


# ---------------------------------------------------------------- document
def compute_document(data: dict) -> dict:
    """Compute the whole fleet. Returns rows, subtotal, levies, total, errors, ok."""
    errors: list[str] = []
    start, end = parse_date(data.get("period_start")), parse_date(data.get("period_end"))
    if not start or not end:
        errors.append("Enter the policy period (inception and expiry dates)")
        start = end = date.today()
    elif end < start:
        errors.append("Policy expiry date is before the inception date")
    day_basis = data.get("day_basis") or DAYS_ACTUAL

    try:
        levy_pct = to_dec(data.get("levy_pct")) or ZERO
        if levy_pct < 0 or levy_pct > 100:
            errors.append("Levy percentage must be between 0 and 100")
            levy_pct = ZERO
    except ValueError as e:
        errors.append(str(e))
        levy_pct = ZERO

    lines = data.get("lines") or []
    if not lines:
        errors.append("Add at least one vehicle")

    rows = []
    if end >= start:
        rows = [compute_line(ln, start, end, day_basis, levy_pct) for ln in lines]

    for i, r in enumerate(rows, 1):
        r["no"] = i
        for e in r["errors"]:
            errors.append(f"Vehicle {i} ({r['reg'] or 'no reg'}): {e}")

    basic_total = sum((r["basic_refund"] for r in rows), ZERO)
    ep_total = sum((r["ep_refund"] for r in rows), ZERO)
    pvt_total = sum((r["pvt_refund"] for r in rows), ZERO)
    subtotal = sum((r["refund"] for r in rows), ZERO)
    levies = sum((r["levy"] for r in rows), ZERO)
    return {
        "rows": rows, "basic_total": basic_total, "ep_total": ep_total, "pvt_total": pvt_total,
        "has_extras": any(r["ep"] or r["pvt"] for r in rows), "subtotal": subtotal, "levy_pct": levy_pct, "levies": levies,
        "total": subtotal + levies, "errors": errors, "ok": not errors and bool(rows),
        "excluded_count": sum(1 for r in rows if r["excluded"]),
    }
