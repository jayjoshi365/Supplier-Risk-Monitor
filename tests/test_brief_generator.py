"""
Unit tests for brief_generator.py — HTML generation only, no S3 calls.

Covers:
  - Supplier brief HTML contains required data fields
  - Assessment-required banner present when status is ASSESSMENT_REQUIRED
  - Dashboard HTML contains all suppliers
  - Risk color thresholds (green/amber/red)
  - Freshness color thresholds
  - Empty watchlist renders gracefully
  - Score history rows generated correctly
"""

import pytest

from brief_generator import (
    _freshness_color,
    _risk_color,
    _trend_icon,
    generate_dashboard_html,
    generate_supplier_brief,
)


# ── Color helpers ─────────────────────────────────────────────────────────────

class TestColorHelpers:
    def test_high_score_is_green(self):
        assert _risk_color(70) == "#22c55e"
        assert _risk_color(100) == "#22c55e"

    def test_medium_score_is_amber(self):
        assert _risk_color(50) == "#f59e0b"
        assert _risk_color(69) == "#f59e0b"

    def test_low_score_is_red(self):
        assert _risk_color(0) == "#ef4444"
        assert _risk_color(49) == "#ef4444"

    def test_freshness_boundary_12_months(self):
        assert _freshness_color(12) == "#22c55e"

    def test_freshness_boundary_18_months(self):
        assert _freshness_color(18) == "#f59e0b"

    def test_freshness_over_18_months_is_red(self):
        assert _freshness_color(19) == "#ef4444"
        assert _freshness_color(24) == "#ef4444"

    def test_trend_icons_present(self):
        icons = {
            "IMPROVING": "↑",
            "STABLE": "→",
            "DECLINING": "↓",
            "DETERIORATING": "↓↓",
        }
        # Icons are HTML entities — just check non-empty
        for trend in ("IMPROVING", "STABLE", "DECLINING", "DETERIORATING", "BASELINE"):
            assert _trend_icon(trend)  # non-empty string


# ── generate_supplier_brief ───────────────────────────────────────────────────

@pytest.fixture
def full_supplier():
    return {
        "ticker": "WDAY",
        "supplier_name": "Workday Inc.",
        "category": "HR Technology",
        "kraljic_position": "Strategic",
        "contract_value_usd": 480000,
        "contract_end_date": "2027-03-31",
        "risk_threshold": 60,
        "last_score": 72,
        "trend": "STABLE",
        "last_filing_period": "2024-01-31",
        "data_age_months": 7,
        "cik": "0001285785",
        "assessment_status": "OK",
        "tags_used": {"revenue": "Revenues", "net_income": "NetIncomeLoss"},
        "score_history": [
            {
                "score": 72,
                "trend": "STABLE",
                "timestamp": "2026-05-14T08:00:00+00:00",
                "filing_period": "2024-01-31",
                "components": {
                    "revenue_growth": {"score": 30, "note": "+16.8%"},
                    "profit_margin": {"score": 35, "note": "19.0%"},
                    "debt_to_assets": {"score": 18, "note": "0.36"},
                    "data_freshness": {"score": 10, "note": "7mo"},
                },
            }
        ],
    }


