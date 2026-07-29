"""Renders a ReportData payload into a PDF (bytes) with ReportLab.

Pure rendering only — no I/O. The caller (report_service.py) does all MinIO /
disk access and passes pre-fetched snapshot bytes in, so this module stays
fully unit-testable without a database or object storage.
"""

import logging
import re
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image as PILImage
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.legends import Legend
from reportlab.graphics.charts.linecharts import HorizontalLineChart
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.widgets.markers import makeMarker
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import registerFontFamily
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    Image as RLImage,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.core.config import settings
from app.schemas.analytics import ComparePoint
from app.services.reporting.report_data import (
    ReportData,
    ReportIncidentRow,
    format_delta_label,
)

logger = logging.getLogger(__name__)

_FONTS_DIR = Path(__file__).parent / "fonts"
_PAGE_SIZE = A4
_MARGIN = 18 * mm
_CONTENT_WIDTH = _PAGE_SIZE[0] - 2 * _MARGIN

SEVERITY_COLORS = {
    "Critical": "#ef4444",
    "High": "#f97316",
    "Medium": "#f59e0b",
    "Low": "#10b981",
}
_SEVERITY_ORDER = ("Critical", "High", "Medium", "Low")
ZONE_PALETTE = [
    "#0ea5e9", "#8b5cf6", "#f59e0b", "#f43f5e",
    "#14b8a6", "#eab308", "#6366f1", "#ec4899",
]
UNASSIGNED_COLOR = "#94a3b8"
INK = "#0f172a"
ACCENT = "#bef264"       # bright lime — header text / eyebrow, tuned for contrast on INK
BRAND = "#0ea5e9"        # sky blue — section accent bars, primary chart series
MUTED = "#64748b"
RULE = "#e2e8f0"
CARD_BG = "#f8fafc"
CAVEAT_BG = "#fef3c7"
CAVEAT_TEXT = "#92400e"
_POSITIVE = "#16a34a"    # fewer incidents than prior period
_NEGATIVE = "#dc2626"    # more incidents than prior period

_BOLD_MARKER = re.compile(r"\*\*(.+?)\*\*")


