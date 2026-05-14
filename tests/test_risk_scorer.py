"""
Unit tests for risk_scorer.py — 100% deterministic, no AWS calls.

Covers:
  - All scoring bands for each component
  - None values (insufficient data paths)
  - Score ceiling at 100
  - Data freshness penalty bands
  - Component output structure
"""

import pytest

from risk_scorer import score_financial_health


# ── Helper ────────────────────────────────────────────────────────────────────

def _score(rev=None, margin=None, dta=None, age=6):
    """Convenience wrapper with sensible defaults."""
    return score_financial_health(
        revenue_growth=rev,
        profit_margin=margin,
        debt_to_assets=dta,
        data_age_months=age,
    )


# ── Revenue growth bands (30 pts) ─────────────────────────────────────────────

class TestRevenueGrowthScoring:
    def test_strong_growth_earns_30(self):
        result = _score(rev=0.15)
        assert result["components"]["revenue_growth"]["score"] == 30

    def test_moderate_growth_earns_22(self):
        result = _score(rev=0.05)
        assert result["components"]["revenue_growth"]["score"] == 22

    def test_marginal_growth_earns_15(self):
        result = _score(rev=0.01)
        assert result["components"]["revenue_growth"]["score"] == 15

    def test_slight_decline_earns_8(self):
        result = _score(rev=-0.03)
        assert result["components"]["revenue_growth"]["score"] == 8

    def test_significant_decline_earns_2(self):
        result = _score(rev=-0.10)
        assert result["components"]["revenue_growth"]["score"] == 2

    def test_none_revenue_returns_partial_score(self):
        # Insufficient data — system doesn't fabricate; gives moderate penalty
        result = _score(rev=None)
        assert result["components"]["revenue_growth"]["score"] == 8
        assert "Insufficient" in result["components"]["revenue_growth"]["note"]

    def test_boundary_exactly_10_percent(self):
        # 0.10 is NOT > 0.10, so should be 22
        result = _score(rev=0.10)
        assert result["components"]["revenue_growth"]["score"] == 22

    def test_boundary_exactly_zero(self):
        # 0.0 is NOT > 0.0, so falls into slight decline band
        result = _score(rev=0.0)
        assert result["components"]["revenue_growth"]["score"] == 8


# ── Profit margin bands (35 pts) ─────────────────────────────────────────────

class TestProfitMarginScoring:
    def test_healthy_margin_earns_35(self):
        result = _score(margin=0.20)
        assert result["components"]["profit_margin"]["score"] == 35

    def test_moderate_margin_earns_26(self):
        result = _score(margin=0.10)
        assert result["components"]["profit_margin"]["score"] == 26

    def test_thin_margin_earns_18(self):
        result = _score(margin=0.05)
        assert result["components"]["profit_margin"]["score"] == 18

    def test_marginal_profit_earns_10(self):
        result = _score(margin=0.01)
        assert result["components"]["profit_margin"]["score"] == 10

    def test_unprofitable_earns_3(self):
        result = _score(margin=-0.05)
        assert result["components"]["profit_margin"]["score"] == 3

    def test_none_margin_returns_partial_score(self):
        result = _score(margin=None)
        assert result["components"]["profit_margin"]["score"] == 10


# ── Debt-to-assets bands (25 pts) ────────────────────────────────────────────

class TestDebtToAssetsScoring:
    def test_low_leverage_earns_25(self):
        result = _score(dta=0.20)
        assert result["components"]["debt_to_assets"]["score"] == 25

    def test_moderate_leverage_earns_18(self):
        result = _score(dta=0.40)
        assert result["components"]["debt_to_assets"]["score"] == 18

    def test_high_leverage_earns_10(self):
        result = _score(dta=0.60)
        assert result["components"]["debt_to_assets"]["score"] == 10

    def test_very_high_leverage_earns_3(self):
        result = _score(dta=0.85)
        assert result["components"]["debt_to_assets"]["score"] == 3

    def test_none_dta_returns_partial_score(self):
        result = _score(dta=None)
        assert result["components"]["debt_to_assets"]["score"] == 10

    def test_boundary_exactly_30_percent(self):
        # 0.30 is NOT < 0.30, so moderate band
        result = _score(dta=0.30)
        assert result["components"]["debt_to_assets"]["score"] == 18


# ── Data freshness penalty (10 pts) ──────────────────────────────────────────

class TestDataFreshnessScoring:
    def test_current_data_earns_10(self):
        result = _score(age=6)
        assert result["components"]["data_freshness"]["score"] == 10

    def test_12_month_boundary_earns_10(self):
        result = _score(age=12)
        assert result["components"]["data_freshness"]["score"] == 10

    def test_aging_data_earns_5(self):
        result = _score(age=15)
        assert result["components"]["data_freshness"]["score"] == 5

    def test_stale_data_earns_0(self):
        result = _score(age=24)
        assert result["components"]["data_freshness"]["score"] == 0

    def test_19_month_earns_0(self):
        result = _score(age=19)
        assert result["components"]["data_freshness"]["score"] == 0


# ── Total score and structure ─────────────────────────────────────────────────

class TestScoreTotal:
    def test_perfect_score_is_100(self):
        result = score_financial_health(
            revenue_growth=0.20,   # 30 pts
            profit_margin=0.20,    # 35 pts
            debt_to_assets=0.10,   # 25 pts
            data_age_months=6,     # 10 pts
        )
        assert result["total_score"] == 100

    def test_worst_case_score_is_low(self):
        result = score_financial_health(
            revenue_growth=-0.20,  # 2 pts
            profit_margin=-0.10,   # 3 pts
            debt_to_assets=0.90,   # 3 pts
            data_age_months=24,    # 0 pts
        )
        assert result["total_score"] == 8

    def test_score_cannot_exceed_100(self):
        # Even if all inputs are extreme, score must cap at 100
        result = score_financial_health(
            revenue_growth=1.0,
            profit_margin=1.0,
            debt_to_assets=0.0,
            data_age_months=0,
        )
        assert result["total_score"] <= 100

    def test_all_none_returns_valid_score(self):
        result = score_financial_health(
            revenue_growth=None,
            profit_margin=None,
            debt_to_assets=None,
            data_age_months=6,
        )
        assert "total_score" in result
        assert 0 <= result["total_score"] <= 100

    def test_components_have_required_keys(self):
        result = _score(rev=0.05, margin=0.10, dta=0.40, age=8)
        for component in ("revenue_growth", "profit_margin", "debt_to_assets", "data_freshness"):
            assert component in result["components"]
            comp = result["components"][component]
            assert "score" in comp
            assert "max" in comp
            assert "note" in comp

    def test_component_max_values_correct(self):
        result = _score()
        assert result["components"]["revenue_growth"]["max"] == 30
        assert result["components"]["profit_margin"]["max"] == 35
        assert result["components"]["debt_to_assets"]["max"] == 25
        assert result["components"]["data_freshness"]["max"] == 10

    def test_idempotency_same_inputs_same_score(self):
        """Same inputs must always produce same score — regression guard."""
        kwargs = dict(
            revenue_growth=0.085,
            profit_margin=0.112,
            debt_to_assets=0.445,
            data_age_months=7,
        )
        first = score_financial_health(**kwargs)["total_score"]
        second = score_financial_health(**kwargs)["total_score"]
        assert first == second