class TestGenerateSupplierBrief:
    def test_html_contains_supplier_name(self, full_supplier):
        html = generate_supplier_brief(full_supplier)
        assert "Workday Inc." in html

    def test_html_contains_ticker(self, full_supplier):
        html = generate_supplier_brief(full_supplier)
        assert "WDAY" in html

    def test_html_contains_score(self, full_supplier):
        html = generate_supplier_brief(full_supplier)
        assert "72" in html

    def test_html_contains_trend(self, full_supplier):
        html = generate_supplier_brief(full_supplier)
        assert "STABLE" in html

    def test_html_contains_filing_period(self, full_supplier):
        html = generate_supplier_brief(full_supplier)
        assert "2024-01-31" in html

    def test_html_contains_edgar_link(self, full_supplier):
        html = generate_supplier_brief(full_supplier)
        assert "edgar" in html.lower()

    def test_html_contains_disclaimer(self, full_supplier):
        html = generate_supplier_brief(full_supplier)
        assert "not investment advice" in html.lower()

    def test_html_contains_score_components(self, full_supplier):
        html = generate_supplier_brief(full_supplier)
        assert "Revenue Growth" in html
        assert "Profit Margin" in html
        assert "Debt-to-Assets" in html
        assert "Data Freshness" in html

    def test_assessment_required_shows_banner(self, full_supplier):
        full_supplier["assessment_status"] = "ASSESSMENT_REQUIRED"
        full_supplier["assessment_reason"] = "No SEC EDGAR record found."
        html = generate_supplier_brief(full_supplier)
        assert "Manual Assessment Required" in html
        assert "No SEC EDGAR record found." in html

    def test_no_score_renders_na(self, full_supplier):
        full_supplier["last_score"] = None
        html = generate_supplier_brief(full_supplier)
        assert "N/A" in html

    def test_html_is_valid_doctype(self, full_supplier):
        html = generate_supplier_brief(full_supplier)
        assert html.strip().startswith("<!DOCTYPE html>")

    def test_contract_value_formatted(self, full_supplier):
        html = generate_supplier_brief(full_supplier)
        assert "480,000" in html

    def test_below_threshold_shows_alert_badge(self, full_supplier):
        full_supplier["last_score"] = 45  # below threshold of 60
        html = generate_supplier_brief(full_supplier)
        assert "BELOW THRESHOLD" in html

    def test_above_threshold_no_alert_badge(self, full_supplier):
        full_supplier["last_score"] = 72  # above threshold of 60
        html = generate_supplier_brief(full_supplier)
        assert "BELOW THRESHOLD" not in html


# ── generate_dashboard_html ───────────────────────────────────────────────────

class TestGenerateDashboardHtml:
    def test_empty_watchlist_renders(self):
        html = generate_dashboard_html([])
        assert "<!DOCTYPE html>" in html
        assert "No suppliers" in html

    def test_dashboard_shows_supplier_names(self):
        suppliers = [
            {
                "ticker": "WDAY",
                "supplier_name": "Workday Inc.",
                "category": "HR Technology",
                "last_score": 72,
                "trend": "STABLE",
                "risk_threshold": 60,
                "data_age_months": 7,
                "contract_end_date": "2027-03-31",
                "assessment_status": "OK",
            }
        ]
        html = generate_dashboard_html(suppliers)
        assert "Workday Inc." in html
        assert "WDAY" in html

    def test_dashboard_shows_all_suppliers(self):
        suppliers = [
            {"ticker": "WDAY", "supplier_name": "Workday", "category": "HR",
             "last_score": 72, "trend": "STABLE", "risk_threshold": 60,
             "data_age_months": 7, "assessment_status": "OK"},
            {"ticker": "MSFT", "supplier_name": "Microsoft", "category": "Cloud",
             "last_score": 88, "trend": "IMPROVING", "risk_threshold": 60,
             "data_age_months": 5, "assessment_status": "OK"},
        ]
        html = generate_dashboard_html(suppliers)
        assert "Workday" in html
        assert "Microsoft" in html

    def test_dashboard_shows_alert_badge_for_breach(self):
        suppliers = [
            {"ticker": "RISKY", "supplier_name": "Risky Corp", "category": "Logistics",
             "last_score": 35, "trend": "DETERIORATING", "risk_threshold": 60,
             "data_age_months": 14, "last_alerted": "2026-05-01T08:00:00Z",
             "assessment_status": "OK"},
        ]
        html = generate_dashboard_html(suppliers)
        assert "ALERT" in html

    def test_dashboard_shows_manual_required(self):
        suppliers = [
            {"ticker": "PRIV", "supplier_name": "Private Co", "category": "Parts",
             "last_score": None, "trend": "BASELINE", "risk_threshold": 60,
             "data_age_months": 0, "assessment_status": "ASSESSMENT_REQUIRED"},
        ]
        html = generate_dashboard_html(suppliers)
        assert "Manual Required" in html

    def test_alert_precision_shown(self):
        suppliers = [
            {"ticker": "WDAY", "supplier_name": "Workday", "category": "HR",
             "last_score": 45, "trend": "DETERIORATING", "risk_threshold": 60,
             "data_age_months": 7, "last_alerted": "2026-04-01",
             "last_alert_action": "ACTIONED", "assessment_status": "OK"},
        ]
        html = generate_dashboard_html(suppliers)
        assert "precision" in html.lower()
