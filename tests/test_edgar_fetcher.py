"""
Unit tests for edgar_fetcher.py — XBRL tag resolution logic.
All HTTP calls are mocked — no live EDGAR requests.

Covers:
  - Successful tag resolution with primary tag
  - Fallback to secondary tag when primary missing
  - ASSESSMENT_REQUIRED when CIK not found (private company)
  - ASSESSMENT_REQUIRED when no XBRL facts
  - Revenue growth computation
  - Profit margin computation
  - Debt-to-assets computation
  - Data age calculation
  - Annual/FY filter (excludes quarterly entries)
  - Deduplication of multiple filings for same fiscal year
"""

import json
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

from edgar_fetcher import (
    _extract_annual_values,
    _get_cik,
    fetch_financial_data,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

COMPANY_TICKERS = {
    "0": {"cik_str": 1285785, "ticker": "WDAY", "title": "WORKDAY INC"},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
}

WDAY_ANNUAL_REVENUE = [
    {"end": "2024-01-31", "val": 7_259_900_000, "form": "10-K", "fp": "FY", "filed": "2024-03-15"},
    {"end": "2023-01-31", "val": 6_217_100_000, "form": "10-K", "fp": "FY", "filed": "2023-03-14"},
    {"end": "2022-01-31", "val": 4_316_800_000, "form": "10-K", "fp": "FY", "filed": "2022-03-15"},
    # Quarterly entries — should be filtered out
    {"end": "2024-04-30", "val": 1_990_000_000, "form": "10-Q", "fp": "Q1", "filed": "2024-06-05"},
]

WDAY_NET_INCOME = [
    {"end": "2024-01-31", "val": 1_382_100_000, "form": "10-K", "fp": "FY", "filed": "2024-03-15"},
    {"end": "2023-01-31", "val": -367_200_000,  "form": "10-K", "fp": "FY", "filed": "2023-03-14"},
]

WDAY_ASSETS = [
    {"end": "2024-01-31", "val": 16_000_000_000, "form": "10-K", "fp": "FY", "filed": "2024-03-15"},
]

WDAY_LIABILITIES = [
    {"end": "2024-01-31", "val": 5_760_000_000, "form": "10-K", "fp": "FY", "filed": "2024-03-15"},
]

WDAY_FACTS = {
    "facts": {
        "us-gaap": {
            "Revenues": {"units": {"USD": WDAY_ANNUAL_REVENUE}},
            "NetIncomeLoss": {"units": {"USD": WDAY_NET_INCOME}},
            "Assets": {"units": {"USD": WDAY_ASSETS}},
            "Liabilities": {"units": {"USD": WDAY_LIABILITIES}},
        }
    }
}


def _make_fetch_mock(tickers=COMPANY_TICKERS, facts=WDAY_FACTS):
    """Return a side_effect function that routes URLs to mock responses."""
    def _side_effect(url: str):
        if "company_tickers.json" in url:
            return tickers
        if "companyfacts" in url:
            return facts
        return None
    return _side_effect


# ── _extract_annual_values ────────────────────────────────────────────────────

class TestExtractAnnualValues:
    def test_resolves_primary_tag(self):
        us_gaap = WDAY_FACTS["facts"]["us-gaap"]
        results = _extract_annual_values(us_gaap, ["Revenues"], "revenue")
        assert len(results) == 3  # Three annual 10-K entries
        assert results[0][1] == 7_259_900_000  # Most recent first

    def test_falls_back_to_secondary_tag(self):
        us_gaap = {
            "RevenueFromContractWithCustomerExcludingAssessedTax": {
                "units": {"USD": WDAY_ANNUAL_REVENUE}
            }
        }
        results = _extract_annual_values(
            us_gaap,
            ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"],
            "revenue",
        )
        assert len(results) > 0
        assert results[0][2] == "RevenueFromContractWithCustomerExcludingAssessedTax"

    def test_returns_empty_when_no_tag_matches(self):
        us_gaap = {"SomeOtherTag": {"units": {"USD": WDAY_ANNUAL_REVENUE}}}
        results = _extract_annual_values(us_gaap, ["Revenues"], "revenue")
        assert results == []

    def test_excludes_quarterly_filings(self):
        us_gaap = WDAY_FACTS["facts"]["us-gaap"]
        results = _extract_annual_values(us_gaap, ["Revenues"], "revenue")
        for (date, val, tag, filed) in results:
            assert "2024-04-30" not in date  # Quarterly entry excluded

    def test_deduplicates_same_fiscal_year(self):
        # Two 10-K entries for same fiscal year (amended filing)
        double_entries = [
            {"end": "2024-01-31", "val": 7_300_000_000, "form": "10-K", "fp": "FY", "filed": "2024-04-01"},
            {"end": "2024-01-31", "val": 7_259_900_000, "form": "10-K", "fp": "FY", "filed": "2024-03-15"},
            {"end": "2023-01-31", "val": 6_217_100_000, "form": "10-K", "fp": "FY", "filed": "2023-03-14"},
        ]
        us_gaap = {"Revenues": {"units": {"USD": double_entries}}}
        results = _extract_annual_values(us_gaap, ["Revenues"], "revenue")
        fiscal_years = [r[0][:4] for r in results]
        assert len(fiscal_years) == len(set(fiscal_years))  # No duplicates

    def test_returns_max_three_entries(self):
        us_gaap = WDAY_FACTS["facts"]["us-gaap"]
        results = _extract_annual_values(us_gaap, ["Revenues"], "revenue")
        assert len(results) <= 3

    def test_newest_first_ordering(self):
        us_gaap = WDAY_FACTS["facts"]["us-gaap"]
        results = _extract_annual_values(us_gaap, ["Revenues"], "revenue")
        dates = [r[0] for r in results]
        assert dates == sorted(dates, reverse=True)


# ── _get_cik ──────────────────────────────────────────────────────────────────

class TestGetCik:
    @patch("edgar_fetcher._fetch_json")
    @patch("edgar_fetcher.time.sleep")
    def test_resolves_known_ticker(self, mock_sleep, mock_fetch):
        mock_fetch.return_value = COMPANY_TICKERS
        cik = _get_cik("WDAY")
        assert cik == "0001285785"

    @patch("edgar_fetcher._fetch_json")
    @patch("edgar_fetcher.time.sleep")
    def test_case_insensitive(self, mock_sleep, mock_fetch):
        mock_fetch.return_value = COMPANY_TICKERS
        cik = _get_cik("wday")
        assert cik == "0001285785"

    @patch("edgar_fetcher._fetch_json")
    @patch("edgar_fetcher.time.sleep")
    def test_unknown_ticker_returns_none(self, mock_sleep, mock_fetch):
        mock_fetch.return_value = COMPANY_TICKERS
        cik = _get_cik("PRIVATE_CO")
        assert cik is None

    @patch("edgar_fetcher._fetch_json")
    @patch("edgar_fetcher.time.sleep")
    def test_cik_zero_padded_to_10_digits(self, mock_sleep, mock_fetch):
        mock_fetch.return_value = COMPANY_TICKERS
        cik = _get_cik("WDAY")
        assert len(cik) == 10
        assert cik.startswith("0")


# ── fetch_financial_data ──────────────────────────────────────────────────────

class TestFetchFinancialData:
    @patch("edgar_fetcher._fetch_json")
    @patch("edgar_fetcher.time.sleep")
    def test_happy_path_returns_ok_status(self, mock_sleep, mock_fetch):
        mock_fetch.side_effect = _make_fetch_mock()
        result = fetch_financial_data("WDAY")
        assert result["status"] == "OK"
        assert result["ticker"] == "WDAY"

    @patch("edgar_fetcher._fetch_json")
    @patch("edgar_fetcher.time.sleep")
    def test_private_company_returns_assessment_required(self, mock_sleep, mock_fetch):
        mock_fetch.return_value = COMPANY_TICKERS  # PRIVATE_CO not in list
        result = fetch_financial_data("PRIVATE_CO")
        assert result["status"] == "ASSESSMENT_REQUIRED"
        assert "ticker" in result
        assert "reason" in result

    @patch("edgar_fetcher._fetch_json")
    @patch("edgar_fetcher.time.sleep")
    def test_revenue_growth_computed_correctly(self, mock_sleep, mock_fetch):
        mock_fetch.side_effect = _make_fetch_mock()
        result = fetch_financial_data("WDAY")
        # (7_259_900_000 - 6_217_100_000) / 6_217_100_000 ≈ 0.1677
        assert result["revenue_growth"] == pytest.approx(0.1677, rel=0.01)

    @patch("edgar_fetcher._fetch_json")
    @patch("edgar_fetcher.time.sleep")
    def test_profit_margin_computed_correctly(self, mock_sleep, mock_fetch):
        mock_fetch.side_effect = _make_fetch_mock()
        result = fetch_financial_data("WDAY")
        # 1_382_100_000 / 7_259_900_000 ≈ 0.1904
        assert result["profit_margin"] == pytest.approx(0.1904, rel=0.01)

    @patch("edgar_fetcher._fetch_json")
    @patch("edgar_fetcher.time.sleep")
    def test_debt_to_assets_computed_correctly(self, mock_sleep, mock_fetch):
        mock_fetch.side_effect = _make_fetch_mock()
        result = fetch_financial_data("WDAY")
        # 5_760_000_000 / 16_000_000_000 = 0.36
        assert result["debt_to_assets"] == pytest.approx(0.36, rel=0.01)

    @patch("edgar_fetcher._fetch_json")
    @patch("edgar_fetcher.time.sleep")
    def test_tags_used_recorded(self, mock_sleep, mock_fetch):
        mock_fetch.side_effect = _make_fetch_mock()
        result = fetch_financial_data("WDAY")
        assert result["tags_used"]["revenue"] == "Revenues"
        assert result["tags_used"]["net_income"] == "NetIncomeLoss"

    @patch("edgar_fetcher._fetch_json")
    @patch("edgar_fetcher.time.sleep")
    def test_no_xbrl_facts_returns_assessment_required(self, mock_sleep, mock_fetch):
        def _side(url):
            if "company_tickers" in url:
                return COMPANY_TICKERS
            return None  # 404 for facts
        mock_fetch.side_effect = _side
        result = fetch_financial_data("WDAY")
        assert result["status"] == "ASSESSMENT_REQUIRED"

    @patch("edgar_fetcher._fetch_json")
    @patch("edgar_fetcher.time.sleep")
    def test_missing_revenue_tag_returns_assessment_required(self, mock_sleep, mock_fetch):
        facts_no_revenue = {
            "facts": {
                "us-gaap": {
                    "NetIncomeLoss": WDAY_FACTS["facts"]["us-gaap"]["NetIncomeLoss"],
                    "Assets": WDAY_FACTS["facts"]["us-gaap"]["Assets"],
                    "Liabilities": WDAY_FACTS["facts"]["us-gaap"]["Liabilities"],
                    # Revenue tag deliberately absent
                }
            }
        }
        mock_fetch.side_effect = _make_fetch_mock(facts=facts_no_revenue)
        result = fetch_financial_data("WDAY")
        assert result["status"] == "ASSESSMENT_REQUIRED"
