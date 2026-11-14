"""Builds the 'Underwriting File Note for Authorising Refund Endorsement' PDF."""
from __future__ import annotations

import io
from xml.sax.saxutils import escape

from PIL import Image, ImageOps
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (Flowable, Image as RLImage, KeepTogether, Paragraph,
                                SimpleDocTemplate, Spacer, Table, TableStyle)

import calc
from db import STATUS_LABEL, FINAL

COMPANY = "TAUSI ASSURANCE COMPANY LIMITED"
TITLE = "UNDERWRITING FILE NOTE FOR AUTHORISING REFUND ENDORSEMENT"
CLAIMS_NOTE = ("(Note: NO REFUND IS TO BE PASSED IF A CLAIM HAS BEEN REGISTERED "
               "WITHIN THE CURRENT PERIOD OF INSURANCE)")
APPROVERS = ["Manager", "Asst. General Manager", "Chief Executive Officer", "Managing Director"]


# ------------------------------------------------------------ signatures
def process_signature(raw: bytes) -> bytes:
    """Flatten onto white, auto-crop whitespace, downsize. Returns PNG bytes."""
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("Image is too large (max 8 MB)")
    img = Image.open(io.BytesIO(raw))
    img.load()
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        bg.alpha_composite(img)
        img = bg
    img = img.convert("RGB")
    ink = ImageOps.invert(img.convert("L")).point(lambda p: 255 if p > 40 else 0)
    box = ink.getbbox()
    if box is None:
        raise ValueError("The image looks blank - please upload a clear signature image")
    pad = 8
    box = (max(0, box[0] - pad), max(0, box[1] - pad),
           min(img.width, box[2] + pad), min(img.height, box[3] + pad))
    img = img.crop(box)
    img.thumbnail((700, 260))
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def _fit_image(png: bytes, max_w: float, max_h: float) -> RLImage:
    w, h = Image.open(io.BytesIO(png)).size
    scale = min(max_w / w, max_h / h)
    return RLImage(io.BytesIO(png), width=w * scale, height=h * scale)


class _TickBox(Flowable):
    """Small empty square for manual ticking."""
    def wrap(self, aw, ah):
        return 5 * mm, 5 * mm

    def draw(self):
        self.canv.setLineWidth(0.8)
        self.canv.rect(0.5 * mm, 0, 4 * mm, 4 * mm)


