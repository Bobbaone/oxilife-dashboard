import sqlite3
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ACCENT = colors.HexColor("#20bde9")
DARK = colors.HexColor("#0d2632")
MUTED = colors.HexColor("#607d89")


def _number(value, decimals=2):
    return "-" if value is None else f"{value:.{decimals}f}".replace(".", ",")


def generate_weekly_report(db_path: Path, output_path: Path, start_ts: int, end_ts: int) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    points = conn.execute("""SELECT d.*,COUNT(r.id) samples,MIN(r.value_num) minimum,
                             MAX(r.value_num) maximum,AVG(r.value_num) average,
                             (SELECT value_num FROM readings x WHERE x.datapoint_id=d.id AND x.ts>=? AND x.ts<?
                              AND x.value_num IS NOT NULL ORDER BY x.ts LIMIT 1) first_value,
                             (SELECT value_num FROM readings x WHERE x.datapoint_id=d.id AND x.ts>=? AND x.ts<?
                              AND x.value_num IS NOT NULL ORDER BY x.ts DESC LIMIT 1) last_value
                             FROM datapoints d LEFT JOIN readings r ON r.datapoint_id=d.id AND r.ts>=? AND r.ts<?
                             WHERE d.logging=1 GROUP BY d.id HAVING samples>0 ORDER BY d.sort_order,d.name""",
                          (start_ts, end_ts, start_ts, end_ts, start_ts, end_ts)).fetchall()
    poll = conn.execute("SELECT COUNT(*) total,SUM(online) online FROM poll_events WHERE ts>=? AND ts<?",
                        (start_ts, end_ts)).fetchone()
    conn.close()
    start, end = datetime.fromtimestamp(start_ts).astimezone(), datetime.fromtimestamp(end_ts - 1).astimezone()
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="ReportTitle", parent=styles["Title"], alignment=TA_CENTER,
                              textColor=DARK, fontSize=23, leading=28, spaceAfter=6))
    styles.add(ParagraphStyle(name="Sub", parent=styles["Normal"], alignment=TA_CENTER,
                              textColor=MUTED, fontSize=10, spaceAfter=16))
    styles.add(ParagraphStyle(name="Section", parent=styles["Heading2"], textColor=DARK,
                              fontSize=15, spaceBefore=10, spaceAfter=8))
    doc = SimpleDocTemplate(str(output_path), pagesize=A4, rightMargin=16*mm, leftMargin=16*mm,
                            topMargin=15*mm, bottomMargin=15*mm, title="Oxilife Wochenbericht")
    total, online = int(poll["total"] or 0), int(poll["online"] or 0)
    availability = online / total * 100 if total else 0
    story = [Paragraph("OXILIFE WOCHENBERICHT", styles["ReportTitle"]),
             Paragraph(f"{start:%d.%m.%Y} bis {end:%d.%m.%Y}", styles["Sub"]),
             Paragraph("Zusammenfassung", styles["Section"]),
             Table([["Erfasste Datenpunkte", str(len(points))], ["Abfragen", str(total)],
                    ["Erreichbarkeit", f"{availability:.1f} %".replace(".", ",")]], colWidths=[65*mm, 45*mm],
                   style=TableStyle([("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eaf7fb")),
                                     ("GRID", (0, 0), (-1, -1), .4, colors.HexColor("#c8dce4")),
                                     ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                                     ("PADDING", (0, 0), (-1, -1), 7)])),
             Spacer(1, 5*mm), Paragraph("Wasserwerte und Anlagenwerte", styles["Section"])]
    rows = [["Datenpunkt", "Min", "Durchschnitt", "Max", "Einheit", "Werte", "Bewertung"]]
    for point in points:
        scale, decimals = float(point["scale"] or 1), int(point["decimals"] or 0)
        minimum = point["minimum"] * scale if point["minimum"] is not None else None
        maximum = point["maximum"] * scale if point["maximum"] is not None else None
        bad = ((point["min_value"] is not None and minimum is not None and minimum < point["min_value"]) or
               (point["max_value"] is not None and maximum is not None and maximum > point["max_value"]))
        warning = ((point["warning_low"] is not None and minimum is not None and minimum < point["warning_low"]) or
                   (point["warning_high"] is not None and maximum is not None and maximum > point["warning_high"]))
        configured = any(point[key] is not None for key in ("min_value", "max_value", "warning_low", "warning_high"))
        rating = "Schlecht" if bad else "Kritisch" if warning else "Unauffällig" if configured else "Keine Grenzen"
        rows.append([point["name"], _number(minimum, decimals),
                     _number(point["average"] * scale if point["average"] is not None else None, decimals),
                     _number(maximum, decimals), point["unit"] or "-", str(point["samples"]), rating])
    if len(rows) == 1:
        rows.append(["Keine aufgezeichneten Werte", "-", "-", "-", "-", "0", "-"])
    table = Table(rows, repeatRows=1, colWidths=[44*mm, 18*mm, 25*mm, 18*mm, 16*mm, 15*mm, 32*mm])
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), DARK), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                               ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 8),
                               ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f8fa")]),
                               ("GRID", (0, 0), (-1, -1), .3, colors.HexColor("#c8dce4")),
                               ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("PADDING", (0, 0), (-1, -1), 5)]))
    story.append(table)
    consumption = []
    for point in points:
        marker = f'{point["name"]} {point["path"]}'.lower()
        if any(word in marker for word in ("verbrauch", "consumption", "dosage", "dosing", "dosierung", "total")):
            if point["first_value"] is not None and point["last_value"] is not None:
                delta = (point["last_value"] - point["first_value"]) * float(point["scale"] or 1)
                consumption.append([point["name"], _number(abs(delta), int(point["decimals"] or 0)), point["unit"] or "-"])
    story += [Spacer(1, 5*mm), Paragraph("Verbrauch und Dosierung", styles["Section"])]
    if consumption:
        usage = Table([["Zähler", "Wochenverbrauch", "Einheit"], *consumption], colWidths=[85*mm, 45*mm, 30*mm])
        usage.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), ACCENT), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                                   ("GRID", (0, 0), (-1, -1), .4, colors.HexColor("#b8d5df")),
                                   ("PADDING", (0, 0), (-1, -1), 6)]))
        story.append(usage)
    else:
        story.append(Paragraph("Kein Verbrauchszähler wurde von Oxilife/Tasmota geliefert. Messwerte wie Salzgehalt oder Chlorwert allein erlauben keine zuverlässige Berechnung der verbrauchten Menge.", styles["BodyText"]))
    story += [Spacer(1, 7*mm), Paragraph("Automatisch erstellt vom lokalen Oxilife Dashboard.", styles["Sub"])]

    def footer(canvas, document):
        canvas.saveState(); canvas.setFillColor(MUTED); canvas.setFont("Helvetica", 8)
        canvas.drawString(16*mm, 8*mm, f"Erstellt: {datetime.now().astimezone():%d.%m.%Y %H:%M}")
        canvas.drawRightString(A4[0]-16*mm, 8*mm, f"Seite {document.page}"); canvas.restoreState()
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
