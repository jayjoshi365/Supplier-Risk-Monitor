"""
EDGAR Fetcher — resolves ticker → CIK → XBRL facts → financial metrics.

SEC EDGAR is free, publicly mandated under the Securities Exchange Act.
No API key required. Rate limit: 10 req/sec (we stay well under at 0.15s delay).

Challenge solved: XBRL tag inconsistency.
Different companies use different concept names for identical line items.
We maintain a fallback tag hierarchy per financial concept, log which tag
was resolved, and flag when no recognized tag is found.
"""

import json
import logging
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

EDGAR_BASE = "https://data.sec.gov"
SEC_BASE = "https://www.sec.gov"
REQUEST_DELAY = 0.15  # Stay under SEC's 10 req/sec limit

# Fallback tag chains per financial concept — order matters (preferred first)
REVENUE_TAGS = [
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomer",
    "SalesRevenueNet",
    "SalesRevenueGoodsNet",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
]

NET_INCOME_TAGS = [
    "NetIncomeLoss",
    "ProfitLoss",
    "NetIncomeLossAvailableToCommonStockholdersBasic",
    "NetIncomeLossAvailableToCommonStockholdersDiluted",
]

ASSETS_TAGS = [
    "Assets",
]

LIABILITIES_TAGS = [
    "Liabilities",
]