def _register_fonts() -> tuple[str, str]:
    """Returns (regular_name, bold_name). Falls back to Helvetica if the
    vendored TTFs are missing, so the report still renders — Latin only."""
    try:
        pdfmetrics.registerFont(TTFont("DejaVuSans", str(_FONTS_DIR / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(
            TTFont("DejaVuSans-Bold", str(_FONTS_DIR / "DejaVuSans-Bold.ttf"))
        )
        registerFontFamily("DejaVuSans", normal="DejaVuSans", bold="DejaVuSans-Bold")
        return "DejaVuSans", "DejaVuSans-Bold"
    except Exception:
        logger.warning(
            "Vendored report fonts unavailable; falling back to Helvetica "
            "(non-Latin characters will not render)."
        )
        return "Helvetica", "Helvetica-Bold"


FONT_REGULAR, FONT_BOLD = _register_fonts()


def _styles() -> dict:
    base = getSampleStyleSheet()
    styles = {
        "Body": ParagraphStyle(
            "ReportBody", parent=base["Normal"], fontName=FONT_REGULAR,
            fontSize=9.5, leading=13, textColor=colors.HexColor(INK),
        ),
        "Muted": ParagraphStyle(
            "ReportMuted", parent=base["Normal"], fontName=FONT_REGULAR,
            fontSize=8.5, leading=11, textColor=colors.HexColor(MUTED),
        ),
        "SectionHeading": ParagraphStyle(
            "ReportSectionHeading", parent=base["Normal"], fontName=FONT_BOLD,
            fontSize=13, leading=16, textColor=colors.HexColor(INK),
        ),
        "Bullet": ParagraphStyle(
            "ReportBullet", parent=base["Normal"], fontName=FONT_REGULAR,
            fontSize=9.5, leading=13.5, textColor=colors.HexColor(INK),
            leftIndent=14, spaceAfter=4,
        ),
        "Caveat": ParagraphStyle(
            "ReportCaveat", parent=base["Normal"], fontName=FONT_REGULAR,
            fontSize=9, leading=12.5, textColor=colors.HexColor(CAVEAT_TEXT),
        ),
        "Caption": ParagraphStyle(
            "ReportCaption", parent=base["Normal"], fontName=FONT_REGULAR,
            fontSize=7.5, leading=10, textColor=colors.HexColor(MUTED),
        ),
        "Centered": ParagraphStyle(
            "ReportCentered", parent=base["Normal"], fontName=FONT_REGULAR,
            fontSize=12, leading=16, textColor=colors.HexColor(MUTED),
            alignment=TA_CENTER,
        ),
        "HeaderTitle": ParagraphStyle(
            "ReportHeaderTitle", parent=base["Normal"], fontName=FONT_BOLD,
            fontSize=16, leading=20, textColor=colors.HexColor(ACCENT),
        ),
        "HeaderSubtitle": ParagraphStyle(
            "ReportHeaderSubtitle", parent=base["Normal"], fontName=FONT_REGULAR,
            fontSize=11, leading=15, textColor=colors.HexColor(ACCENT),
        ),
    }
    return styles


def render_incident_report(
    data: ReportData, snapshots: dict[str, bytes] | None = None
) -> bytes:
    styles = _styles()
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=_PAGE_SIZE,
        leftMargin=_MARGIN,
        rightMargin=_MARGIN,
        topMargin=_MARGIN,
        bottomMargin=_MARGIN,
        title=f"Incident Analytics Report — {data.factory_name}",
        author=data.company_name,
    )

    story: list = []
    story.append(_header_band(data, styles))
    story.append(Spacer(1, 12))
    story.append(_metadata_table(data, styles))
    if data.data_caveats:
        story.append(Spacer(1, 8))
        story.append(_caveats_box(data.data_caveats, styles))
    story.append(Spacer(1, 12))
    story.append(_kpi_band(data, styles))
    story.extend(_heading_block("Key Insights", styles))
    story.extend(_insights_bullets(data.insights, styles))

    if data.summary.grand_total == 0:
        story.append(Spacer(1, 24))
        story.append(Paragraph("No incidents recorded in this period.", styles["Centered"]))
        doc.build(story, onFirstPage=_footer(data), onLaterPages=_footer(data))
        return buffer.getvalue()

    story.extend(_heading_block("Severity Distribution", styles))
    story.append(_severity_drawing(data.summary))

    story.extend(_heading_block("Incidents by Zone", styles))
    story.extend(_zone_section(data, styles))

    story.extend(_heading_block("Trend Over Time", styles))
    story.extend(_trend_section(data.trend, styles))

    story.extend(_heading_block("Current vs Prior Period", styles))
    story.extend(_compare_section(data.compare, styles))

    story.extend(_heading_block("Incident Types", styles))
    story.append(_type_table(data.summary, styles))

    story.extend(_heading_block("Recent Priority Incidents", styles))
    story.append(_incident_table(data.top_incidents, styles))

    if snapshots:
        appendix = _evidence_appendix(data.top_incidents, snapshots, styles)
        if appendix is not None:
            story.extend(_heading_block("Evidence Appendix", styles))
            story.append(appendix)

    doc.build(story, onFirstPage=_footer(data), onLaterPages=_footer(data))
    return buffer.getvalue()


def _heading_block(text: str, styles: dict) -> list:
    """Section heading with a colored accent tab — one Table so the bar's
    height always matches the heading's actual wrapped height."""
    heading = Paragraph(escape(text), styles["SectionHeading"])
    row = Table([["", heading]], colWidths=[4, _CONTENT_WIDTH - 4])
    row.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, 0), colors.HexColor(BRAND)),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (0, 0), 0),
                ("RIGHTPADDING", (0, 0), (0, 0), 0),
                ("LEFTPADDING", (1, 0), (1, 0), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return [Spacer(1, 16), row, Spacer(1, 8)]


def _bold_markup(text: str) -> str:
    escaped = escape(text)
    return _BOLD_MARKER.sub(r"<b>\1</b>", escaped)


def _header_band(data: ReportData, styles: dict) -> Table:
    eyebrow = Paragraph(
        f'<font face="{FONT_BOLD}" size="8" color="{ACCENT}">INCIDENT ANALYTICS REPORT</font>',
        styles["Body"],
    )
    title = Paragraph(escape(data.company_name), styles["HeaderTitle"])
    subtitle = Paragraph(
        escape(f"{data.range_label} · {data.zone_scope_label}"), styles["HeaderSubtitle"]
    )
    text_cell = [eyebrow, Spacer(1, 4), title, Spacer(1, 2), subtitle]

    logo_path = settings.REPORT_LOGO_PATH
    if logo_path and Path(logo_path).is_file():
        try:
            logo = RLImage(logo_path, width=24 * mm, height=12 * mm)
            row = [[logo, text_cell]]
            col_widths = [28 * mm, _CONTENT_WIDTH - 28 * mm]
        except Exception:
            logger.warning("Could not load report logo at %s.", logo_path, exc_info=True)
            row = [[text_cell]]
            col_widths = [_CONTENT_WIDTH]
    else:
        row = [[text_cell]]
        col_widths = [_CONTENT_WIDTH]

    band = Table(row, colWidths=col_widths)
    band.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(INK)),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 16),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 16),
                ("LEFTPADDING", (0, 0), (-1, -1), 16),
                ("RIGHTPADDING", (0, 0), (-1, -1), 16),
            ]
        )
    )

    stripe = Table([[""]], colWidths=[_CONTENT_WIDTH], rowHeights=[4])
    stripe.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(BRAND)),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )

    combined = Table([[band], [stripe]], colWidths=[_CONTENT_WIDTH])
    combined.setStyle(
        TableStyle(
            [
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return combined


def _metadata_table(data: ReportData, styles: dict) -> Table:
    rows = [
        ("Factory", data.factory_name, "Location", data.factory_location or "—"),
        ("Period", data.range_label, "Zone scope", data.zone_scope_label),
        ("Generated at", data.generated_at_local, "Timezone", data.timezone_label),
    ]
    table_rows = []
    for label_a, value_a, label_b, value_b in rows:
        table_rows.append(
            [
                Paragraph(f"<b>{escape(label_a)}</b>", styles["Muted"]),
                Paragraph(escape(value_a), styles["Body"]),
                Paragraph(f"<b>{escape(label_b)}</b>", styles["Muted"]),
                Paragraph(escape(value_b), styles["Body"]),
            ]
        )
    table = Table(
        table_rows,
        colWidths=[28 * mm, _CONTENT_WIDTH / 2 - 28 * mm, 28 * mm, _CONTENT_WIDTH / 2 - 28 * mm],
    )
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(CARD_BG)),
                ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor(RULE)),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (0, -1), 10),
                ("LEFTPADDING", (2, 0), (2, -1), 10),
                ("RIGHTPADDING", (-1, 0), (-1, -1), 10),
            ]
        )
    )
    return table


