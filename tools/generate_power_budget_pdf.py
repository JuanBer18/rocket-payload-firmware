#!/usr/bin/env python3
"""Generate the readable PDF version of the payload power budget."""

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph,
                                SimpleDocTemplate, Spacer, Table, TableStyle)


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "output" / "pdf" / "payload_power_budget.pdf"


def footer(canvas, document):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#5b6470"))
    canvas.drawString(18 * mm, 11 * mm, "HYPOWER Payload - continuous mission power budget")
    canvas.drawRightString(192 * mm, 11 * mm, "Page %d" % document.page)
    canvas.restoreState()


def build_pdf(output=OUTPUT):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Title2", parent=styles["Title"],
                              fontName="Helvetica-Bold", fontSize=22,
                              leading=25, textColor=colors.HexColor("#132238"),
                              alignment=TA_CENTER, spaceAfter=8))
    styles.add(ParagraphStyle(name="Lead", parent=styles["BodyText"],
                              fontSize=11, leading=15, textColor=colors.HexColor("#253347"),
                              spaceAfter=8))
    styles.add(ParagraphStyle(name="H2x", parent=styles["Heading2"],
                              fontSize=14, leading=17, textColor=colors.HexColor("#173b64"),
                              spaceBefore=10, spaceAfter=6))
    styles.add(ParagraphStyle(name="Small", parent=styles["BodyText"],
                              fontSize=8.5, leading=11, spaceAfter=5))
    styles.add(ParagraphStyle(name="Callout", parent=styles["BodyText"],
                              fontSize=12, leading=16, borderColor=colors.HexColor("#2367a8"),
                              borderWidth=1, borderPadding=9, backColor=colors.HexColor("#eaf3fb"),
                              spaceAfter=9))

    doc = SimpleDocTemplate(str(output), pagesize=A4, rightMargin=17 * mm,
                            leftMargin=17 * mm, topMargin=16 * mm,
                            bottomMargin=18 * mm, title="HYPOWER Payload Power Budget",
                            author="HYPOWER Payload Project")
    story = [Paragraph("HYPOWER Payload Power Budget", styles["Title2"]),
             Paragraph("Continuous mission firmware | 9 August 2026", styles["Lead"]),
             Paragraph("<b>Planning result:</b> approximately <b>210 mA at 3.3 V (0.69 W)</b>. "
                       "Allow 165-300 mA average until measured, at least 0.75 A transient, "
                       "and use a regulated 3.3 V source rated for 1 A minimum (1.5 A preferred).",
                       styles["Callout"]),
             Paragraph("The two dominant loads", styles["H2x"])]

    cell = ParagraphStyle(name="Cell", parent=styles["BodyText"], fontSize=8.2,
                          leading=10.2, spaceAfter=0)
    head = ParagraphStyle(name="CellHead", parent=cell, fontName="Helvetica-Bold",
                          textColor=colors.white)
    dominant = [
        [Paragraph("Load", head), Paragraph("Continuous current", head),
         Paragraph("Peak", head), Paragraph("What the number means", head)],
        [Paragraph("<b>ESP32-S3-WROOM-1-N8R2</b>", cell),
         Paragraph("100 mA budget<br/>70-130 mA expected", cell),
         Paragraph("340 mA", cell),
         Paragraph("The average is an engineering estimate for the active MicroPython + Wi-Fi AP workload. "
                   "340 mA is Espressif's documented 21 dBm TX peak.", cell)],
        [Paragraph("<b>SEN65</b>", cell),
         Paragraph("80 mA typ.<br/>100 mA max.", cell),
         Paragraph("200 mA max.<br/>(2 ms)", cell),
         Paragraph("Sensirion Table 11, continuous measurement after the first 60 seconds.", cell)],
    ]
    table = Table(dominant, colWidths=[44 * mm, 37 * mm, 28 * mm, 61 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#173b64")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5), ("LEADING", (0, 0), (-1, -1), 11),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9aa7b4")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f8")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story += [table, Paragraph("Detailed budget", styles["H2x"])]

    rows = [
        ["Load", "Nominal", "Upper", "Peak", "Rail"],
        ["ESP32-S3 module", "100 mA", "130 mA", "340 mA", "+3V3"],
        ["SEN65", "80 mA", "100 mA", "200 mA", "3V3_SR"],
        ["microSD (allowance)", "25 mA", "60 mA", "200 mA", "3V3_SR"],
        ["LSM6DSO32", "0.55 mA", "0.55 mA", "0.55 mA", "3V3_SR"],
        ["BME280 (allowance)", "1.0 mA", "1.2 mA", "1.2 mA", "3V3_SR"],
        ["Power LED", "1.3 mA", "2.0 mA", "2.0 mA", "+3V3"],
        ["Board overhead", "2.0 mA", "4.0 mA", "4.0 mA", "mixed"],
        ["TOTAL", "209.9 mA", "297.8 mA", "747.8 mA", "mixed"],
    ]
    budget = Table(rows, colWidths=[59 * mm, 27 * mm, 27 * mm, 27 * mm, 28 * mm], repeatRows=1)
    budget.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#173b64")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#dceaf6")),
        ("FONTSIZE", (0, 0), (-1, -1), 9), ("ALIGN", (1, 1), (3, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9aa7b4")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#f5f7f9")]),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story += [budget,
              Paragraph("Wi-Fi and SD current are bursty. The ESP32 average changes with browser "
                        "traffic, live plots, downloads and RF retransmissions; it cannot be derived exactly from source code.",
                        styles["Small"]),
              PageBreak(),
              Paragraph("Board rail routing", styles["H2x"]),
              Paragraph("The schematic routes the ESP32 to <b>+3V3</b>, selected at J10 from "
                        "<b>3V3_USB</b> or <b>3V3_SR</b>. The SEN65 connector, IMU, BME280 and microSD "
                        "are wired directly to <b>3V3_SR</b>.", styles["Lead"]),
              Paragraph("Selecting USB at J10 therefore powers the ESP32 from the USB LDO but does "
                        "not create the sensor rail. The battery/external converter must still provide "
                        "a clean 3.3 V 3V3_SR rail. If J10 selects 3V3_SR, the battery-side converter "
                        "supplies the complete payload.", styles["Callout"]),
              Paragraph("Firmware represented by the budget", styles["H2x"]),
              Paragraph("The Wi-Fi access point remains active from boot; the mission loop runs continuously; "
                        "the LSM6DSO32 logs at 833 Hz; the BME280 runs in normal mode with x16 pressure "
                        "oversampling; SEN65 measurement continues; and binary records are written and flushed to microSD. "
                        "Optional live plots add Wi-Fi traffic without changing sensor logging rates.", styles["Lead"]),
              Paragraph("Supply and runtime implications", styles["H2x"]),
              Paragraph("At 210 mA, a hypothetical 5 V-to-3.3 V linear regulator carrying the whole payload "
                        "would dissipate about 0.36 W. At the 298 mA upper budget it would dissipate about 0.51 W. "
                        "Actual dissipation follows the selected +3V3 source and the separate 3V3_SR converter.", styles["Lead"]),
              Paragraph("With a 3.7 V battery and 90% conversion efficiency, the nominal 0.69 W load draws "
                        "about 208 mA from the battery. Estimated runtime is usable capacity divided by 208 mA, "
                        "then derated for temperature, voltage sag, converter cutoff and battery rating conditions.", styles["Lead"]),
              Paragraph("How to verify it", styles["H2x"]),
              Paragraph("Measure boot, continuous mission with no browser, dashboard connected, and live "
                        "plots/file download. Measure +3V3 and 3V3_SR separately. Use peak capture or a current "
                        "shunt and oscilloscope; a normal multimeter may miss the SEN65's 2 ms pulse and Wi-Fi/SD bursts.",
                        styles["Lead"]),
              Paragraph("Sources and confidence", styles["H2x"]),
              Paragraph("SEN65: PS_DS_SEN6x.pdf p.13 Table 11. LSM6DSO32: lsm6dso32.pdf p.28 Table 4. "
                        "Rail routing and LEDs: custom-board schematic. ESP32-S3: Espressif Series Datasheet v2.2 "
                        "pp.66-68. BME280: Bosch product data. microSD and the ESP32 average are engineering allowances "
                        "that must be replaced by measurements of this exact board and card.", styles["Small"])]
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return output


if __name__ == "__main__":
    print(build_pdf())
