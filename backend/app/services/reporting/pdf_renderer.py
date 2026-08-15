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
    CondPageBreak,
    Flowable,
    Image as RLImage,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.core.config import settings
from app.schemas.analytics import ComparePoint
from app.services.reporting.i18n import t
from app.services.reporting.report_data import (
    ReportData,
    ReportIncidentRow,
    format_delta_label,
)

logger = logging.getLogger(__name__)

# Report-local display labels only — these do NOT change the underlying
# `severity`/`category` values used anywhere else in the app (dashboard,
# analytics API, etc.). See plan §0.2 scope note.
_SEVERITY_LABEL_VI = {"Critical": "Nghiêm Trọng", "High": "Cao", "Medium": "Trung Bình", "Low": "Thấp"}
_CATEGORY_LABEL_VI = {"ppe": "PPE", "zone": "Khu Vực", "behavior": "Hành Vi"}


def _severity_label(severity: str, language: str) -> str:
    return _SEVERITY_LABEL_VI.get(severity, severity) if language == "vi" else severity


def _category_label(category: str, language: str) -> str:
    if language == "vi":
        return _CATEGORY_LABEL_VI.get(category.lower(), category.upper())
    return category.upper()

_FONTS_DIR = Path(__file__).parent / "fonts"
_PAGE_SIZE = A4
_MARGIN = 18 * mm
_CONTENT_WIDTH = _PAGE_SIZE[0] - 2 * _MARGIN

# Semantic severity scale — the ONLY place red/orange/amber/green appear.
# Every severity reference in the report (KPI cards, table text, the
# severity bar) draws from this single mapping so the meaning stays fixed.
SEVERITY_COLORS = {
    "Critical": "#dc2626",
    "High": "#ea580c",
    "Medium": "#d97706",
    "Low": "#16a34a",
}
_SEVERITY_ORDER = ("Critical", "High", "Medium", "Low")
INK = "#0f172a"
ACCENT = "#bef264"       # bright lime — header text / eyebrow, tuned for contrast on INK
BRAND = "#0ea5e9"        # sky blue — the single neutral color for every non-severity
                         # chart (zone counts, trend line, period comparison); a
                         # second series within the same chart is a tint of BRAND,
                         # never a new hue, so color always means the same thing
MUTED = "#64748b"
RULE = "#e2e8f0"
CARD_BG = "#f8fafc"
CAVEAT_BG = "#fef3c7"
CAVEAT_TEXT = "#92400e"
_POSITIVE = "#16a34a"    # fewer incidents than prior period
_NEGATIVE = "#dc2626"    # more incidents than prior period

_BOLD_MARKER = re.compile(r"\*\*(.+?)\*\*")


