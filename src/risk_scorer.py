"""
Risk Scorer — deterministic financial health scoring.

Scoring is intentionally rule-based, not ML:
  - Dataset is one data point per quarter per supplier (too small for ML)
  - CFO must be able to understand why a supplier was flagged (explainability)
  - False positives have real operational consequences (auditability required)
  - Same filing = same score, always (idempotency guarantee)

Score breakdown (100 pts total):
  Revenue growth  30 pts
  Profit margin   35 pts
  Debt-to-assets  25 pts
  Data freshness  10 pts
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def score_financial_health(
    revenue_growth: Optional[float],
    profit_margin: Optional[float],
    debt_to_assets: Optional[float],
    data_age_months: int,
) -> dict:
    """
    Compute financial health score (0–100).

    Args:
        revenue_growth:   YoY revenue growth as decimal (0.10 = 10%). None if unavailable.
        profit_margin:    Net income / revenue. None if unavailable.
        debt_to_assets:   Total liabilities / total assets. None if unavailable.
        data_age_months:  Months since the filing period end date.

    Returns:
        {
          "total_score": int (0-100),
          "components": {
            "revenue_growth": {"score": int, "max": 30, "value": float|None, "note": str},
            "profit_margin":  {"score": int, "max": 35, "value": float|None, "note": str},
            "debt_to_assets": {"score": int, "max": 25, "value": float|None, "note": str},
            "data_freshness": {"score": int, "max": 10, "value": int,        "note": str},
          }
        }
    """
    score = 0
    components: dict = {}

    # ── Revenue growth (30 pts) ───────────────────────────────────────────────
    if revenue_growth is None:
        rev_score = 8
        rev_note = "Insufficient data (single year filing)"
    elif revenue_growth > 0.10:
        rev_score = 30
        rev_note = f"+{revenue_growth:.1%} (strong growth)"
    elif revenue_growth > 0.03:
        rev_score = 22
        rev_note = f"+{revenue_growth:.1%} (moderate growth)"
    elif revenue_growth > 0.0:
        rev_score = 15
        rev_note = f"+{revenue_growth:.1%} (marginal growth)"
    elif revenue_growth > -0.05:
        rev_score = 8
        rev_note = f"{revenue_growth:.1%} (slight decline)"
    else:
        rev_score = 2
        rev_note = f"{revenue_growth:.1%} (significant decline)"

    score += rev_score
    components["revenue_growth"] = {
        "score": rev_score,
        "max": 30,
        "value": revenue_growth,
        "note": rev_note,
    }

    # ── Profit margin (35 pts) ────────────────────────────────────────────────
    if profit_margin is None:
        margin_score = 10
        margin_note = "Insufficient data"
    elif profit_margin > 0.15:
        margin_score = 35
        margin_note = f"{profit_margin:.1%} (healthy)"
    elif profit_margin > 0.08:
        margin_score = 26
        margin_note = f"{profit_margin:.1%} (moderate)"
    elif profit_margin > 0.03:
        margin_score = 18
        margin_note = f"{profit_margin:.1%} (thin)"
    elif profit_margin > 0.0:
        margin_score = 10
        margin_note = f"{profit_margin:.1%} (marginal)"
    else:
        margin_score = 3
        margin_note = f"{profit_margin:.1%} (unprofitable)"

    score += margin_score
    components["profit_margin"] = {
        "score": margin_score,
        "max": 35,
        "value": profit_margin,
        "note": margin_note,
    }

    # ── Debt-to-assets (25 pts) ───────────────────────────────────────────────
    if debt_to_assets is None:
        debt_score = 10
        debt_note = "Insufficient data"
    elif debt_to_assets < 0.30:
        debt_score = 25
        debt_note = f"{debt_to_assets:.2f} (low leverage)"
    elif debt_to_assets < 0.50:
        debt_score = 18
        debt_note = f"{debt_to_assets:.2f} (moderate leverage)"
    elif debt_to_assets < 0.70:
        debt_score = 10
        debt_note = f"{debt_to_assets:.2f} (high leverage)"
    else:
        debt_score = 3
        debt_note = f"{debt_to_assets:.2f} (very high leverage)"

    score += debt_score
    components["debt_to_assets"] = {
        "score": debt_score,
        "max": 25,
        "value": debt_to_assets,
        "note": debt_note,
    }

    # ── Data freshness (10 pts) ───────────────────────────────────────────────
    # Staleness is itself a risk signal — treat it as a scoring penalty.
    if data_age_months <= 12:
        fresh_score = 10
        fresh_note = f"{data_age_months}mo (current)"
    elif data_age_months <= 18:
        fresh_score = 5
        fresh_note = f"{data_age_months}mo (aging)"
    else:
        fresh_score = 0
        fresh_note = f"{data_age_months}mo (stale — maximum penalty)"

    score += fresh_score
    components["data_freshness"] = {
        "score": fresh_score,
        "max": 10,
        "value": data_age_months,
        "note": fresh_note,
    }

    total = min(score, 100)
    logger.info(
        f"Score: {total}/100 "
        f"(rev={rev_score}/30 margin={margin_score}/35 "
        f"debt={debt_score}/25 fresh={fresh_score}/10)"
    )
    return {"total_score": total, "components": components}
