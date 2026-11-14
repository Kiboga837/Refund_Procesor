"""
Tausi Assurance - Refund Endorsement System (Streamlit)
Run:  streamlit run app.py
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

import calc
import db
import pdfgen

st.set_page_config(page_title="Tausi Refund Endorsement System", page_icon="🧾", layout="wide")


@st.cache_resource
def _init():
    db.init_db()
    return True


_init()

LINE_COLS = ["reg", "desc", "type", "basis", "eff", "si", "rate", "new_si", "new_rate", "ep", "pvt"]


# =============================================================== helpers
def go(view: str, rid=None):
    st.session_state["view"], st.session_state["rid"] = view, rid
    st.rerun()


def rec_total(rec) -> str:
    return calc.fmt_amt(calc.compute_document(rec["data"])["total"])


def pdf_button(rec, label="Download PDF", key=None, on_click=None, args=None):
    final = rec["status"] == db.FINAL
    name = f"{rec['ref_no']}{'_FINAL' if final else '_' + rec['status']}.pdf"
    st.download_button(label, data=pdfgen.build_pdf(rec), file_name=name, mime="application/pdf",
                       key=key or f"dl_{rec['id']}_{rec['status']}", type="primary" if final else "secondary",
                       on_click=on_click, args=args)


def _mark_downloaded(rid: int, username: str):
    """Underwriter downloaded the FINAL pdf -> the note moves to the Attended inbox."""
    rec = db.get_refund(rid)
    db.update_refund(rid, data={**rec["data"], "final_downloaded": True})
    db.add_history(rid, username, "Downloaded final PDF")


def acted_by(rec: dict, username: str) -> bool:
    """True if this user has performed an action on the note (signed / returned / downloaded)."""
    return any(h["actor"] == username for h in db.get_history(rec["id"]))


def inbox_tabs(unattended: list, attended: list, prefix: str, hint_un: str, hint_at: str):
    """Two separate inboxes: Unattended (needs my action) and Attended (already dealt with)."""
    t1, t2 = st.tabs([f"📥 Unattended inbox ({len(unattended)})", f"✅ Attended inbox ({len(attended)})"])
    with t1:
        st.caption(hint_un)
        record_list(unattended, f"{prefix}_un")
    with t2:
        st.caption(hint_at)
        record_list(attended, f"{prefix}_at")


def signature_widget(key: str, user: dict):
    """Returns processed PNG bytes or None. Supports a saved default signature."""
    saved = db.get_signature(user["username"])
    use_saved = "Use my saved signature"
    opts = ([use_saved] if saved else []) + ["Upload a signature image"]
    choice = st.radio("Signature", opts, key=f"{key}_choice", horizontal=True)
    if saved and choice == use_saved:
        st.image(saved, width=220, caption="Saved signature")
        return saved
    up = st.file_uploader("Upload a clear image of your signature (PNG / JPG, dark ink on a plain background)",
                          type=["png", "jpg", "jpeg"], key=f"{key}_up")
    if not up:
        return None
    try:
        png = pdfgen.process_signature(up.getvalue())
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not use this image: {e}")
        return None
    st.image(png, width=220, caption="Signature preview (auto-cropped)")
    if st.checkbox("Save as my default signature for next time", key=f"{key}_save"):
        db.set_signature(user["username"], png)
    return png


def calc_table(res: dict):
    """Display the computed schedule."""
    if not res["rows"]:
        return
    out = []
    for r in res["rows"]:
        out.append({
            "#": r["no"], "Vehicle": f"{r['reg']} {r['desc']}".strip(),
            "Reason": calc.SHORT_REASON.get(r["type"], ""), "Basis": r["basis"] or "",
            "Sum insured": calc.fmt(r["si"]) + (f" -> {calc.fmt(r['new_si'])}" if r["new_si"] is not None else ""),
            "Rate %": f"{r['rate'].normalize():f}" if r["rate"] is not None else "",
            "Annual premium": calc.fmt(r["old_prem"]) + (f" -> {calc.fmt(r['new_prem'])}" if r["new_prem"] is not None else ""),
            "Days": (f"{r['unexp']}/{r['total_days']}" if r["unexp"] is not None else ("Full" if r["basis"] == calc.ANNUAL else "")),
            "Premium base": calc.fmt(r["base"]),
            "Time factor": ("" if r["factor"] is None else f"{r['factor']:.6f}"),
            **({"Basic refund": calc.fmt_amt(r["basic_refund"]),
                "Excess protector": calc.fmt_amt(r["ep_refund"]) if r["ep"] else "-",
                "P.V.T": calc.fmt_amt(r["pvt_refund"]) if r["pvt"] else "-"}
               if res["has_extras"] and r["errors"] == [] else {}),
            "Refund": calc.fmt_amt(r["refund"]) if r["errors"] == [] else "-",
            **({"Levies": calc.fmt_amt(r["levy"]), "Refund + levies": calc.fmt_amt(r["line_total"])}
               if res["levy_pct"] > 0 and r["errors"] == [] else {}),
            "Claim": r["claim"] + (" (EXCLUDED)" if r["excluded"] else ""),
        })
    st.dataframe(pd.DataFrame(out), hide_index=True, use_container_width=True)
    if res["has_extras"]:
        m1, m2, m3 = st.columns(3)
        m1.metric("Basic premium refund", f"{calc.CURRENCY} {calc.fmt_amt(res['basic_total'])}")
        m2.metric("Excess protector refund", f"{calc.CURRENCY} {calc.fmt_amt(res['ep_total'])}")
        m3.metric("P.V.T refund", f"{calc.CURRENCY} {calc.fmt_amt(res['pvt_total'])}")
    c1, c2, c3 = st.columns(3)
    c1.metric("Sub-total", f"{calc.CURRENCY} {calc.fmt_amt(res['subtotal'])}")
    if res["levy_pct"] > 0:
        c2.metric("Levies / duties", f"{calc.CURRENCY} {calc.fmt_amt(res['levies'])}")
    c3.metric("TOTAL REFUND", f"{calc.CURRENCY} {calc.fmt_amt(res['total'])}")


def render_record(rec: dict):
    """Read-only view of a refund note."""
    d = rec["data"]
    st.subheader(f"{rec['ref_no']} - {db.STATUS_LABEL[rec['status']]}")
    if d.get("return_note"):
        st.warning(f"Returned for correction: {d['return_note']}")
    a, b = st.columns(2)
    a.write(f"**Department:** {d.get('department', '')}\n\n**Policy No:** {d.get('policy_no', '')}\n\n"
            f"**Agent/Broker:** {d.get('agent', '')}")
    b.write(f"**Insured:** {d.get('insured', '')}\n\n**Policy period:** {calc.dmy(d.get('period_start'))} "
            f"to {calc.dmy(d.get('period_end'))}\n\n**Prepared by:** {(d.get('uw') or {}).get('name', '')}")
    st.markdown("**Brief details**")
    st.info(d.get("brief", ""))
    calc_table(calc.compute_document(d))
    if d.get("claims"):
        cl = d["claims"]
        st.markdown("**Comment on claims status**")
        st.info(f"{cl.get('comment', '')}\n\n— {cl.get('name', '')}, {calc.dmy(cl.get('date'))}")
    with st.expander("Audit trail"):
        st.dataframe(pd.DataFrame(db.get_history(rec["id"])), hide_index=True, use_container_width=True)


def record_list(recs, prefix: str):
    if not recs:
        st.info("Nothing here yet.")
        return
    h = st.columns([1.3, 2.2, 1.8, 2.2, 1.5, 1.6, 0.8])
    for c, t in zip(h, ["Ref", "Insured", "Policy no", "Status", "Total refund", "Updated", ""]):
        c.markdown(f"**{t}**")
    for r in recs:
        c = st.columns([1.3, 2.2, 1.8, 2.2, 1.5, 1.6, 0.8])
        d = r["data"]
        c[0].write(r["ref_no"]); c[1].write(d.get("insured") or "-"); c[2].write(d.get("policy_no") or "-")
        c[3].write(db.STATUS_LABEL[r["status"]]); c[4].write(f"{rec_total(r)}")
        c[5].write(r["updated_at"][:16])
        if c[6].button("Open", key=f"{prefix}_open_{r['id']}"):
            go("open", r["id"])


# ================================================================== login
def login_page():
    st.title("🧾 Tausi Assurance - Refund Endorsement System")
    _, mid, _ = st.columns([1, 1.2, 1])
    with mid, st.form("login"):
        st.subheader("Sign in")
        u = st.text_input("Username")
        p = st.text_input("Password", type="password")
        if st.form_submit_button("Sign in", type="primary"):
            user = db.verify_login(u, p)
            if user:
                st.session_state["user"] = user
                st.session_state["view"], st.session_state["rid"] = "list", None
                st.rerun()
            st.error("Invalid username or password")


def sidebar(user):
    with st.sidebar:
        st.markdown(f"### {user['full_name']}\n*{db.ROLE_LABEL[user['role']]}*")
        with st.expander("My saved signature"):
            sig = db.get_signature(user["username"])
            if sig:
                st.image(sig, width=200)
            up = st.file_uploader("Upload / replace", type=["png", "jpg", "jpeg"], key="prof_sig")
            if up and st.button("Save signature"):
                try:
                    db.set_signature(user["username"], pdfgen.process_signature(up.getvalue()))
                    st.success("Saved"); st.rerun()
                except Exception as e:  # noqa: BLE001
                    st.error(str(e))
        with st.expander("Change password"):
            with st.form("pw"):
                old, new, new2 = (st.text_input("Current password", type="password"),
                                  st.text_input("New password (min 8 chars)", type="password"),
                                  st.text_input("Repeat new password", type="password"))
                if st.form_submit_button("Update"):
                    if not db.verify_login(user["username"], old):
                        st.error("Current password is wrong")
                    elif len(new) < 8 or new != new2:
                        st.error("New passwords must match and be at least 8 characters")
                    else:
                        db.change_password(user["username"], new); st.success("Password changed")
        if st.button("Sign out"):
            st.session_state.clear(); st.rerun()


# ============================================================ UNDERWRITER
def df_to_lines(df: pd.DataFrame) -> list[dict]:
    lines = []
    for _, r in df.iterrows():
        def val(c):
            v = r.get(c)
            return None if pd.isna(v) else v
        reg = str(val("reg") or "").strip()
        if not reg and val("si") is None:
            continue                                    # ignore empty rows
        eff_raw = val("eff")
        eff = calc.parse_date(eff_raw)             # accepts date / Timestamp / datetime64 / text
        lines.append({
            "reg": reg, "desc": str(val("desc") or "").strip(), "type": val("type"), "basis": val("basis"),
            "eff": eff.isoformat() if eff else (None if eff_raw is None else str(eff_raw)),
            "si": None if val("si") is None else repr(float(val("si"))),
            "rate": None if val("rate") is None else repr(float(val("rate"))),
            "new_si": None if val("new_si") is None else repr(float(val("new_si"))),
            "new_rate": None if val("new_rate") is None else repr(float(val("new_rate"))),
            "ep": bool(val("ep")), "pvt": bool(val("pvt")),
            "claim": calc.CLAIM_NONE,
        })
    return lines


def lines_to_df(lines: list[dict]) -> pd.DataFrame:
    if not lines:
        lines = [{"type": calc.DELETION, "basis": calc.PRO_RATA}]
    rows = []
    for l in lines:
        rows.append({
            "reg": l.get("reg", ""), "desc": l.get("desc", ""), "type": l.get("type"), "basis": l.get("basis"),
            "eff": calc.parse_date(l.get("eff")),
            **{c: (float(l[c]) if l.get(c) not in (None, "") else None) for c in ("si", "rate", "new_si", "new_rate")},
            "ep": bool(l.get("ep")), "pvt": bool(l.get("pvt")),
        })
    df = pd.DataFrame(rows, columns=LINE_COLS)
    df["eff"] = df["eff"].astype(object)
    for c in ("si", "rate", "new_si", "new_rate"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in ("ep", "pvt"):
        df[c] = df[c].fillna(False).astype(bool)
    return df


def uw_edit(rec: dict | None, user: dict):
    rid = rec["id"] if rec else "new"
    d = rec["data"] if rec else {}
    K = lambda n: f"{n}_{rid}"  # noqa: E731

    if rec:
        st.subheader(f"{rec['ref_no']} - Draft")
        if d.get("return_note"):
            st.warning(f"Returned for correction: {d['return_note']}")
    else:
        st.subheader("New refund note")

    c1, c2 = st.columns(2)
    department = c1.text_input("Department", d.get("department", ""), key=K("dept"))
    insured = c2.text_input("Insured", d.get("insured", ""), key=K("ins"))
    policy_no = c1.text_input("Policy No", d.get("policy_no", ""), key=K("pol"))
    agent = c2.text_input("Agent / Broker", d.get("agent", ""), key=K("agent"))
    p1, p2 = st.columns(2)
    start = p1.date_input("Policy period - from (inception)", calc.parse_date(d.get("period_start")) or date.today(),
                          key=K("ps"), format="DD/MM/YYYY")
    end = p2.date_input("Policy period - to (expiry)",
                        calc.parse_date(d.get("period_end")) or (date.today() + timedelta(days=364)),
                        key=K("pe"), format="DD/MM/YYYY")
    o1, o2 = st.columns(2)
    day_basis = o1.selectbox("Pro-rata day count", calc.DAY_BASES,
                             index=calc.DAY_BASES.index(d.get("day_basis", calc.DAYS_ACTUAL)), key=K("db"))
    levy = o2.number_input("Statutory levies/duties to refund (% of refund) - leave 0 if not applicable",
                           0.0, 100.0, float(d.get("levy_pct", 0) or 0), step=0.05, format="%.4f", key=K("levy"))
    brief = st.text_area("Brief details (why the refund is being passed)", d.get("brief", ""), height=110, key=K("brief"))

    st.markdown("#### Vehicles to be refunded (fleet)")
    st.caption("Add one row per vehicle (use the empty bottom row to add more). For *Revision of sum insured*, enter the "
               "current Sum Insured/Rate and the New Sum Insured (New Rate is optional - blank means unchanged). "
               "Effective date = first day the vehicle is no longer covered / the revision takes effect. "
               "Tick *Excess protector* and/or *P.V.T* when those covers are refunded too - each is charged at 0.25% "
               "of the sum insured and refunded on the same basis as the basic premium.")
    base_key = K("base_df")
    if base_key not in st.session_state:
        st.session_state[base_key] = lines_to_df(d.get("lines", []))
    edited = st.data_editor(
        st.session_state[base_key], num_rows="dynamic", use_container_width=True, key=K("editor"),
        column_config={
            "reg": st.column_config.TextColumn("Reg. No.", required=True),
            "desc": st.column_config.TextColumn("Make / model"),
            "type": st.column_config.SelectboxColumn("Reason", options=calc.REASONS, default=calc.DELETION, required=True),
            "basis": st.column_config.SelectboxColumn("Basis", options=calc.BASES, default=calc.PRO_RATA, required=True),
            "eff": st.column_config.DateColumn("Effective date", format="DD/MM/YYYY"),
            "si": st.column_config.NumberColumn("Sum insured", min_value=0.0, format="%.2f"),
            "rate": st.column_config.NumberColumn("Rate %", min_value=0.0, format="%.4f"),
            "new_si": st.column_config.NumberColumn("New sum insured (revision)", min_value=0.0, format="%.2f"),
            "new_rate": st.column_config.NumberColumn("New rate % (revision)", min_value=0.0, format="%.4f"),
            "ep": st.column_config.CheckboxColumn("Excess protector (0.25%)", default=False,
                                                  help="Tick if the Excess Protector is also being refunded"),
            "pvt": st.column_config.CheckboxColumn("P.V.T (0.25%)", default=False,
                                                   help="Tick if the P.V.T cover is also being refunded"),
        })

    data = {**d, "department": department, "insured": insured, "policy_no": policy_no, "agent": agent,
            "period_start": start.isoformat(), "period_end": end.isoformat(), "day_basis": day_basis,
            "levy_pct": repr(levy), "brief": brief, "lines": df_to_lines(edited)}
    res = calc.compute_document(data)
    st.markdown("#### Refund workings (calculated automatically)")
    for e in res["errors"]:
        st.error(e)
    calc_table(res)

    st.markdown("#### Schedule prepared by")
    s1, s2 = st.columns(2)
    name = s1.text_input("Name", (d.get("uw") or {}).get("name", user["full_name"]), key=K("uwname"))
    sdate = s2.date_input("Date", calc.parse_date((d.get("uw") or {}).get("date")) or date.today(),
                          key=K("uwdate"), format="DD/MM/YYYY")
    sig = signature_widget(K("uwsig"), user)
    data["uw"] = {"name": name, "date": sdate.isoformat(), "user": user["username"]}

    b1, b2, b3 = st.columns([1, 1.6, 4])
    if b1.button("💾 Save draft", key=K("save")):
        saved_id = _save(rec, data, user)
        st.toast("Draft saved")
        go("open", saved_id)
    if b2.button("📤 Sign & send to Claims", type="primary", key=K("send")):
        problems = [p for p, ok in [
            ("Department", department.strip()), ("Insured", insured.strip()), ("Policy No", policy_no.strip()),
            ("Agent/Broker", agent.strip()), ("Brief details", brief.strip()), ("Your name", name.strip()),
            ("Your signature image", sig), ("The calculation has no errors", res["ok"])] if not ok]
        if problems:
            st.error("Please complete: " + ", ".join(problems))
        else:
            data.pop("return_note", None)
            rid_saved = _save(rec, data, user)
            db.update_refund(rid_saved, status=db.WITH_CLAIMS, uw_sig=sig)
            db.add_history(rid_saved, user["username"], "Signed and sent to Claims")
            st.success("Sent to Claims")
            go("open", rid_saved)


def _save(rec, data, user) -> int:
    # An editable draft never carries claims / HOD sign-offs (they are redone after any edit)
    data.pop("claims", None)
    data.pop("hod", None)
    if rec:
        db.update_refund(rec["id"], data=data, claims_sig=None, hod_sig=None)
        st.session_state["rid"] = rec["id"]
        return rec["id"]
    rid = db.create_refund(user["username"], data)
    st.session_state["rid"] = rid
    for k in [k for k in st.session_state if str(k).endswith("_new")]:
        del st.session_state[k]          # reset the blank "new note" form
    return rid


def underwriter_page(user):
    nav = st.sidebar.radio("Menu", ["My refund notes", "New refund note"], key="uw_nav")
    if st.session_state.get("last_nav") != nav:
        st.session_state["last_nav"] = nav
        st.session_state["view"], st.session_state["rid"] = ("new", None) if nav == "New refund note" else ("list", None)
    view, rid = st.session_state.get("view", "list"), st.session_state.get("rid")

    if view == "new":
        uw_edit(None, user)
    elif view == "open" and rid:
        rec = db.get_refund(rid)
        if st.button("← Back to list"):
            go("list")
        if rec["status"] == db.DRAFT:
            uw_edit(rec, user)
            return
        render_record(rec)
        if rec["status"] == db.FINAL and not rec["data"].get("final_downloaded"):
            pdf_button(rec, "⬇ Download FINAL PDF for submission", on_click=_mark_downloaded,
                       args=(rec["id"], user["username"]))
        else:
            pdf_button(rec, "⬇ Download current PDF" if rec["status"] != db.FINAL else "⬇ Download FINAL PDF")
        if rec["status"] == db.CLAIMS_DONE:
            if rec["data"].get("claims") and calc.compute_document(rec["data"])["excluded_count"]:
                st.warning("Claims has marked one or more vehicles as having a registered claim. Those vehicles are "
                           "excluded from the total refund.")
            if st.button("📤 Send to Head of Department", type="primary"):
                db.update_refund(rid, status=db.WITH_HOD)
                db.add_history(rid, user["username"], "Sent to Head of Department")
                go("open", rid)
        elif rec["status"] == db.FINAL:
            st.success("Checked by Head of Department. Download the PDF above for final submission for approval.")
    else:
        mine = [r for r in db.list_refunds() if r["created_by"] == user["username"]]
        st.title("My refund notes")

        def needs_my_action(r):
            return (r["status"] in (db.DRAFT, db.CLAIMS_DONE)
                    or (r["status"] == db.FINAL and not r["data"].get("final_downloaded")))

        inbox_tabs([r for r in mine if needs_my_action(r)], [r for r in mine if not needs_my_action(r)], "uw",
                   "Drafts and returned notes, notes back from Claims (to send to HOD), and checked notes waiting "
                   "to be downloaded.",
                   "Notes you have sent on and that are with Claims or the Head of Department, and finished notes "
                   "you have already downloaded.")


# ======================================================= CLAIMS OFFICER
def staff_inbox(user, title, pending_status, review_fn):
    """Inbox for Claims / HOD: Unattended = waiting for me; Attended = notes I have already acted on."""
    view, rid = st.session_state.get("view", "list"), st.session_state.get("rid")
    if view == "open" and rid:
        rec = db.get_refund(rid)
        if st.button("← Back"):
            go("list")
        if rec["status"] == pending_status:
            review_fn(rec, user)
        else:
            render_record(rec)
        pdf_button(rec)
        return
    recs = db.list_refunds()
    st.title(title)
    unattended = [r for r in recs if r["status"] == pending_status]
    attended = [r for r in recs if r["status"] != pending_status and acted_by(r, user["username"])]
    inbox_tabs(unattended, attended, "st",
               "Notes sent to you that still need your review and signature.",
               "Notes you have already signed or returned for correction (you can open them and download the PDF).")


def claims_page(user):
    staff_inbox(user, "Claims Department", db.WITH_CLAIMS, _claims_review)


def _claims_review(rec, user):
    d, rid = rec["data"], rec["id"]
    render_record(rec)
    st.markdown("---\n#### Comment on claims status")
    st.caption("NO REFUND IS TO BE PASSED IF A CLAIM HAS BEEN REGISTERED WITHIN THE CURRENT PERIOD OF INSURANCE. "
               "Vehicles you mark as 'Claim registered' are automatically excluded from the total refund.")
    lines = d["lines"]
    status = {}
    for i, l in enumerate(lines):
        status[i] = st.selectbox(f"{i + 1}. {l['reg']} {l.get('desc', '')}", calc.CLAIM_OPTIONS,
                                 index=calc.CLAIM_OPTIONS.index(l.get("claim", calc.CLAIM_NONE)), key=f"cs_{rid}_{i}")
    comment = st.text_area("Comment (claim numbers, dates of loss, status, etc.)", key=f"cc_{rid}", height=110)
    c1, c2 = st.columns(2)
    name = c1.text_input("Name", user["full_name"], key=f"cn_{rid}")
    cdate = c2.date_input("Date", date.today(), key=f"cdt_{rid}", format="DD/MM/YYYY")
    sig = signature_widget(f"csig_{rid}", user)

    b1, b2, _ = st.columns([2, 2, 4])
    if b1.button("✅ Sign & return to Underwriter", type="primary", key=f"cok_{rid}"):
        missing = [m for m, ok in [("Comment", comment.strip()), ("Name", name.strip()), ("Signature image", sig)] if not ok]
        if missing:
            st.error("Please complete: " + ", ".join(missing))
        else:
            d2 = {**d, "lines": [{**l, "claim": status[i]} for i, l in enumerate(lines)],
                  "claims": {"user": user["username"], "name": name, "date": cdate.isoformat(), "comment": comment}}
            db.update_refund(rid, data=d2, status=db.CLAIMS_DONE, claims_sig=sig)
            db.add_history(rid, user["username"], "Claims completed and returned to Underwriter", comment)
            go("list")
    reason = st.text_input("Reason for returning to Underwriter for correction", key=f"cr_{rid}")
    if b2.button("↩ Return for correction", key=f"cret_{rid}"):
        if not reason.strip():
            st.error("Enter the reason first")
        else:
            _return_to_uw(rec, user, reason)


def _return_to_uw(rec, user, reason):
    d2 = {**rec["data"], "return_note": f"{reason} ({user['full_name']})"}
    db.update_refund(rec["id"], data=d2, status=db.DRAFT)
    db.add_history(rec["id"], user["username"], "Returned to Underwriter for correction", reason)
    go("list")


# ============================================================ HOD
def hod_page(user):
    staff_inbox(user, "Head of Department", db.WITH_HOD, _hod_review)


def _hod_review(rec, user):
    rid = rec["id"]
    render_record(rec)
    if calc.compute_document(rec["data"])["excluded_count"]:
        st.warning("One or more vehicles are excluded from the refund because Claims reported a registered claim.")
    st.markdown("---\n#### Schedule checked by")
    c1, c2 = st.columns(2)
    name = c1.text_input("Name", user["full_name"], key=f"hn_{rid}")
    hdate = c2.date_input("Date", date.today(), key=f"hdt_{rid}", format="DD/MM/YYYY")
    sig = signature_widget(f"hsig_{rid}", user)
    b1, b2, _ = st.columns([2, 2, 4])
    if b1.button("✅ Sign & return to Underwriter", type="primary", key=f"hok_{rid}"):
        if not name.strip() or not sig:
            st.error("Please enter your name and provide your signature image")
        else:
            d2 = {**rec["data"], "hod": {"user": user["username"], "name": name, "date": hdate.isoformat()}}
            db.update_refund(rid, data=d2, status=db.FINAL, hod_sig=sig)
            db.add_history(rid, user["username"], "Checked by Head of Department; returned to Underwriter")
            go("list")
    reason = st.text_input("Reason for returning to Underwriter for correction", key=f"hr_{rid}")
    if b2.button("↩ Return for correction", key=f"hret_{rid}"):
        if not reason.strip():
            st.error("Enter the reason first")
        else:
            _return_to_uw(rec, user, reason)


# ============================================================== main
user = st.session_state.get("user")
if not user:
    login_page()
else:
    sidebar(user)
    {db.UNDERWRITER: underwriter_page, db.CLAIMS: claims_page, db.HOD: hod_page}[user["role"]](user)
