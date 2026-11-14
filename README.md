# Tausi Assurance – Refund Endorsement System

Streamlit app that digitises the "Underwriting File Note for Authorising Refund Endorsement" slip.

## Run
    pip install -r requirements.txt
    streamlit run app.py
    python test_calc.py        # optional: verifies the maths

## Demo logins (created on first run – change the passwords from the sidebar!)
| Role | Username | Password |
|---|---|---|
| Underwriter | underwriter1 | Underwriter@123 |
| Claims Officer | claims1 | Claims@123 |
| Head of Department | hod1 | Hod@123 |

## Workflow
1. **Underwriter** fills the slip, adds the fleet, signs, clicks *Sign & send to Claims*.
2. **Claims** marks each vehicle (claim registered / not), comments, signs → returns to Underwriter.
3. **Underwriter** clicks *Send to Head of Department*.
4. **HOD** reviews, signs under "Schedule checked by" → returns to Underwriter.
5. **Underwriter** downloads the FINAL PDF for approval (the "Refund approved by" block is left blank for the approver).
Claims or HOD can *Return for correction* with a reason; the note goes back to the Underwriter as a draft and the
Claims/HOD sign-offs are cleared so they are redone after any change.

## Calculation rules (calc.py – exact `Decimal` arithmetic)
* Annual premium = Sum insured × Rate% ÷ 100 (2 dp, half-up)
* Cancellation / Deletion: premium base = the vehicle's annual premium
* Revision of sum insured: premium base = old annual premium − new annual premium (new rate defaults to old rate)
* Pro-rata: base × unexpired days ÷ days in policy period (both dates inclusive; effective date = first uncovered day).
  Optional 365-day denominator. Annual: full base, no apportionment.
* Each vehicle's basic refund is rounded once (half-up) and its levy is rounded separately; total = sum of the rounded figures.
  Rounding is to WHOLE NUMBERS (`ROUNDING_DP = 0` at the top of calc.py; set 2 for cents). Annual premium stays in cents.
* **Excess Protector (E.P.) and P.V.T** (tick per vehicle): each is charged at 0.25% of the sum insured and refunded on the
  same reason / basis / time factor as the basic premium (for a revision: based on old SI minus new SI). Basic, E.P. and
  P.V.T are each rounded to a whole number, then added. Rates are `EP_RATE` / `PVT_RATE` at the top of calc.py.
* Vehicles with a claim registered by Claims are excluded from the total (per the slip's note).
* Optional levies/duties % applied to each vehicle's rounded gross refund (basic + E.P. + P.V.T) (default 0).

## Deployment notes
* Streamlit Community Cloud's disk is **temporary** – the SQLite file (`refund_app.db`) and saved notes are lost on
  redeploy/restart. Fine for a trial; for production use a persistent DB (Postgres) or a host with a persistent volume.
* Set `REFUND_DB=/path/to/file.db` to relocate the database.
* Passwords are salted PBKDF2-SHA256 hashes. Use HTTPS (Streamlit Cloud does by default).