def _caveats_box(caveats: list[str], styles: dict) -> Table:
    paragraphs = [Paragraph(escape(c), styles["Caveat"]) for c in caveats]
    table = Table([[paragraphs]], colWidths=[_CONTENT_WIDTH])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(CAVEAT_BG)),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#f59e0b")),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    return table


class _KPICard(Flowable):
    """A small stat card: colored left accent bar, muted uppercase label,
    large value, optional colored sublabel (e.g. the vs-prior-period delta).
    Drawn directly on the canvas (rounded rect + text) rather than composed
    from Paragraphs/Tables — gives it a real "dashboard card" look instead of
    plain stacked text."""

    def __init__(
        self,
        width: float,
        height: float,
        label: str,
        value: str,
        accent_color: str,
        value_color: str | None = None,
        sublabel: str | None = None,
        sublabel_color: str = MUTED,
    ) -> None:
        super().__init__()
        self.width = width
        self.height = height
        self.label = label
        self.value = value
        self.accent_color = accent_color
        self.value_color = value_color or INK
        self.sublabel = sublabel
        self.sublabel_color = sublabel_color

    def wrap(self, _avail_width: float, _avail_height: float) -> tuple[float, float]:
        return self.width, self.height

    def draw(self) -> None:
        c = self.canv
        w, h = self.width, self.height

        c.setFillColor(colors.HexColor(CARD_BG))
        c.setStrokeColor(colors.HexColor(RULE))
        c.setLineWidth(0.75)
        c.roundRect(0, 0, w, h, 5, fill=1, stroke=1)

        c.setFillColor(colors.HexColor(self.accent_color))
        c.roundRect(0, 0, 5, h, 2.5, fill=1, stroke=0)
        # square off the accent bar's right edge so it reads as a straight
        # tab rather than a floating pill
        c.rect(2.5, 0, 2.5, h, fill=1, stroke=0)

        c.setFont(FONT_BOLD, 7.5)
        c.setFillColor(colors.HexColor(MUTED))
        c.drawString(13, h - 16, _truncate(self.label.upper(), 22))

        value_font_size = 19 if len(self.value) <= 9 else 14
        c.setFont(FONT_BOLD, value_font_size)
        c.setFillColor(colors.HexColor(self.value_color))
        c.drawString(13, h - 36, _truncate(self.value, 16))

        if self.sublabel:
            c.setFont(FONT_BOLD, 8)
            c.setFillColor(colors.HexColor(self.sublabel_color))
            c.drawString(13, 7, _truncate(self.sublabel, 24))


