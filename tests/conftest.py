"""
Shared fixtures and helpers for SRIM test suite.
"""

import os
import sys

import pytest

# Ensure src/ is on the path so modules import without a package install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Stub out AWS environment variables before any module is imported
os.environ.setdefault("DYNAMODB_TABLE", "srim-suppliers-test")
os.environ.setdefault("SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123456789012:srim-test")
os.environ.setdefault("S3_BUCKET", "srim-dashboard-test")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")


@pytest.fixture
def sample_supplier():
    return {
        "ticker": "WDAY",
        "supplier_name": "Workday Inc.",
        "category": "HR Technology",
        "kraljic_position": "Strategic",
        "contract_value_usd": 480000,
        "contract_end_date": "2027-03-31",
        "risk_threshold": 60,
        "alert_email": "procurement@company.com",
        "added_date": "2026-05-14",
        "score_history": [],
        "last_alerted": None,
        "override_status": None,
    }


@pytest.fixture
def sample_financial_data():
    return {
        "status": "OK",
        "ticker": "WDAY",
        "cik": "0001285785",
        "filing_period_end": "2024-01-31",
        "filing_date": "2024-03-15",
        "data_age_months": 8,
        "revenue": 7_259_900_000,
        "revenue_growth": 0.165,          # 16.5% — strong
        "net_income": 1_382_100_000,
        "profit_margin": 0.190,           # 19% — healthy
        "total_assets": 16_000_000_000,
        "total_liabilities": 5_120_000_000,
        "debt_to_assets": 0.32,           # moderate
        "tags_used": {
            "revenue": "Revenues",
            "net_income": "NetIncomeLoss",
            "assets": "Assets",
            "liabilities": "Liabilities",
        },
    }