def _tint(hex_color: str, alpha: float) -> colors.Color:
    """Same hue, lower opacity — used to distinguish a secondary series
    (e.g. 'prior period', 'unassigned zone') without introducing a new,
    meaningless color into a chart."""
    return colors.HexColor(hex_color).clone(alpha=alpha)


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
# Standard PDF base-14 fonts — always available, no embedding needed. Used
# for timestamps and tabular figures throughout the report (metadata
# "Generated at", the incident table's Time column, KPI values, chart axis
# numbers) to visually tie those values back to the monitoring system they
# came from, distinct from the sans-serif used for labels/prose.
FONT_MONO = "Courier"
FONT_MONO_BOLD = "Courier-Bold"


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
        "Mono": ParagraphStyle(
            "ReportMono", parent=base["Normal"], fontName=FONT_MONO,
            fontSize=9, leading=12, textColor=colors.HexColor(INK),
        ),
        "TableCell": ParagraphStyle(
            "ReportTableCell", parent=base["Normal"], fontName=FONT_REGULAR,
            fontSize=8, leading=10, textColor=colors.HexColor(INK),
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
        title=f"{t('pdf_title_prefix', data.language)} — {data.factory_name}",
        author=data.company_name,
    )

    lang = data.language
    story: list = []
    story.append(_header_band(data, styles))
    story.append(Spacer(1, 12))
    story.append(_metadata_table(data, styles))
    if data.data_caveats:
        story.append(Spacer(1, 8))
        story.append(_caveats_box(data.data_caveats, styles))
    story.append(Spacer(1, 12))
    story.append(_kpi_band(data, styles))
    story.extend(_section(t("key_insights", lang), styles, _insights_bullets(data.insights, styles, lang)))

    if data.summary.grand_total == 0:
        story.append(Spacer(1, 24))
        story.append(Paragraph(t("no_incidents_period", lang), styles["Centered"]))
        doc.build(story, onFirstPage=_footer(data), onLaterPages=_footer(data))
        return buffer.getvalue()

    story.extend(_section(t("severity_distribution", lang), styles, [_severity_drawing(data.summary, lang)]))
    story.extend(_section(t("incidents_by_zone", lang), styles, _zone_section(data, styles)))
    story.extend(_section(t("trend_over_time", lang), styles, _trend_section(data.trend, styles, lang)))
    story.extend(_section(t("current_vs_prior", lang), styles, _compare_section(data.compare, styles, lang)))
    story.extend(_section(t("incident_types", lang), styles, [_type_table(data.summary, styles, lang)]))
    story.extend(
        _section_flowing(
            t("recent_priority_incidents", lang), styles, [_incident_table(data.top_incidents, styles, lang)]
        )
    )

    if snapshots:
        appendix = _evidence_appendix(data.top_incidents, snapshots, styles)
        if appendix is not None:
            story.extend(_section(t("evidence_appendix", lang), styles, [appendix]))

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


def _section(heading: str, styles: dict, flowables: list) -> list:
    """Groups a section heading together with its immediate, fixed-height
    content (a chart or a small, bounded table) in one KeepTogether unit,
    so the heading can never get orphaned at the bottom of a page while
    its content is pushed to the next one."""
    return [KeepTogether([*_heading_block(heading, styles), *flowables])]


def _section_flowing(heading: str, styles: dict, flowables: list, min_height: float = 110) -> list:
    """Like _section, but for a table that may itself span several pages
    (repeatRows keeps its header row visible on each). KeepTogether would
    force the *entire* table onto a fresh page whenever it doesn't fit the
    remaining space, trading the orphaned-heading bug for an even larger
    blank gap. Instead, only guarantee the heading isn't placed unless at
    least the header row plus one full data row can follow it on the same
    page — 110pt covers heading (~46pt) + header + one wrapped data row
    (~50pt) with headroom; a smaller margin let the table split right
    after its header, stranding the heading above an empty page tail."""
    return [CondPageBreak(min_height), *_heading_block(heading, styles), *flowables]


def _bold_markup(text: str) -> str:
    escaped = escape(text)
    return _BOLD_MARKER.sub(r"<b>\1</b>", escaped)


