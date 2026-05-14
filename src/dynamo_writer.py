"""
DynamoDB Writer — all persistence operations for the supplier watchlist.

Schema (partition key: ticker — string):
  ticker              String   — e.g. "WDAY"
  supplier_name       String
  category            String
  kraljic_position    String   — Strategic | Leverage | Bottleneck | Noncritical
  contract_value_usd  Number
  contract_end_date   String   — ISO date
  risk_threshold      Number   — alert fires below this score
  alert_email         String
  added_date          String   — ISO datetime
  score_history       List     — [{score, trend, timestamp, filing_period, components}, ...]
  last_alerted        String?  — ISO datetime of most recent alert
  override_status     String?  — manual override label
  override_score      Number?  — manual score when assessment_status=ASSESSMENT_REQUIRED
  last_score          Number?
  trend               String?
  last_run            String?  — ISO datetime of last Lambda execution
  last_filing_period  String?  — e.g. "2024-10-31"
  assessment_status   String?  — "OK" | "ASSESSMENT_REQUIRED"
  assessment_reason   String?
  last_alert_action   String?  — ACTIONED | INVESTIGATING | FALSE_POSITIVE
  cik                 String?
  data_age_months     Number?
  revenue_growth      Number?
  profit_margin       Number?
  debt_to_assets      Number?
  tags_used           Map?
"""

import json
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional

import boto3

logger = logging.getLogger(__name__)

TABLE_NAME = os.environ.get("DYNAMODB_TABLE", "srim-suppliers")
_TABLE = None


def _get_table():
    global _TABLE
    if _TABLE is None:
        _TABLE = boto3.resource("dynamodb").Table(TABLE_NAME)
    return _TABLE


def _float_to_decimal(val) -> Optional[Decimal]:
    if val is None:
        return None
    return Decimal(str(round(float(val), 8)))


def _decimal_to_float(obj):
    """JSON serializer that converts Decimal → float for API responses."""
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Not serializable: {type(obj)}")


def _to_json_safe(item: dict) -> dict:
    return json.loads(json.dumps(item, default=_decimal_to_float))