def _fetch_json(url: str) -> Optional[dict]:
    """HTTP GET with SEC-compliant User-Agent. Returns None on 404."""
    headers = {
        "User-Agent": "SRIM procurement-risk-monitor contact@srim-tool.com",
        "Accept": "application/json",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            logger.warning(f"404 from EDGAR: {url}")
            return None
        raise
    except urllib.error.URLError as exc:
        logger.error(f"URL error fetching {url}: {exc}")
        raise


def _get_cik(ticker: str) -> Optional[str]:
    """
    Resolve ticker symbol to zero-padded 10-digit CIK.
    Uses SEC company_tickers.json — refreshed daily by SEC.
    Returns None if ticker not found (likely private company).
    """
    url = f"{SEC_BASE}/files/company_tickers.json"
    time.sleep(REQUEST_DELAY)
    data = _fetch_json(url)
    if not data:
        return None

    ticker_upper = ticker.upper()
    for entry in data.values():
        if entry.get("ticker", "").upper() == ticker_upper:
            cik = str(entry["cik_str"]).zfill(10)
            logger.info(f"Resolved {ticker} → CIK {cik}")
            return cik

    logger.warning(f"Ticker {ticker} not found in SEC company_tickers.json")
    return None


def _extract_annual_values(us_gaap: dict, tag_list: list, concept_name: str) -> list:
    """
    Extract annual 10-K values using the fallback tag hierarchy.
    Returns list of (period_end_date, value, tag_used, filed_date) tuples,
    sorted newest-first, deduplicated by fiscal year, max 3 entries.

    Tries ALL tags and returns whichever has the most recent data.
    This handles companies that switched XBRL tags (e.g. ASC 606 adoption in
    2019 moved many companies from Revenues → RevenueFromContractWith...).
    Without this, we'd return stale pre-switch data from the first tag found.

    Logs which tag resolved — audit trail for XBRL inconsistency problem.
    """
    best_results = []
    best_date = ""
    best_tag = None

    for tag in tag_list:
        if tag not in us_gaap:
            continue

        usd_values = us_gaap[tag].get("units", {}).get("USD", [])
        # Filter to annual 10-K full-year filings only
        annual = [
            v for v in usd_values
            if v.get("form") == "10-K" and v.get("fp") == "FY"
        ]
        if not annual:
            # Some companies file 10-K with fp != "FY" — try without fp filter
            annual = [v for v in usd_values if v.get("form") == "10-K"]

        if not annual:
            continue

        # Sort by period end date descending
        annual.sort(key=lambda x: x.get("end", ""), reverse=True)

        # Deduplicate: one entry per fiscal year (keep most recently filed)
        seen_years: set = set()
        deduplicated = []
        for v in annual:
            year = v.get("end", "")[:4]
            if year and year not in seen_years:
                seen_years.add(year)
                deduplicated.append(
                    (v["end"], v["val"], tag, v.get("filed", ""))
                )

        if not deduplicated:
            continue

        # Keep the tag whose most recent entry is newest
        most_recent = deduplicated[0][0]
        if most_recent > best_date:
            best_date = most_recent
            best_results = deduplicated[:3]
            best_tag = tag

    if best_results:
        logger.info(
            f"XBRL resolved: concept={concept_name} tag={best_tag} "
            f"most_recent={best_date} entries={len(best_results)}"
        )
        return best_results

    # No tag resolved
    logger.warning(
        f"XBRL miss: concept={concept_name} tried={tag_list} — "
        f"no matching tag found. Supplier may need manual assessment."
    )
    return []


def fetch_financial_data(ticker: str) -> dict:
    """
    Main entry point. Fetch and compute financial metrics for a ticker.

    Returns:
        {"status": "OK", "ticker": ..., "revenue_growth": ..., ...}
        {"status": "ASSESSMENT_REQUIRED", "ticker": ..., "reason": ...}

    Private company handling (Challenge 5 from spec):
        If EDGAR has no record for the ticker, return ASSESSMENT_REQUIRED.
        The system never fabricates a score — it flags for manual input.
    """
    # Step 1: Resolve ticker to CIK
    cik = _get_cik(ticker)
    if not cik:
        return {
            "status": "ASSESSMENT_REQUIRED",
            "ticker": ticker,
            "reason": (
                "No SEC EDGAR record found. This supplier may be private, "
                "foreign-listed, or use a different ticker symbol."
            ),
        }

    # Step 2: Fetch XBRL company facts
    time.sleep(REQUEST_DELAY)
    url = f"{EDGAR_BASE}/api/xbrl/companyfacts/CIK{cik}.json"
    facts = _fetch_json(url)
    if not facts:
        return {
            "status": "ASSESSMENT_REQUIRED",
            "ticker": ticker,
            "reason": "SEC EDGAR returned no XBRL facts for this CIK.",
        }

    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    if not us_gaap:
        return {
            "status": "ASSESSMENT_REQUIRED",
            "ticker": ticker,
            "reason": "No US-GAAP XBRL data found. Supplier may not file under US GAAP.",
        }

    # Step 3: Extract financial data using fallback tag chains
    revenue_data = _extract_annual_values(us_gaap, REVENUE_TAGS, "revenue")
    net_income_data = _extract_annual_values(us_gaap, NET_INCOME_TAGS, "net_income")
    assets_data = _extract_annual_values(us_gaap, ASSETS_TAGS, "assets")
    liabilities_data = _extract_annual_values(us_gaap, LIABILITIES_TAGS, "liabilities")

    # Require at minimum revenue and net income to score
    if not revenue_data or not net_income_data:
        return {
            "status": "ASSESSMENT_REQUIRED",
            "ticker": ticker,
            "reason": (
                "Insufficient XBRL data: could not resolve revenue or net income "
                "from any known XBRL tag. Manual assessment required."
            ),
        }

    # Step 4: Compute metrics from most recent annual data
    latest_rev_date, latest_rev, rev_tag, filing_date = revenue_data[0]
    _, latest_ni, ni_tag, _ = net_income_data[0]

    latest_assets = assets_data[0][1] if assets_data else None
    assets_tag = assets_data[0][2] if assets_data else None
    latest_liabilities = liabilities_data[0][1] if liabilities_data else None
    liab_tag = liabilities_data[0][2] if liabilities_data else None

    # Revenue growth: YoY (requires at least 2 annual data points)
    revenue_growth: Optional[float] = None
    if len(revenue_data) >= 2 and revenue_data[1][1] and revenue_data[1][1] != 0:
        prior_rev = revenue_data[1][1]
        revenue_growth = (latest_rev - prior_rev) / abs(prior_rev)

    # Profit margin
    profit_margin: Optional[float] = None
    if latest_rev and latest_rev != 0:
        profit_margin = latest_ni / latest_rev

    # Debt-to-assets
    debt_to_assets: Optional[float] = None
    if latest_assets and latest_liabilities is not None and latest_assets != 0:
        debt_to_assets = latest_liabilities / latest_assets

    # Data age in months from filing period end
    try:
        filing_end_dt = datetime.strptime(latest_rev_date, "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
        now = datetime.now(tz=timezone.utc)
        data_age_months = (now.year - filing_end_dt.year) * 12 + (
            now.month - filing_end_dt.month
        )
    except ValueError:
        data_age_months = 24  # Treat as stale if unparseable

    return {
        "status": "OK",
        "ticker": ticker,
        "cik": cik,
        "filing_period_end": latest_rev_date,
        "filing_date": filing_date,
        "data_age_months": data_age_months,
        "revenue": latest_rev,
        "revenue_growth": revenue_growth,
        "net_income": latest_ni,
        "profit_margin": profit_margin,
        "total_assets": latest_assets,
        "total_liabilities": latest_liabilities,
        "debt_to_assets": debt_to_assets,
        "tags_used": {
            "revenue": rev_tag,
            "net_income": ni_tag,
            "assets": assets_tag,
            "liabilities": liab_tag,
        },
    }