def _delta_sublabel(compare) -> tuple[str, str]:
    if compare.prior_total == 0:
        return "No prior period", MUTED
    label = format_delta_label(compare)
    if compare.current_total > compare.prior_total:
        return f"{label} vs prior", _NEGATIVE
    if compare.current_total < compare.prior_total:
        return f"{label} vs prior", _POSITIVE
    return f"{label} vs prior", MUTED


def _kpi_band(data: ReportData, styles: dict) -> Table:
    summary = data.summary
    delta_text, delta_color = _delta_sublabel(data.compare)
    zones_affected = sum(1 for z in summary.zone_totals if z.total > 0)

    col_width = _CONTENT_WIDTH / 3
    card_width = col_width - 8
    card_height = 54

    cards = [
        _KPICard(
            card_width, card_height, "Total Incidents", str(summary.grand_total), BRAND,
            sublabel=delta_text, sublabel_color=delta_color,
        ),
        _KPICard(
            card_width, card_height, "Critical", str(summary.severity_counts.Critical),
            SEVERITY_COLORS["Critical"], value_color=SEVERITY_COLORS["Critical"],
        ),
        _KPICard(
            card_width, card_height, "High", str(summary.severity_counts.High),
            SEVERITY_COLORS["High"], value_color=SEVERITY_COLORS["High"],
        ),
        _KPICard(card_width, card_height, "Open Incidents", str(summary.open_incidents), "#8b5cf6"),
        _KPICard(
            card_width, card_height, "Active Cameras",
            f"{summary.active_cameras}/{summary.total_cameras}", "#14b8a6",
        ),
        _KPICard(card_width, card_height, "Zones Affected", str(zones_affected), "#eab308"),
    ]
    table = Table([cards[:3], cards[3:]], colWidths=[col_width] * 3)
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return table


def _insights_bullets(insights: list[str], styles: dict) -> list:
    if not insights:
        return [Paragraph("No notable insights for this period.", styles["Muted"])]
    return [Paragraph(f"•  {_bold_markup(text)}", styles["Bullet"]) for text in insights]


def _style_value_axis(axis) -> None:
    axis.visibleGrid = True
    axis.gridStrokeColor = colors.HexColor(RULE)
    axis.gridStrokeWidth = 0.5
    axis.strokeColor = colors.HexColor(RULE)
    axis.labels.fillColor = colors.HexColor(MUTED)
    axis.labels.fontName = FONT_REGULAR
    axis.labels.fontSize = 7


def _style_category_axis(axis, font_size: float = 6.5) -> None:
    axis.strokeColor = colors.HexColor(RULE)
    axis.labels.fillColor = colors.HexColor(MUTED)
    axis.labels.fontName = FONT_REGULAR
    axis.labels.fontSize = font_size


