#!/usr/bin/env python3
"""Build a readable PDF from a generated flight-analysis directory."""

import argparse
from datetime import datetime
from html import escape
import json
from pathlib import Path
import re

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate, Frame, Image, NextPageTemplate, PageBreak, PageTemplate,
    Paragraph, Spacer,
)


FIGURES = [
    ("altitude_and_acceleration.png", "Altitude and acceleration",
     "Barometric height above the pressure reference paired with total acceleration magnitude."),
    ("sen65_altitude_profiles.png", "Altitude-resolved SEN65 profiles",
     "SEN65 air measurements paired to interpolated barometric AGL altitude. Solid lines show ascent-bin medians and dashed lines show descent-bin medians."),
    ("sen65_analysis.png", "SEN65 time-series analysis",
     "Particulate mass, particle-number concentration, gas indices, temperature, humidity and raw gas signals over time."),
    ("barometer_analysis.png", "Barometer analysis",
     "Pressure, BME280 temperature and humidity, and relative barometric altitude."),
    ("imu_motion.png", "IMU motion",
     "Body-axis acceleration, acceleration magnitude and angular rates."),
    ("dead_reckoning_experimental.png", "Experimental dead reckoning",
     "Diagnostic inertial integration only. Horizontal position and yaw are not ground truth without external position and heading references."),
]


def _inline_markup(text):
    safe = escape(text)
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", safe)


def _styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="ReportTitle", parent=styles["Title"], fontName="Helvetica-Bold",
        fontSize=24, leading=29, textColor=colors.HexColor("#17365D"),
        alignment=TA_CENTER, spaceAfter=7 * mm,
    ))
    styles.add(ParagraphStyle(
        name="ReportSubtitle", parent=styles["Normal"], fontSize=11,
        leading=15, textColor=colors.HexColor("#4B5563"),
        alignment=TA_CENTER, spaceAfter=8 * mm,
    ))
    styles.add(ParagraphStyle(
        name="Section", parent=styles["Heading2"], fontName="Helvetica-Bold",
        fontSize=15, leading=19, textColor=colors.HexColor("#17365D"),
        spaceBefore=5 * mm, spaceAfter=2.5 * mm,
    ))
    styles.add(ParagraphStyle(
        name="BulletReport", parent=styles["BodyText"], fontSize=9.5,
        leading=13.5, leftIndent=5 * mm, firstLineIndent=-3.5 * mm,
        spaceAfter=1.8 * mm,
    ))
    styles.add(ParagraphStyle(
        name="BodyReport", parent=styles["BodyText"], fontSize=9.5,
        leading=13.5, spaceAfter=2.5 * mm,
    ))
    styles.add(ParagraphStyle(
        name="FigureTitle", parent=styles["Heading2"], fontName="Helvetica-Bold",
        fontSize=15, leading=19, textColor=colors.HexColor("#17365D"),
        alignment=TA_CENTER, spaceAfter=3 * mm,
    ))
    styles.add(ParagraphStyle(
        name="Caption", parent=styles["Normal"], fontSize=8.5, leading=12,
        textColor=colors.HexColor("#4B5563"), alignment=TA_CENTER,
        spaceBefore=2.5 * mm,
    ))
    return styles


def _page(canvas, doc, flight_name):
    canvas.saveState()
    width, height = canvas._pagesize
    canvas.setStrokeColor(colors.HexColor("#D1D5DB"))
    canvas.line(18 * mm, height - 14 * mm, width - 18 * mm, height - 14 * mm)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#4B5563"))
    canvas.drawString(18 * mm, height - 10.5 * mm,
                      "Atmospheric Rocket Payload - %s" % flight_name)
    canvas.drawRightString(width - 18 * mm, 10 * mm, "Page %d" % doc.page)
    canvas.restoreState()


def _scaled_image(path, max_width, max_height):
    with PILImage.open(path) as source:
        width, height = source.size
    scale = min(max_width / width, max_height / height)
    return Image(str(path), width=width * scale, height=height * scale)


def build_pdf(analysis_dir, output_path=None):
    analysis_dir = Path(analysis_dir).resolve()
    report_path = analysis_dir / "analysis_report.md"
    summary_path = analysis_dir / "summary.json"
    if not report_path.is_file() or not summary_path.is_file():
        raise FileNotFoundError("analysis_report.md and summary.json are required in %s" % analysis_dir)
    metrics = json.loads(summary_path.read_text(encoding="utf-8"))
    source = metrics.get("input", {}).get("binary") or metrics.get("input", {}).get("csv")
    flight_name = Path(source).stem if source else analysis_dir.name.replace("_analysis", "")
    output = (Path(output_path).resolve() if output_path else
              analysis_dir / "analysis_report.pdf")
    output.parent.mkdir(parents=True, exist_ok=True)

    doc = BaseDocTemplate(
        str(output), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=19 * mm, bottomMargin=16 * mm,
        title="%s flight analysis" % flight_name,
        author="Atmospheric Rocket Payload analysis tools",
        subject="Payload sensor and flight-log analysis",
    )
    portrait_frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height,
                           id="report-frame")
    landscape_size = landscape(A4)
    landscape_width = landscape_size[0] - doc.leftMargin - doc.rightMargin
    landscape_height = landscape_size[1] - doc.topMargin - doc.bottomMargin
    landscape_frame = Frame(doc.leftMargin, doc.bottomMargin,
                            landscape_width, landscape_height,
                            id="figure-frame")
    page_callback = lambda canvas, current_doc: _page(
        canvas, current_doc, flight_name)
    doc.addPageTemplates([
        PageTemplate(id="analysis", frames=[portrait_frame], pagesize=A4,
                     onPage=page_callback),
        PageTemplate(id="figures", frames=[landscape_frame],
                     pagesize=landscape_size, onPage=page_callback),
    ])
    styles = _styles()
    story = [
        Spacer(1, 15 * mm),
        Paragraph("Flight Analysis Report", styles["ReportTitle"]),
        Paragraph(_inline_markup(flight_name), styles["ReportSubtitle"]),
        Paragraph("Generated %s" % datetime.now().strftime("%Y-%m-%d %H:%M"),
                  styles["ReportSubtitle"]),
    ]

    for raw in report_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line == "# Flight analysis":
            continue
        if line.startswith("## "):
            story.append(Paragraph(_inline_markup(line[3:]), styles["Section"]))
        elif line.startswith("- "):
            story.append(Paragraph("&#8226;&nbsp; " + _inline_markup(line[2:]),
                                   styles["BulletReport"]))
        else:
            story.append(Paragraph(_inline_markup(line), styles["BodyReport"]))

    first_figure = True
    for filename, title, caption in FIGURES:
        figure = analysis_dir / filename
        if not figure.is_file():
            continue
        if first_figure:
            story.append(NextPageTemplate("figures"))
            first_figure = False
        story.extend([
            PageBreak(),
            Paragraph(title, styles["FigureTitle"]),
            Spacer(1, 2 * mm),
            _scaled_image(figure, landscape_width, landscape_height - 25 * mm),
            Paragraph(caption, styles["Caption"]),
        ])

    doc.build(story)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis_dir", help="folder containing analysis_report.md and plots")
    parser.add_argument("-o", "--output", help="PDF output path")
    args = parser.parse_args(argv)
    try:
        output = build_pdf(args.analysis_dir, args.output)
    except Exception as exc:
        print("ERROR: PDF generation failed: %s" % exc)
        return 1
    print("PDF written to %s" % output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