def _header_band(data: ReportData, styles: dict) -> Table:
    eyebrow = Paragraph(
        f'<font face="{FONT_BOLD}" size="8" color="{ACCENT}">{escape(t("report_eyebrow", data.language))}</font>',
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
    lang = data.language
    # (label, value, is_monospace) — is_monospace used to be derived by
    # comparing label text against the literal "Generated at", which broke
    # once labels became translatable. Use an explicit flag instead.
    rows = [
        (t("factory", lang), data.factory_name, False, t("location", lang), data.factory_location or "—", False),
        (t("period", lang), data.range_label, False, t("zone_scope", lang), data.zone_scope_label, False),
        (t("generated_at", lang), data.generated_at_local, True, t("timezone", lang), data.timezone_label, False),
    ]
    table_rows = []
    for label_a, value_a, mono_a, label_b, value_b, mono_b in rows:
        style_a = styles["Mono"] if mono_a else styles["Body"]
        style_b = styles["Mono"] if mono_b else styles["Body"]
        table_rows.append(
            [
                Paragraph(f"<b>{escape(label_a)}</b>", styles["Muted"]),
                Paragraph(escape(value_a), style_a),
                Paragraph(f"<b>{escape(label_b)}</b>", styles["Muted"]),
                Paragraph(escape(value_b), style_b),
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

        value_font_size = 18 if len(self.value) <= 9 else 13
        c.setFont(FONT_MONO_BOLD, value_font_size)
        c.setFillColor(colors.HexColor(self.value_color))
        c.drawString(13, h - 36, _truncate(self.value, 16))

        if self.sublabel:
            c.setFont(FONT_BOLD, 8)
            c.setFillColor(colors.HexColor(self.sublabel_color))
            c.drawString(13, 7, _truncate(self.sublabel, 24))


def _delta_sublabel(compare, language: str) -> tuple[str, str]:
    if compare.prior_total == 0:
        return t("no_prior_period", language), MUTED
    label = format_delta_label(compare)
    suffix = t("vs_prior", language)
    if compare.current_total > compare.prior_total:
        return f"{label} {suffix}", _NEGATIVE
    if compare.current_total < compare.prior_total:
        return f"{label} {suffix}", _POSITIVE
    return f"{label} {suffix}", MUTED


def _kpi_band(data: ReportData, styles: dict) -> Table:
    lang = data.language
    summary = data.summary
    delta_text, delta_color = _delta_sublabel(data.compare, lang)
    zones_affected = sum(1 for z in summary.zone_totals if z.total > 0)

    col_width = _CONTENT_WIDTH / 3
    card_width = col_width - 8
    card_height = 54

    cards = [
        _KPICard(
            card_width, card_height, t("total_incidents", lang), str(summary.grand_total), BRAND,
            sublabel=delta_text, sublabel_color=delta_color,
        ),
        _KPICard(
            card_width, card_height, _severity_label("Critical", lang), str(summary.severity_counts.Critical),
            SEVERITY_COLORS["Critical"], value_color=SEVERITY_COLORS["Critical"],
        ),
        _KPICard(
            card_width, card_height, _severity_label("High", lang), str(summary.severity_counts.High),
            SEVERITY_COLORS["High"], value_color=SEVERITY_COLORS["High"],
        ),
        _KPICard(card_width, card_height, t("open_incidents", lang), str(summary.open_incidents), "#8b5cf6"),
        _KPICard(
            card_width, card_height, t("active_cameras", lang),
            f"{summary.active_cameras}/{summary.total_cameras}", "#14b8a6",
        ),
        _KPICard(card_width, card_height, t("zones_affected", lang), str(zones_affected), "#eab308"),
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


def _insights_bullets(insights: list[str], styles: dict, language: str) -> list:
    if not insights:
        return [Paragraph(t("no_insights", language), styles["Muted"])]
    return [Paragraph(f"•  {_bold_markup(text)}", styles["Bullet"]) for text in insights]


def _style_value_axis(axis) -> None:
    axis.visibleGrid = True
    axis.gridStrokeColor = colors.HexColor(RULE)
    axis.gridStrokeWidth = 0.5
    axis.strokeColor = colors.HexColor(RULE)
    axis.labels.fillColor = colors.HexColor(MUTED)
    # Value axes are pure figures — monospace keeps them tabular and reads
    # as instrument-panel data rather than prose.
    axis.labels.fontName = FONT_MONO
    axis.labels.fontSize = 7


def _style_category_axis(axis, font_size: float = 6.5) -> None:
    axis.strokeColor = colors.HexColor(RULE)
    axis.labels.fillColor = colors.HexColor(MUTED)
    axis.labels.fontName = FONT_REGULAR
    axis.labels.fontSize = font_size


class _SeverityBar(Flowable):
    """A single 100%-stacked horizontal bar: one segment per severity,
    width proportional to its share of total incidents. Counts and
    percentages are labeled directly on each segment when it's wide enough
    to hold the text, and always repeated in the legend row underneath —
    so every value stays readable even for a near-zero-count severity."""

    _BAR_HEIGHT = 22
    _LEGEND_GAP = 10
    _LEGEND_HEIGHT = 16
    _MIN_LABEL_PAD = 6

    def __init__(self, width: float, counts: list[int], language: str = "en") -> None:
        super().__init__()
        self.width = width
        self.counts = counts
        self.language = language
        self.total = sum(counts) or 1
        self.height = self._BAR_HEIGHT + self._LEGEND_GAP + self._LEGEND_HEIGHT

    def wrap(self, _avail_width: float, _avail_height: float) -> tuple[float, float]:
        return self.width, self.height

    def draw(self) -> None:
        c = self.canv
        bar_y = self._LEGEND_GAP + self._LEGEND_HEIGHT

        x = 0.0
        for sev_name, count in zip(_SEVERITY_ORDER, self.counts):
            seg_width = count / self.total * self.width
            if seg_width <= 0:
                continue
            c.setFillColor(colors.HexColor(SEVERITY_COLORS[sev_name]))
            c.rect(x, bar_y, seg_width, self._BAR_HEIGHT, fill=1, stroke=0)

            label = f"{count} · {count / self.total * 100:.0f}%"
            label_width = c.stringWidth(label, FONT_MONO_BOLD, 8)
            if label_width + self._MIN_LABEL_PAD <= seg_width:
                c.setFont(FONT_MONO_BOLD, 8)
                c.setFillColor(colors.white)
                c.drawCentredString(x + seg_width / 2, bar_y + self._BAR_HEIGHT / 2 - 3, label)
            x += seg_width

        # thin white separators between segments so adjacent severities with
        # similar hues (e.g. High/Medium) stay visually distinct
        c.setStrokeColor(colors.white)
        c.setLineWidth(1.25)
        x = 0.0
        for count in self.counts:
            seg_width = count / self.total * self.width
            x += seg_width
            if seg_width > 0 and x < self.width - 0.5:
                c.line(x, bar_y, x, bar_y + self._BAR_HEIGHT)

        # legend row: swatch + name (sans) + count/percent (mono), one
        # column per severity, always visible regardless of segment width
        col_width = self.width / len(_SEVERITY_ORDER)
        for i, (sev_name, count) in enumerate(zip(_SEVERITY_ORDER, self.counts)):
            cx = i * col_width
            c.setFillColor(colors.HexColor(SEVERITY_COLORS[sev_name]))
            c.rect(cx, 4, 8, 8, fill=1, stroke=0)

            display_name = _severity_label(sev_name, self.language)
            c.setFont(FONT_BOLD, 8)
            c.setFillColor(colors.HexColor(INK))
            c.drawString(cx + 12, 5, display_name)
            name_width = c.stringWidth(display_name, FONT_BOLD, 8)

            c.setFont(FONT_MONO_BOLD, 8)
            c.setFillColor(colors.HexColor(MUTED))
            pct = count / self.total * 100
            c.drawString(cx + 12 + name_width + 5, 5, f"{count} · {pct:.0f}%")


def _severity_drawing(summary, language: str = "en") -> Flowable:
    sev = summary.severity_counts
    counts = [getattr(sev, s) for s in _SEVERITY_ORDER]
    return _SeverityBar(_CONTENT_WIDTH, counts, language)


def _zone_section(data: ReportData, styles: dict) -> list:
    lang = data.language
    summary = data.summary
    elements: list = []
    if data.zone_id is not None:  # was a fragile string compare — see plan §0.2
        elements.append(
            Paragraph(
                t("site_wide_filtered_note", lang).format(zone=escape(data.zone_scope_label)),
                styles["Muted"],
            )
        )
        elements.append(Spacer(1, 4))

    if not summary.zone_totals:
        elements.append(Paragraph(t("no_zones_configured", lang), styles["Muted"]))
        return elements

    zones = summary.zone_totals
    d = Drawing(_CONTENT_WIDTH, 60 * mm)
    chart = VerticalBarChart()
    chart.x = 12 * mm
    chart.y = 20 * mm
    chart.width = _CONTENT_WIDTH - 20 * mm
    chart.height = 32 * mm
    chart.data = [[z.total for z in zones]]
    # Font and per-line width budget scale down as more zones share the same
    # chart width, so a wrapped label can never bleed into its neighbor.
    category_font_size = 7 if len(zones) <= 8 else 6
    label_budget = (chart.width / len(zones)) * 0.92
    chart.categoryAxis.categoryNames = [
        _wrap_label(z.zone_name, label_budget, FONT_REGULAR, category_font_size) for z in zones
    ]
    _style_category_axis(chart.categoryAxis, font_size=category_font_size)
    chart.categoryAxis.labels.leading = category_font_size + 1.5
    chart.valueAxis.valueMin = 0
    _style_value_axis(chart.valueAxis)
    chart.barLabels.fontName = FONT_MONO_BOLD
    chart.barLabels.fontSize = 7
    chart.barLabels.fillColor = colors.HexColor(INK)
    chart.barLabels.dy = 4
    chart.barLabelFormat = "%d"
    chart.bars.strokeWidth = 0
    for i, zone in enumerate(zones):
        # Single neutral brand color everywhere — an unassigned-zone bar is
        # a lighter tint of the same hue, never a different, meaningless color.
        chart.bars[(0, i)].fillColor = _tint(BRAND, 0.45) if zone.zone_id is None else colors.HexColor(BRAND)
    d.add(chart)
    elements.append(d)
    return elements


def _trend_section(trend, styles: dict, language: str = "en") -> list:
    totals = [sum(p.zone_totals.values()) for p in trend.points]
    if not trend.points or sum(totals) <= 0:
        return [Paragraph(t("no_trend_data", language), styles["Muted"])]

    d = Drawing(_CONTENT_WIDTH, 55 * mm)
    chart = HorizontalLineChart()
    chart.x = 16 * mm
    chart.y = 16 * mm
    chart.width = _CONTENT_WIDTH - 26 * mm
    chart.height = 34 * mm
    chart.data = [totals]
    chart.categoryAxis.categoryNames = [p.date for p in trend.points]
    _style_category_axis(chart.categoryAxis)
    chart.categoryAxis.labels.fontName = FONT_MONO  # dates are data, not prose
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


def _compare_section(compare, styles: dict, language: str = "en") -> list:
    points = compare.points
    if len(points) > 14:
        points = _downsample_weekly(points)
    if not points:
        return [Paragraph(t("no_compare_data", language), styles["Muted"])]

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
        chart.bars[(1, i)].fillColor = _tint(BRAND, 0.4)

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
        (colors.HexColor(BRAND), t("current_period", language)),
        (_tint(BRAND, 0.4), t("prior_period", language)),
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


def _type_table(summary, styles: dict, language: str = "en") -> Table:
    header = [t("col_category", language), t("col_type", language), t("col_count", language), t("col_pct_total", language)]
    rows = [header]
    total = summary.grand_total or 1
    for row in summary.type_counts[:10]:
        rows.append(
            [
                _category_label(row.category, language),
                Paragraph(escape(row.type), styles["TableCell"]),
                str(row.count),
                f"{row.count / total * 100:.0f}%",
            ]
        )
    if len(rows) == 1:
        rows.append(["—", t("no_incident_types", language), "", ""])

    table = Table(
        rows,
        colWidths=[25 * mm, _CONTENT_WIDTH - 25 * mm - 25 * mm - 25 * mm, 25 * mm, 25 * mm],
        repeatRows=1,
    )
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
                # Count / % of total are pure figures — monospace for tabular alignment
                ("FONTNAME", (2, 1), (3, -1), FONT_MONO),
            ]
        )
    )
    return table


def _incident_table(rows: list[ReportIncidentRow], styles: dict, language: str = "en") -> Table:
    header = [
        t("col_time", language), t("col_severity", language), t("col_category", language),
        t("col_type", language), t("col_zone", language), t("col_camera", language),
    ]
    table_rows = [header]
    for row in rows:
        severity_color = SEVERITY_COLORS.get(row.severity, INK)
        table_rows.append(
            [
                row.timestamp_local,
                Paragraph(
                    f'<font face="{FONT_BOLD}" color="{severity_color}">'
                    f"{escape(_severity_label(row.severity, language))}</font>",
                    styles["TableCell"],
                ),
                # already translated by report_data.py's _CATEGORY_LABELS
                Paragraph(escape(row.category), styles["TableCell"]),
                Paragraph(escape(row.type), styles["TableCell"]),
                Paragraph(escape(row.zone_name), styles["TableCell"]),
                Paragraph(escape(row.camera_label), styles["TableCell"]),
            ]
        )
    if len(table_rows) == 1:
        table_rows.append(["—", "—", "—", t("no_incidents_table", language), "—", "—"])

    # Every free-text/variable-length cell is wrapped in a Paragraph so it
    # wraps instead of silently overflowing into the next column once a
    # translation or a long zone/camera name outgrows its fixed width. This
    # bit both languages in practice: Vietnamese severity labels ("Nghiêm
    # Trọng", "Trung Bình") and the English "Behavior" category label are
    # each wider than the old fixed-width plain-string columns allowed for,
    # which bled the text into the neighboring column.
    col_widths = [33 * mm, 29 * mm, 21 * mm, 33 * mm, 33 * mm, _CONTENT_WIDTH - 149 * mm]
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
        # Time is a timestamp — monospace ties it visually to the source data;
        # sized slightly down from the other columns' 8pt so it reliably fits
        # its column (Courier runs wider per-character than the sans body font)
        ("FONTNAME", (0, 1), (0, -1), FONT_MONO),
        ("FONTSIZE", (0, 1), (0, -1), 7.5),
    ]
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