def _severity_drawing(summary) -> Drawing:
    sev = summary.severity_counts
    counts = [getattr(sev, s) for s in _SEVERITY_ORDER]
    total = sum(counts) or 1

    d = Drawing(_CONTENT_WIDTH, 44 * mm)
    pie = Pie()
    pie.x = 12
    pie.y = 6
    pie.width = 40 * mm
    pie.height = 40 * mm
    pie.data = counts
    pie.labels = None
    pie.simpleLabels = False
    pie.slices.strokeWidth = 1.25
    pie.slices.strokeColor = colors.white
    for i, sev_name in enumerate(_SEVERITY_ORDER):
        pie.slices[i].fillColor = colors.HexColor(SEVERITY_COLORS[sev_name])
    d.add(pie)

    legend = Legend()
    legend.x = 66 * mm
    legend.y = 34 * mm
    legend.dx = 9
    legend.dy = 9
    legend.dxTextSpace = 6
    legend.columnMaximum = len(_SEVERITY_ORDER)
    legend.fontName = FONT_BOLD
    legend.fontSize = 9.5
    legend.leading = 16
    legend.alignment = "left"
    legend.colorNamePairs = [
        (
            colors.HexColor(SEVERITY_COLORS[sev_name]),
            f"{sev_name}   {counts[i]} · {counts[i] / total * 100:.0f}%",
        )
        for i, sev_name in enumerate(_SEVERITY_ORDER)
    ]
    d.add(legend)
    return d


def _zone_section(data: ReportData, styles: dict) -> list:
    summary = data.summary
    elements: list = []
    if data.zone_scope_label != "All zones":
        elements.append(
            Paragraph(
                f"Site-wide totals — headline figures above are filtered to "
                f"{escape(data.zone_scope_label)}.",
                styles["Muted"],
            )
        )
        elements.append(Spacer(1, 4))

    if not summary.zone_totals:
        elements.append(Paragraph("No zones configured.", styles["Muted"]))
        return elements

    zones = summary.zone_totals
    d = Drawing(_CONTENT_WIDTH, 55 * mm)
    chart = VerticalBarChart()
    chart.x = 12 * mm
    chart.y = 14 * mm
    chart.width = _CONTENT_WIDTH - 20 * mm
    chart.height = 36 * mm
    chart.data = [[z.total for z in zones]]
    chart.categoryAxis.categoryNames = [_truncate(z.zone_name, 14) for z in zones]
    _style_category_axis(chart.categoryAxis)
    if len(zones) > 6:
        chart.categoryAxis.labels.angle = 30
        chart.categoryAxis.labels.dy = -8
        chart.categoryAxis.labels.dx = -4
    chart.valueAxis.valueMin = 0
    _style_value_axis(chart.valueAxis)
    chart.barLabels.fontName = FONT_BOLD
    chart.barLabels.fontSize = 7
    chart.barLabels.fillColor = colors.HexColor(INK)
    chart.barLabels.dy = 4
    chart.barLabelFormat = "%d"
    chart.bars.strokeWidth = 0
    for i, zone in enumerate(zones):
        color = UNASSIGNED_COLOR if zone.zone_id is None else ZONE_PALETTE[i % len(ZONE_PALETTE)]
        chart.bars[(0, i)].fillColor = colors.HexColor(color)
    d.add(chart)
    elements.append(d)
    return elements