# ------------------------------------------------------------------ PDF
def build_pdf(rec: dict) -> bytes:
    d = rec["data"]
    res = calc.compute_document(d)
    ss = getSampleStyleSheet()
    n = ParagraphStyle("n", parent=ss["Normal"], fontName="Helvetica", fontSize=9, leading=11.5)
    sm = ParagraphStyle("sm", parent=n, fontSize=7.3, leading=9)
    smr = ParagraphStyle("smr", parent=sm, alignment=2)
    smc = ParagraphStyle("smc", parent=sm, alignment=TA_CENTER)
    hd = ParagraphStyle("hd", parent=n, fontName="Helvetica-Bold")
    head = ParagraphStyle("head", parent=hd, fontSize=13, alignment=TA_CENTER, leading=16)
    title = ParagraphStyle("title", parent=hd, fontSize=10.5, alignment=TA_CENTER, leading=13)
    note = ParagraphStyle("note", parent=hd, fontSize=8)

    def P(text, style=n):
        return Paragraph(escape(str(text or "")).replace("\n", "<br/>"), style)

    def sec(text):
        return Paragraph(f"<u>{escape(text)}</u>", hd)

    story = [Paragraph(COMPANY, head), Spacer(1, 2 * mm), Paragraph(f"<u>{TITLE}</u>", title),
             Spacer(1, 4 * mm)]

    # ---- header fields
    period = f"{calc.dmy(d.get('period_start'))}  to  {calc.dmy(d.get('period_end'))}"
    def lab(t): return Paragraph(f"<b>{t}</b>", n)
    hdr = Table([
        [lab("DEPARTMENT:"), P(d.get("department")), lab("INSURED:"), P(d.get("insured"))],
        [lab("POLICY NO:"), P(d.get("policy_no")), lab("POLICY PERIOD:"), P(period)],
        [lab("AGENT/BROKER:"), P(d.get("agent")), "", ""],
    ], colWidths=[30 * mm, 60 * mm, 32 * mm, 56 * mm])
    hdr.setStyle(TableStyle([
        ("LINEBELOW", (1, 0), (1, -1), 0.4, colors.grey), ("LINEBELOW", (3, 0), (3, 1), 0.4, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "BOTTOM"), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 2)]))
    story += [hdr, Spacer(1, 4 * mm)]

    # ---- brief details
    story += [sec("BRIEF DETAILS:"), Spacer(1, 1 * mm), P(d.get("brief")), Spacer(1, 4 * mm)]

    # ---- claims status
    cl = d.get("claims") or {}
    story += [sec("COMMENT ON CLAIMS STATUS:"), Paragraph(escape(CLAIMS_NOTE), note), Spacer(1, 1 * mm)]
    if cl:
        per_vehicle = [f"{r['reg']}: {r['claim']}" for r in res["rows"]]
        story.append(P("; ".join(per_vehicle), sm))
        story.append(P(cl.get("comment")))
    else:
        story.append(P("(Pending - to be completed by Claims Department)", sm))
    story += [Spacer(1, 1.5 * mm), _sig_row(cl.get("name"), rec.get("claims_sig"), cl.get("date"), n),
              Spacer(1, 4 * mm)]

    # ---- calculation table
    story.append(sec("CALCULATION OF REFUND ENDORSEMENT"))
    story.append(Spacer(1, 1.5 * mm))
    cur = calc.CURRENCY
    rows = [[P("#", sm), P("Vehicle", sm), P("Reason / basis", sm), P(f"Sum insured ({cur})", smr),
             P("Rate %", smr), P(f"Annual premium ({cur})", smr), P("Days", smc), P(f"Refund ({cur})", smr)]]
    for r in res["rows"]:
        rows.append(_calc_row(r, sm, smr, smc))
    k = len(rows)
    tot_rows = []
    if res["has_extras"]:
        tot_rows += [("Basic premium refund", calc.fmt_amt(res["basic_total"]), False),
                     ("Excess protector refund", calc.fmt_amt(res["ep_total"]), False),
                     ("P.V.T refund", calc.fmt_amt(res["pvt_total"]), False)]
    tot_rows.append(("Sub-total", calc.fmt_amt(res["subtotal"]), False))
    if res["levy_pct"] > 0:
        tot_rows.append((f"Levies/duties @ {res['levy_pct'].normalize():f}%", calc.fmt_amt(res["levies"]), False))
    tot_rows.append(("TOTAL REFUND", f"{cur} {calc.fmt_amt(res['total'])}", True))
    for label, val, bold in tot_rows:
        b0, b1 = ("<b>", "</b>") if bold else ("", "")
        rows.append(["", "", "", "", "", Paragraph(f"{b0}{label}{b1}", smr), "", Paragraph(f"{b0}{val}{b1}", smr)])
    t = Table(rows, repeatRows=1, colWidths=[6 * mm, 27 * mm, 36 * mm, 28 * mm, 12 * mm, 28 * mm, 16 * mm, 29 * mm])
    spans = [("SPAN", (5, i), (6, i)) for i in range(k, len(rows))]
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, k - 1), 0.4, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8E8E8")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEABOVE", (5, k), (-1, k), 0.6, colors.black),
        ("LINEABOVE", (5, -1), (-1, -1), 0.8, colors.black), ("LINEBELOW", (5, -1), (-1, -1), 1.2, colors.black),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2), *spans]))
    story += [t]
    story.append(Spacer(1, 1.5 * mm))
    story.append(P("Pro-rata = premium x unexpired days / days in policy period (both dates inclusive). "
                   "Annual premium = sum insured x rate. Excess protector and P.V.T are each charged at 0.25% of the sum insured "
                   "and refunded on the same basis. Refund and levy amounts are rounded to whole numbers.", sm))
    story.append(Spacer(1, 5 * mm))

    # ---- approval (left blank - signed after the note is downloaded)
    box_t = Table([[_TickBox(), Paragraph(f"{i}. {a}", n)] for i, a in enumerate(APPROVERS, 1)],
                  colWidths=[8 * mm, 80 * mm], hAlign="LEFT")
    box_t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5)]))
    appr = [sec("REFUND APPROVED BY (Tick as appropriate):"), Spacer(1, 1.5 * mm), box_t,
            Spacer(1, 2 * mm), _sig_row(None, None, None, n)]
    story.append(KeepTogether(appr))
    story.append(Spacer(1, 3 * mm))

    # ---- prepared by / checked by
    uw, hod = d.get("uw") or {}, d.get("hod") or {}
    story.append(KeepTogether([sec("SCHEDULE PREPARED BY:"), Spacer(1, 2 * mm),
                               _sig_row(uw.get("name"), rec.get("uw_sig"), uw.get("date"), n)]))
    story.append(Spacer(1, 3 * mm))
    story.append(KeepTogether([sec("SCHEDULE CHECKED BY:"), Spacer(1, 2 * mm),
                               _sig_row(hod.get("name"), rec.get("hod_sig"), hod.get("date"), n)]))

    ref, status = rec.get("ref_no") or "", STATUS_LABEL.get(rec.get("status"), "")

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.grey)
        canvas.drawString(14 * mm, 8 * mm, f"Ref: {ref}   |   Status: {status}")
        canvas.drawRightString(A4[0] - 14 * mm, 8 * mm, f"Page {doc.page}")
        canvas.restoreState()

    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=12 * mm,
                      bottomMargin=16 * mm, title=f"Refund note {ref}", author=COMPANY
                      ).build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


