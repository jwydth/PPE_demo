"""Static translation table for report chrome (PDF section headings, table
headers, KPI card labels, placeholder/empty-state text, metadata labels).

Deliberately NOT used for: incident type labels, zone names, camera names
(these come from app/services/incident_normalization.py and user-configured
data — see plan §0.2, do not translate them here or anywhere else). Severity
and category words get their own small report-local maps in pdf_renderer.py
/ report_data.py (§2.3.4) since they're single words reused in several
render contexts (table cells, chart legends, canvas-drawn text) rather than
one-shot Paragraph strings.

Usage: t("key_insights", data.language) -> "Key Insights" | "Thông Tin Chính"
Unknown key or unknown language both fall back to English rather than
raising, so a missing translation degrades gracefully instead of crashing
PDF generation.
"""

from typing import Literal

Language = Literal["en", "vi"]
LANGUAGES: tuple[Language, ...] = ("en", "vi")
DEFAULT_LANGUAGE: Language = "en"

_STRINGS: dict[str, dict[str, str]] = {
    # Header / metadata
    "report_eyebrow": {"en": "INCIDENT ANALYTICS REPORT", "vi": "BÁO CÁO PHÂN TÍCH SỰ CỐ"},
    "pdf_title_prefix": {"en": "Incident Analytics Report", "vi": "Báo Cáo Phân Tích Sự Cố"},
    "factory": {"en": "Factory", "vi": "Nhà Máy"},
    "location": {"en": "Location", "vi": "Vị Trí"},
    "period": {"en": "Period", "vi": "Kỳ Báo Cáo"},
    "zone_scope": {"en": "Zone scope", "vi": "Phạm Vi Khu Vực"},
    "generated_at": {"en": "Generated at", "vi": "Thời Gian Tạo"},
    "timezone": {"en": "Timezone", "vi": "Múi Giờ"},
    "all_zones": {"en": "All zones", "vi": "Tất cả khu vực"},

    # Section headings
    "key_insights": {"en": "Key Insights", "vi": "Thông Tin Chính"},
    "severity_distribution": {"en": "Severity Distribution", "vi": "Phân Bố Mức Độ Nghiêm Trọng"},
    "incidents_by_zone": {"en": "Incidents by Zone", "vi": "Sự Cố Theo Khu Vực"},
    "trend_over_time": {"en": "Trend Over Time", "vi": "Xu Hướng Theo Thời Gian"},
    "current_vs_prior": {"en": "Current vs Prior Period", "vi": "Kỳ Hiện Tại So Với Kỳ Trước"},
    "incident_types": {"en": "Incident Types", "vi": "Loại Sự Cố"},
    "recent_priority_incidents": {"en": "Recent Priority Incidents", "vi": "Sự Cố Ưu Tiên Gần Đây"},
    "evidence_appendix": {"en": "Evidence Appendix", "vi": "Phụ Lục Bằng Chứng"},

    # KPI cards
    "total_incidents": {"en": "Total Incidents", "vi": "Tổng Số Sự Cố"},
    "open_incidents": {"en": "Open Incidents", "vi": "Sự Cố Đang Mở"},
    "active_cameras": {"en": "Active Cameras", "vi": "Camera Hoạt Động"},
    "zones_affected": {"en": "Zones Affected", "vi": "Khu Vực Bị Ảnh Hưởng"},
    "no_prior_period": {"en": "No prior period", "vi": "Không có kỳ trước"},
    "vs_prior": {"en": "vs prior", "vi": "so với kỳ trước"},

    # Empty states / placeholders
    "no_incidents_period": {
        "en": "No incidents recorded in this period.",
        "vi": "Không có sự cố nào được ghi nhận trong kỳ này.",
    },
    "no_zones_configured": {"en": "No zones configured.", "vi": "Chưa cấu hình khu vực nào."},
    "no_trend_data": {
        "en": "No trend data for this period.",
        "vi": "Không có dữ liệu xu hướng cho kỳ này.",
    },
    "no_compare_data": {
        "en": "No comparison data for this period.",
        "vi": "Không có dữ liệu so sánh cho kỳ này.",
    },
    "no_insights": {
        "en": "No notable insights for this period.",
        "vi": "Không có thông tin nổi bật nào cho kỳ này.",
    },
    "no_incident_types": {
        "en": "No incident types recorded.",
        "vi": "Chưa ghi nhận loại sự cố nào.",
    },
    "no_incidents_table": {
        "en": "No incidents in this period.",
        "vi": "Không có sự cố nào trong kỳ này.",
    },
    "site_wide_filtered_note": {
        "en": "Site-wide totals — headline figures above are filtered to {zone}.",
        "vi": "Số liệu toàn nhà máy — các số liệu chính ở trên đã được lọc theo {zone}.",
    },

    # Table headers
    "col_category": {"en": "Category", "vi": "Danh Mục"},
    "col_type": {"en": "Type", "vi": "Loại"},
    "col_count": {"en": "Count", "vi": "Số Lượng"},
    "col_pct_total": {"en": "% of total", "vi": "% Tổng Số"},
    "col_time": {"en": "Time", "vi": "Thời Gian"},
    "col_severity": {"en": "Severity", "vi": "Mức Độ"},
    "col_zone": {"en": "Zone", "vi": "Khu Vực"},
    "col_camera": {"en": "Camera", "vi": "Camera"},

    # Chart legend
    "current_period": {"en": "Current period", "vi": "Kỳ hiện tại"},
    "prior_period": {"en": "Prior period", "vi": "Kỳ trước"},

    # Footer
    "confidential_generated": {"en": "Confidential · Generated", "vi": "Bảo Mật · Tạo Lúc"},
    "page": {"en": "Page", "vi": "Trang"},

    # Email
    "email_subject_prefix": {"en": "[Safety Report]", "vi": "[Báo Cáo An Toàn]"},
    "email_incidents_word": {"en": "incidents", "vi": "sự cố"},
    "email_critical_word": {"en": "critical", "vi": "nghiêm trọng"},
    "email_report_title": {
        "en": "Incident Analytics Report",
        "vi": "Báo Cáo Phân Tích Sự Cố",
    },
    "email_total": {"en": "Total", "vi": "Tổng Số"},
    "email_critical": {"en": "Critical", "vi": "Nghiêm Trọng"},
    "email_high": {"en": "High", "vi": "Cao"},
    "email_vs_prior": {"en": "vs prior period", "vi": "So với kỳ trước"},
    "email_key_insights": {"en": "Key insights", "vi": "Thông tin chính"},
    "email_no_insights": {
        "en": "No notable insights for this period.",
        "vi": "Không có thông tin nổi bật cho kỳ này.",
    },
    "email_pdf_attached": {
        "en": "The full report is attached as a PDF.",
        "vi": "Báo cáo đầy đủ được đính kèm dưới dạng PDF.",
    },
}


def t(key: str, language: str) -> str:
    """Looks up `key`, falling back to English if the language is unknown or
    the key has no entry for it. Never raises — a missing translation must
    not break report generation."""
    entry = _STRINGS.get(key)
    if entry is None:
        return key
    return entry.get(language, entry.get(DEFAULT_LANGUAGE, key))


def normalize_language(value: str | None) -> Language:
    """Coerces any input to a supported language code, defaulting to English
    for anything unrecognized (None, empty string, typo, future code)."""
    if value in LANGUAGES:
        return value  # type: ignore[return-value]
    return DEFAULT_LANGUAGE