def _trend_section(trend, styles: dict) -> list:
    totals = [sum(p.zone_totals.values()) for p in trend.points]
    if not trend.points or sum(totals) <= 0:
        return [Paragraph("No trend data for this period.", styles["Muted"])]

    d = Drawing(_CONTENT_WIDTH, 55 * mm)
    chart = HorizontalLineChart()
    chart.x = 16 * mm
    chart.y = 16 * mm
    chart.width = _CONTENT_WIDTH - 26 * mm
    chart.height = 34 * mm
    chart.data = [totals]
    chart.categoryAxis.categoryNames = [p.date for p in trend.points]
    _style_category_axis(chart.categoryAxis)
    if len(trend.points) > 12:
        chart.categoryAxis.labels.angle = 45
        chart.categoryAxis.labels.dy = -10
        chart.categoryAxis.labels.dx = -4
    chart.valueAxis.valueMin = 0
    _style_value_axis(chart.valueAxis)
    chart.lines[0].strokeColor = colors.HexColor(BRAND)
    chart.lines[0].strokeWidth = 2
    chart.lines[0].symbol = makeMarker("FilledCircle")
    chart.lines[0].symbol.fillColor = colors.HexColor(BRAND)
    chart.lines[0].symbol.strokeColor = colors.white
    chart.lines[0].symbol.strokeWidth = 0.75
    chart.lines[0].symbol.size = 4.5
    d.add(chart)
    return [d]


def _compare_section(compare, styles: dict) -> list:
    points = compare.points
    if len(points) > 14:
        points = _downsample_weekly(points)
    if not points:
        return [Paragraph("No comparison data for this period.", styles["Muted"])]

    # Legend lives in its own reserved strip above the plot area — never
    # inside the chart's coordinate box — so it can never overlap a bar
    # regardless of data height (this was the bug: the legend used to sit
    # inside the same bounds the tallest bar could reach).
    drawing_height = 64 * mm
    legend_strip = 14 * mm
    chart_height = drawing_height - legend_strip - 16 * mm

    chart = VerticalBarChart()
    chart.x = 16 * mm
    chart.y = 14 * mm
    chart.width = _CONTENT_WIDTH - 26 * mm
    chart.height = chart_height
    chart.data = [[p.current for p in points], [p.prior for p in points]]
    chart.categoryAxis.categoryNames = [p.label for p in points]
    _style_category_axis(chart.categoryAxis)
    chart.valueAxis.valueMin = 0
    _style_value_axis(chart.valueAxis)
    chart.groupSpacing = 8
    chart.barSpacing = 1.5
    chart.bars.strokeWidth = 0
    for i in range(len(points)):
        chart.bars[(0, i)].fillColor = colors.HexColor(BRAND)
        chart.bars[(1, i)].fillColor = colors.HexColor(UNASSIGNED_COLOR)

    legend = Legend()
    legend.x = 16 * mm
    legend.y = drawing_height - 8 * mm
    legend.dx = 8
    legend.dy = 8
    legend.dxTextSpace = 5
    legend.deltax = 85
    legend.columnMaximum = 1
    legend.fontName = FONT_BOLD
    legend.fontSize = 8.5
    legend.alignment = "left"
    legend.colorNamePairs = [
        (colors.HexColor(BRAND), "Current period"),
        (colors.HexColor(UNASSIGNED_COLOR), "Prior period"),
    ]

    outer = Drawing(_CONTENT_WIDTH, drawing_height)
    outer.add(chart)
    outer.add(legend)
    return [outer]


def _downsample_weekly(points: list) -> list:
    buckets = []
    for i in range(0, len(points), 7):
        chunk = points[i : i + 7]
        buckets.append(
            ComparePoint(
                label=f"Wk {i // 7 + 1}",
                current=sum(p.current for p in chunk),
                prior=sum(p.prior for p in chunk),
            )
        )
    return buckets


def _type_table(summary, styles: dict) -> Table:
    header = ["Category", "Type", "Count", "% of total"]
    rows = [header]
    total = summary.grand_total or 1
    for t in summary.type_counts[:10]:
        rows.append(
            [
                t.category.upper(),
                t.type,
                str(t.count),
                f"{t.count / total * 100:.0f}%",
            ]
        )
    if len(rows) == 1:
        rows.append(["—", "No incident types recorded.", "", ""])

    table = Table(rows, colWidths=[25 * mm, _CONTENT_WIDTH - 25 * mm - 25 * mm - 25 * mm, 25 * mm, 25 * mm])
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), FONT_REGULAR),
                ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
                ("FONTSIZE", (0, 1), (-1, -1), 8.5),
                ("FONTSIZE", (0, 0), (-1, 0), 7.5),
                ("TEXTCOLOR", (0, 1), (-1, -1), colors.HexColor(INK)),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(INK)),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor(CARD_BG)]),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return table