def _wrap_label(
    text: str, max_width_pts: float, font_name: str = FONT_REGULAR, font_size: float = 7
) -> str:
    """Wraps a chart category label onto two lines at a word boundary
    instead of truncating it — full zone names should always be readable.
    Both lines are sized to the actual per-category width (via stringWidth,
    not a fixed character count) so labels never bleed into the
    neighboring category regardless of how many bars share the chart. A
    third line's worth of leftover text (rare — most zone names are a
    handful of words) is ellipsized rather than left to overflow."""
    if pdfmetrics.stringWidth(text, font_name, font_size) <= max_width_pts:
        return text
    words = text.split(" ")
    if len(words) == 1:
        return text

    def _fill(candidates: list[str]) -> tuple[str, int]:
        line = ""
        used = 0
        for word in candidates:
            candidate = f"{line} {word}".strip()
            if not line or pdfmetrics.stringWidth(candidate, font_name, font_size) <= max_width_pts:
                line = candidate
                used += 1
            else:
                break
        return line, used

    line1, used1 = _fill(words)
    remaining = words[used1:]
    if not remaining:
        return line1

    line2, used2 = _fill(remaining)
    if used2 < len(remaining):
        while line2 and pdfmetrics.stringWidth(f"{line2}…", font_name, font_size) > max_width_pts:
            line2 = line2.rsplit(" ", 1)[0] if " " in line2 else line2[:-1]
        line2 = f"{line2}…" if line2 else "…"
    return f"{line1}\n{line2}"


def _footer(data: ReportData):
    def _draw(canvas, doc) -> None:
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor(RULE))
        canvas.setLineWidth(0.5)
        rule_y = 14 * mm
        canvas.line(_MARGIN, rule_y, _PAGE_SIZE[0] - _MARGIN, rule_y)

        canvas.setFont(FONT_REGULAR, 7.5)
        canvas.setFillColor(colors.HexColor(MUTED))
        left_text = f"{data.company_name} — {t('confidential_generated', data.language)} {data.generated_at_local}"
        canvas.drawString(_MARGIN, 10 * mm, left_text)
        right_text = f"{t('page', data.language)} {canvas.getPageNumber()}"
        canvas.drawRightString(_PAGE_SIZE[0] - _MARGIN, 10 * mm, right_text)
        canvas.restoreState()

    return _draw