def _prepare_for_dynamo(obj):
    """Recursively convert floats to Decimal for DynamoDB compatibility."""
    if isinstance(obj, dict):
        return {k: _prepare_for_dynamo(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_prepare_for_dynamo(v) for v in obj]
    if isinstance(obj, float):
        return _float_to_decimal(obj)
    return obj


# ── Read operations ──────────────────────────────────────────────────────────

def get_all_suppliers() -> List[dict]:
    """Return all supplier records. Safe for watchlists up to ~1 MB."""
    table = _get_table()
    result = table.scan()
    return [_to_json_safe(item) for item in result.get("Items", [])]


def get_supplier(ticker: str) -> Optional[dict]:
    """Return a single supplier record, or None if not found."""
    table = _get_table()
    result = table.get_item(Key={"ticker": ticker.upper()})
    item = result.get("Item")
    return _to_json_safe(item) if item else None


# ── Write operations ─────────────────────────────────────────────────────────

def put_supplier(supplier: dict) -> dict:
    """
    Create or replace a supplier record.
    Normalises ticker to uppercase and sets default fields.
    """
    table = _get_table()
    record = dict(supplier)
    record["ticker"] = record.get("ticker", "").upper()
    record.setdefault("added_date", datetime.now(tz=timezone.utc).isoformat())
    record.setdefault("score_history", [])
    record.setdefault("last_alerted", None)
    record.setdefault("override_status", None)

    item = _prepare_for_dynamo(record)
    # DynamoDB doesn't accept None — remove null fields
    item = {k: v for k, v in item.items() if v is not None}
    table.put_item(Item=item)
    logger.info(f"Supplier upserted: {record['ticker']}")
    return _to_json_safe(record)


def update_score(
    ticker: str,
    score: float,
    trend: str,
    financial_data: dict,
    components: dict,
) -> None:
    """
    Persist a new score reading. Prepends to score_history (max 10 entries kept).
    Challenge 2: trend detection requires history — this is how we build it.
    """
    table = _get_table()
    now = datetime.now(tz=timezone.utc).isoformat()

    score_entry = _prepare_for_dynamo({
        "score": score,
        "trend": trend,
        "timestamp": now,
        "filing_period": financial_data.get("filing_period_end", ""),
        "components": {
            k: {
                "score": v.get("score"),
                "value": v.get("value"),
                "note": v.get("note", ""),
            }
            for k, v in components.items()
        },
    })

    # Fetch existing history and prepend new entry
    existing = get_supplier(ticker) or {}
    history = existing.get("score_history", [])
    history.insert(0, score_entry)
    history = history[:10]
    history_for_dynamo = _prepare_for_dynamo(history)

    table.update_item(
        Key={"ticker": ticker.upper()},
        UpdateExpression=(
            "SET last_score = :score, #tr = :trend, score_history = :history, "
            "last_run = :now, last_filing_period = :filing, "
            "revenue_growth = :rev, profit_margin = :margin, "
            "debt_to_assets = :dta, data_age_months = :age, "
            "tags_used = :tags, cik = :cik, assessment_status = :status"
        ),
        ExpressionAttributeNames={"#tr": "trend"},  # "trend" is a reserved word
        ExpressionAttributeValues={
            ":score": _float_to_decimal(score),
            ":trend": trend,
            ":history": history_for_dynamo,
            ":now": now,
            ":filing": financial_data.get("filing_period_end", ""),
            ":rev": _float_to_decimal(financial_data.get("revenue_growth")),
            ":margin": _float_to_decimal(financial_data.get("profit_margin")),
            ":dta": _float_to_decimal(financial_data.get("debt_to_assets")),
            ":age": financial_data.get("data_age_months", 0),
            ":tags": financial_data.get("tags_used") or {},
            ":cik": financial_data.get("cik", ""),
            ":status": "OK",
        },
    )
    logger.info(f"Score stored: ticker={ticker} score={score:.0f} trend={trend}")


def update_last_alerted(ticker: str, timestamp: str) -> None:
    """Record the timestamp of a sent alert (used for 30-day cooldown)."""
    _get_table().update_item(
        Key={"ticker": ticker.upper()},
        UpdateExpression="SET last_alerted = :ts",
        ExpressionAttributeValues={":ts": timestamp},
    )


def mark_assessment_required(ticker: str, reason: str) -> None:
    """Flag a supplier as requiring manual assessment (private company path)."""
    _get_table().update_item(
        Key={"ticker": ticker.upper()},
        UpdateExpression=(
            "SET assessment_status = :status, assessment_reason = :reason, last_run = :now"
        ),
        ExpressionAttributeValues={
            ":status": "ASSESSMENT_REQUIRED",
            ":reason": reason,
            ":now": datetime.now(tz=timezone.utc).isoformat(),
        },
    )
    logger.info(f"Supplier {ticker} flagged ASSESSMENT_REQUIRED: {reason}")


def set_override(
    ticker: str,
    override_status: str,
    override_score: Optional[float] = None,
) -> None:
    """Set a manual override (e.g. procurement team has assessed a private supplier)."""
    update_expr = "SET override_status = :status"
    expr_values: dict = {":status": override_status}

    if override_score is not None:
        update_expr += ", override_score = :score"
        expr_values[":score"] = _float_to_decimal(override_score)

    _get_table().update_item(
        Key={"ticker": ticker.upper()},
        UpdateExpression=update_expr,
        ExpressionAttributeValues=expr_values,
    )
    logger.info(f"Override set: ticker={ticker} status={override_status} score={override_score}")


def mark_alert_action(ticker: str, action: str) -> None:
    """
    Record the procurement team's response to an alert.
    Actions: ACTIONED | INVESTIGATING | FALSE_POSITIVE
    Used to compute alert precision metric shown in dashboard header.
    """
    _get_table().update_item(
        Key={"ticker": ticker.upper()},
        UpdateExpression="SET last_alert_action = :action",
        ExpressionAttributeValues={":action": action},
    )
    logger.info(f"Alert action recorded: ticker={ticker} action={action}")
