"""
Alert Engine — threshold + cooldown decision logic, SNS dispatch.

Alert quality design (Challenge 3):
  - Only DETERIORATING trend triggers an alert (not DECLINING — too early)
  - Score must be below supplier-specific risk_threshold
  - 30-day cooldown per supplier prevents alert storms
  - CRITICAL level bypasses cooldown when score drops 15+ pts below threshold
    (escalation path for procurement leadership)

Human-in-the-loop: alert emails contain EDGAR filing link and score breakdown.
The system never escalates, modifies contracts, or contacts suppliers directly.
"""

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

import boto3

logger = logging.getLogger(__name__)

SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN", "")
_SNS_CLIENT = None


def _get_sns():
    global _SNS_CLIENT
    if _SNS_CLIENT is None:
        _SNS_CLIENT = boto3.client("sns")
    return _SNS_CLIENT


def should_alert(
    current_score: float,
    trend: str,
    risk_threshold: float,
    last_alerted: Optional[str],
) -> dict:
    """
    Determine whether to fire an alert.

    Args:
        current_score:  Current financial health score (0–100).
        trend:          Trend label from trend_detector.detect_trend().
        risk_threshold: Supplier-specific alert threshold (e.g. 60).
        last_alerted:   ISO 8601 timestamp of last alert, or None.

    Returns:
        {
          "should_fire": bool,
          "level": "NORMAL" | "CRITICAL" (only present when should_fire=True),
          "reason": str,
        }
    """
    # Condition 1: only alert on confirmed sustained deterioration
    if trend != "DETERIORATING":
        return {
            "should_fire": False,
            "reason": f"Trend is {trend} — alert requires DETERIORATING trend",
        }

    # Condition 2: score must breach threshold
    if current_score >= risk_threshold:
        return {
            "should_fire": False,
            "reason": (
                f"Score {current_score:.0f} is at or above threshold {risk_threshold} "
                f"— no breach"
            ),
        }

    # Determine alert level
    critical_threshold = risk_threshold - 15
    is_critical = current_score < critical_threshold

    # Condition 3: cooldown (CRITICAL bypasses 30-day cooldown)
    if last_alerted and not is_critical:
        try:
            last_dt = datetime.fromisoformat(last_alerted.replace("Z", "+00:00"))
            days_since = (datetime.now(tz=timezone.utc) - last_dt).days
            if days_since < 30:
                return {
                    "should_fire": False,
                    "reason": (
                        f"Within 30-day cooldown ({days_since}d since last alert). "
                        f"Use CRITICAL escalation path to override."
                    ),
                }
        except (ValueError, TypeError) as exc:
            logger.warning(f"Could not parse last_alerted timestamp: {exc}")

    level = "CRITICAL" if is_critical else "NORMAL"
    return {
        "should_fire": True,
        "level": level,
        "reason": (
            f"Score {current_score:.0f} below threshold {risk_threshold} "
            f"with DETERIORATING trend — {level} alert"
        ),
    }


def send_alert(
    supplier: dict,
    current_score: float,
    trend: str,
    components: dict,
    level: str,
) -> bool:
    """
    Publish alert to SNS topic. Returns True on success, False on failure.

    The alert email contains:
      - Score and trend summary
      - Component breakdown (revenue, margin, debt, freshness)
      - Direct link to EDGAR filing
      - Explicit statement that human review is required before action
    """
    if not SNS_TOPIC_ARN:
        logger.warning("SNS_TOPIC_ARN not configured — alert suppressed")
        return False

    ticker = supplier.get("ticker", "UNKNOWN")
    name = supplier.get("supplier_name", ticker)
    threshold = supplier.get("risk_threshold", 60)
    filing = supplier.get("last_filing_period", "See EDGAR")
    category = supplier.get("category", "")
    kraljic = supplier.get("kraljic_position", "")

    rev = components.get("revenue_growth", {})
    margin = components.get("profit_margin", {})
    debt = components.get("debt_to_assets", {})
    fresh = components.get("data_freshness", {})

    subject = (
        f"[SRIM {level}] {name} ({ticker}) — "
        f"Risk Score {current_score:.0f} / Threshold {threshold:.0f}"
    )[:100]

    body = f"""
SUPPLIER RISK INTELLIGENCE MONITOR — {level} ALERT
{"=" * 64}

Supplier:       {name}
Ticker:         {ticker}
Category:       {category}
Position:       {kraljic}
Risk Score:     {current_score:.0f} / 100
Threshold:      {threshold:.0f}
Trend:          {trend}
Timestamp:      {datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}

SCORE BREAKDOWN
{"─" * 64}
Revenue Growth  {rev.get("score", "—"):>3}/30 pts  {rev.get("note", "")}
Profit Margin   {margin.get("score", "—"):>3}/35 pts  {margin.get("note", "")}
Debt-to-Assets  {debt.get("score", "—"):>3}/25 pts  {debt.get("note", "")}
Data Freshness  {fresh.get("score", "—"):>3}/10 pts  {fresh.get("note", "")}

DATA SOURCE
{"─" * 64}
Source:         SEC EDGAR / XBRL (10-K annual filing)
Filing period:  {filing}
EDGAR link:     https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ticker}&type=10-K

REQUIRED ACTION
{"─" * 64}
This signal requires procurement team review before any action.
SRIM does not make procurement decisions — it surfaces risk signals.

To record your review:
  Mark Reviewed:      PUT /suppliers/{ticker}/alert-action  {{"action": "ACTIONED"}}
  Mark Investigating: PUT /suppliers/{ticker}/alert-action  {{"action": "INVESTIGATING"}}
  Mark False Positive:PUT /suppliers/{ticker}/alert-action  {{"action": "FALSE_POSITIVE"}}

DISCLAIMER
{"─" * 64}
This tool is for procurement risk monitoring purposes only.
It is not investment advice.
SRIM does not perform sanctions screening — use a certified OFAC process
before any supplier action.
    """.strip()

    try:
        _get_sns().publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=subject,
            Message=body,
        )
        logger.info(f"Alert sent: ticker={ticker} level={level} score={current_score:.0f}")
        return True
    except Exception as exc:
        logger.error(f"SNS publish failed for {ticker}: {exc}", exc_info=True)
        return False