def _sig_row(name, sig_png, date_iso, style):
    """Name: ...... Signature: [image] Date: ......"""
    sig = _fit_image(sig_png, 44 * mm, 14 * mm) if sig_png else ""
    nm = Paragraph(f"Name: <b>{escape(name)}</b>" if name else "Name:", style)
    dt = Paragraph(f"Date: <b>{calc.dmy(date_iso)}</b>" if date_iso else "Date:", style)
    t = Table([[nm, Paragraph("Signature:", style), sig, dt]],
              colWidths=[68 * mm, 19 * mm, 50 * mm, 40 * mm], rowHeights=[15 * mm])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "BOTTOM"), ("LINEBELOW", (0, 0), (0, 0), 0.4, colors.grey),
        ("LINEBELOW", (2, 0), (2, 0), 0.4, colors.grey), ("LINEBELOW", (3, 0), (3, 0), 0.4, colors.grey),
        ("ALIGN", (2, 0), (2, 0), "CENTER"), ("LEFTPADDING", (0, 0), (-1, -1), 2)]))
    return t


def _calc_row(r, sm, smr, smc):
    f = calc.fmt
    def p(x, s=sm): return Paragraph(x, s)
    eff = calc.dmy(r["eff"])
    basis = r["basis"] or ""
    reason = f"<b>{calc.SHORT_REASON.get(r['type'], '')}</b><br/>{basis}" + (f", eff. {eff}" if eff and basis == calc.PRO_RATA else "")
    veh = escape(r["reg"]) + (f"<br/>{escape(r['desc'])}" if r["desc"] else "")
    if r["type"] == calc.REVISION and r["new_si"] is not None:
        si = f"Old: {f(r['si'])}<br/>New: {f(r['new_si'])}"
        rate = f"{r['rate'].normalize():f}<br/>{r['new_rate'].normalize():f}"
        prem = f"Old: {f(r['old_prem'])}<br/>New: {f(r['new_prem'])}"
    else:
        si = f(r["si"]); rate = f"{r['rate'].normalize():f}" if r["rate"] is not None else ""
        prem = f(r["old_prem"])
    extras = [(n, k) for n, k in (("E.P.", "ep"), ("P.V.T", "pvt")) if r[k]]
    for n, k in extras:
        prem += (f"<br/>{n}: {f(r[k + '_prem'])}" if r[k + "_new_prem"] is None else
                 f"<br/>{n}: {f(r[k + '_prem'])} -> {f(r[k + '_new_prem'])}")
    days = f"{r['unexp']}/{r['total_days']}" if r["unexp"] is not None else ("Full" if basis == calc.ANNUAL else "")
    if r["excluded"]:
        refund = f"0<br/><font size=6 color='#B00020'>CLAIM REGISTERED - excluded<br/>(would be {calc.fmt_amt(r['calc_refund'])})</font>"
    elif extras:
        refund = (f"Basic: {calc.fmt_amt(r['basic_refund'])}"
                  + "".join(f"<br/>{n}: {calc.fmt_amt(r[k + '_refund'])}" for n, k in extras)
                  + f"<br/><b>{calc.fmt_amt(r['refund'])}</b>")
    else:
        refund = calc.fmt_amt(r["refund"])
    return [p(str(r["no"])), p(veh), p(reason), p(si, smr), p(rate, smr), p(prem, smr), p(days, smc), p(refund, smr)]