def _incident_table(rows: list[ReportIncidentRow], styles: dict) -> Table:
    header = ["Time", "Severity", "Category", "Type", "Zone", "Camera"]
    table_rows = [header]
    for row in rows:
        table_rows.append(
            [
                row.timestamp_local,
                row.severity,
                row.category,
                row.type,
                row.zone_name,
                row.camera_label,
            ]
        )
    if len(table_rows) == 1:
        table_rows.append(["—", "—", "—", "No incidents in this period.", "—", "—"])

    col_widths = [
        28 * mm, 18 * mm, 18 * mm, _CONTENT_WIDTH - 28 * mm - 18 * mm - 18 * mm - 28 * mm - 28 * mm,
        28 * mm, 28 * mm,
    ]
    table = Table(table_rows, colWidths=col_widths, repeatRows=1)
    style_commands = [
        ("FONTNAME", (0, 0), (-1, -1), FONT_REGULAR),
        ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("FONTSIZE", (0, 0), (-1, 0), 7.5),
        ("TEXTCOLOR", (0, 1), (-1, -1), colors.HexColor(INK)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(INK)),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor(CARD_BG)]),
        ("TOPPADDING", (0, 0), (-1, -1), 4.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]
    for i, row in enumerate(rows, start=1):
        color = SEVERITY_COLORS.get(row.severity)
        if color:
            style_commands.append(("TEXTCOLOR", (1, i), (1, i), colors.HexColor(color)))
            style_commands.append(("FONTNAME", (1, i), (1, i), FONT_BOLD))
    table.setStyle(TableStyle(style_commands))
    return table


def _evidence_appendix(
    rows: list[ReportIncidentRow], snapshots: dict[str, bytes], styles: dict
) -> Table | None:
    cells = []
    for row in rows:
        if row.snapshot_url is None or row.snapshot_url not in snapshots:
            continue
        img = _evidence_image(snapshots[row.snapshot_url], target_width=(_CONTENT_WIDTH - 8 * mm) / 2)
        if img is None:
            continue
        caption = Paragraph(
            f"{escape(row.timestamp_local)} · {escape(row.type)} · {escape(row.zone_name)}",
            styles["Caption"],
        )
        cells.append([img, Spacer(1, 2), caption])
        if len(cells) >= settings.REPORT_MAX_SNAPSHOTS:
            break

    if not cells:
        return None

    grid_rows = []
    for i in range(0, len(cells), 2):
        pair = cells[i : i + 2]
        if len(pair) < 2:
            pair.append("")
        grid_rows.append(pair)

    table = Table(grid_rows, colWidths=[_CONTENT_WIDTH / 2] * 2)
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    return table


def _evidence_image(img_bytes: bytes, target_width: float) -> RLImage | None:
    try:
        with PILImage.open(BytesIO(img_bytes)) as im:
            width, height = im.size
        if width <= 0 or height <= 0:
            return None
        target_height = target_width * (height / width)
        return RLImage(BytesIO(img_bytes), width=target_width, height=target_height)
    except Exception:
        logger.warning("Could not decode evidence image for report appendix.", exc_info=True)
        return None


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _footer(data: ReportData):
    def _draw(canvas, doc) -> None:
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor(RULE))
        canvas.setLineWidth(0.5)
        rule_y = 14 * mm
        canvas.line(_MARGIN, rule_y, _PAGE_SIZE[0] - _MARGIN, rule_y)

        canvas.setFont(FONT_REGULAR, 7.5)
        canvas.setFillColor(colors.HexColor(MUTED))
        left_text = f"{data.company_name} — Confidential · Generated {data.generated_at_local}"
        canvas.drawString(_MARGIN, 10 * mm, left_text)
        right_text = f"Page {canvas.getPageNumber()}"
        canvas.drawRightString(_PAGE_SIZE[0] - _MARGIN, 10 * mm, right_text)
        canvas.restoreState()

    return _draw
