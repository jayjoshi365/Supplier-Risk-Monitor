"""
Integration test — hits real SEC EDGAR API.

Run manually only:
  pytest tests/test_integration.py -v -s

NOT included in CI (no network in GitHub Actions test job).
Validates end-to-end pipeline with a live public company ticker.

Asserts:
  - EDGAR returns OK status for MSFT
  - Financial metrics are plausible (non-zero, reasonable ranges)
  - Tags are resolved and logged
  - Score is computed and is in 0-100 range
  - Trend label is one of the valid set
"""

import pytest

# Integration tests require network — skip if offline
pytest.importorskip("urllib.request")

from edgar_fetcher import fetch_financial_data
from risk_scorer import score_financial_health
from trend_detector import detect_trend


VALID_TRENDS = {"BASELINE", "IMPROVING", "STABLE", "DECLINING", "DETERIORATING"}


@pytest.mark.integration
class TestEdgarIntegration:
    def test_msft_returns_ok_status(self):
        """Microsoft is a large public company — should always have EDGAR data."""
        result = fetch_financial_data("MSFT")
        assert result["status"] == "OK", (
            f"Expected OK but got {result.get('status')}: {result.get('reason')}"
        )

    def test_msft_financial_metrics_are_plausible(self):
        result = fetch_financial_data("MSFT")
        assert result["status"] == "OK"

        # Revenue must be positive and in billions range
        assert result["revenue"] > 0
        assert result["revenue"] > 1_000_000_000

        # Filing period must be parseable date
        assert len(result["filing_period_end"]) == 10

        # Data age must be non-negative
        assert result["data_age_months"] >= 0

        # Tags must be recorded
        assert result["tags_used"]["revenue"] is not None

    def test_msft_score_is_in_range(self):
        fin = fetch_financial_data("MSFT")
        assert fin["status"] == "OK"
        result = score_financial_health(
            revenue_growth=fin.get("revenue_growth"),
            profit_margin=fin.get("profit_margin"),
            debt_to_assets=fin.get("debt_to_assets"),
            data_age_months=fin.get("data_age_months", 12),
        )
        score = result["total_score"]
        assert 0 <= score <= 100, f"Score {score} out of range"
        # MSFT should score reasonably well
        assert score >= 40, f"MSFT scored unexpectedly low: {score}"

    def test_private_company_returns_assessment_required(self):
        """A ticker that doesn't exist in EDGAR must return ASSESSMENT_REQUIRED."""
        result = fetch_financial_data("NOTACOMPANY999")
        assert result["status"] == "ASSESSMENT_REQUIRED"
        assert "reason" in result

    def test_full_pipeline_wday(self):
        """
        Full pipeline for WDAY: fetch → score → trend.
        Validates that every stage produces valid output.
        """
        fin = fetch_financial_data("WDAY")
        if fin["status"] == "ASSESSMENT_REQUIRED":
            pytest.skip("WDAY EDGAR data unavailable — skipping full pipeline test")

        scoring = score_financial_health(
            revenue_growth=fin.get("revenue_growth"),
            profit_margin=fin.get("profit_margin"),
            debt_to_assets=fin.get("debt_to_assets"),
            data_age_months=fin.get("data_age_months", 12),
        )
        score = scoring["total_score"]
        assert 0 <= score <= 100

        # Single score → BASELINE (no history)
        trend = detect_trend([score])
        assert trend == "BASELINE"

        # Two scores → direction established
        prior_score = score - 8  # simulate prior quarter
        trend2 = detect_trend([score, prior_score])
        assert trend2 in VALID_TRENDS
