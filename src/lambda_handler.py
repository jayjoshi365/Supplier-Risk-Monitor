"""
Lambda Handler — orchestrator and API Gateway router.

Two execution paths:

1. EventBridge scheduled event (daily at 08:00 UTC)
   → Process every supplier in the DynamoDB watchlist
   → Fetch EDGAR data → Score → Detect trend → Alert if needed → Upload brief

2. API Gateway HTTP event
   GET  /suppliers               → list all suppliers
   GET  /suppliers/{ticker}      → get one supplier
   GET  /suppliers/{ticker}/brief → HTML brief (inline, not S3 redirect)
   POST /suppliers               → add supplier to watchlist
   PUT  /suppliers/{ticker}/override    → set manual override
   PUT  /suppliers/{ticker}/alert-action → record ACTIONED/INVESTIGATING/FALSE_POSITIVE

Idempotency guarantee (Challenge 4):
   If the EDGAR filing hasn't changed since the last run (same filing_period_end),
   the function skips scoring and returns NO_NEW_FILING. Same input = same score,
   always. This is testable in the regression test suite.

Cold start note:
   This function runs on a daily schedule — Lambda cold start latency of
   500ms–2s has zero impact on user experience. Provisioned concurrency is
   unnecessary cost and complexity for this use case.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from alert_engine import send_alert, should_alert
from brief_generator import (
    generate_and_upload_dashboard,
    generate_supplier_brief,
    upload_brief,
)
from dynamo_writer import (
    get_all_suppliers,
    get_supplier,
    mark_alert_action,
    mark_assessment_required,
    put_supplier,
    set_override,
    update_last_alerted,
    update_score,
)
from edgar_fetcher import fetch_financial_data
from risk_scorer import score_financial_health
from trend_detector import detect_trend

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

VALID_ALERT_ACTIONS = {"ACTIONED", "INVESTIGATING", "FALSE_POSITIVE"}


# ── Entry point ───────────────────────────────────────────────────────────────

def lambda_handler(event: dict, context: Any) -> dict:
    """Route to scheduled handler or API Gateway handler based on event shape."""
    if "httpMethod" in event or "requestContext" in event:
        return _handle_api(event)
    return _handle_scheduled(event)


# ── Scheduled execution ───────────────────────────────────────────────────────

def _handle_scheduled(event: dict) -> dict:
    """
    Daily run: process all suppliers, regenerate dashboard.
    Each supplier is processed independently — one failure doesn't block others.
    """
    logger.info("Scheduled run started")
    suppliers = get_all_suppliers()
    logger.info(f"Processing {len(suppliers)} supplier(s)")

    results = []
    for supplier in suppliers:
        ticker = supplier.get("ticker", "UNKNOWN")
        try:
            result = _process_supplier(supplier)
            results.append({"ticker": ticker, **result})
        except Exception as exc:
            logger.error(f"Error processing {ticker}: {exc}", exc_info=True)
            results.append({"ticker": ticker, "status": "ERROR", "error": str(exc)})

    # Regenerate dashboard with fresh data
    updated = get_all_suppliers()
    dashboard_url = generate_and_upload_dashboard(updated)

    logger.info(f"Scheduled run complete. Processed: {len(results)}. Dashboard: {dashboard_url}")
    return {
        "status": "OK",
        "processed": len(results),
        "dashboard_url": dashboard_url,
        "results": results,
    }


def _process_supplier(supplier: dict) -> dict:
    """
    Full pipeline for one supplier:
      fetch → idempotency check → score → trend → write → brief → alert
    """
    ticker = supplier["ticker"]

    # 1. Fetch financial data
    financial_data = fetch_financial_data(ticker)

    if financial_data.get("status") == "ASSESSMENT_REQUIRED":
        mark_assessment_required(ticker, financial_data.get("reason", "Unknown reason"))
        return {"status": "ASSESSMENT_REQUIRED", "reason": financial_data.get("reason")}

    # 2. Idempotency: skip if same annual filing already processed
    last_filing = supplier.get("last_filing_period")
    new_filing = financial_data.get("filing_period_end")
    if last_filing and last_filing == new_filing:
        logger.info(f"{ticker}: filing unchanged ({new_filing}) — idempotency skip")
        return {"status": "NO_NEW_FILING", "filing": new_filing}

    # 3. Score
    scoring_result = score_financial_health(
        revenue_growth=financial_data.get("revenue_growth"),
        profit_margin=financial_data.get("profit_margin"),
        debt_to_assets=financial_data.get("debt_to_assets"),
        data_age_months=financial_data.get("data_age_months", 24),
    )
    current_score = scoring_result["total_score"]
    components = scoring_result["components"]

    # 4. Build score series for trend detection (new score prepended to history)
    existing_scores = [
        float(h.get("score", 0))
        for h in supplier.get("score_history", [])
    ]
    score_series = [float(current_score)] + existing_scores

    # 5. Detect trend
    trend = detect_trend(score_series)

    # 6. Persist score to DynamoDB
    update_score(ticker, current_score, trend, financial_data, components)

    # 7. Regenerate supplier brief on S3
    updated_supplier = get_supplier(ticker) or supplier
    brief_html = generate_supplier_brief(updated_supplier)
    upload_brief(ticker, brief_html)

    # 8. Alert decision
    alert_decision = should_alert(
        current_score=current_score,
        trend=trend,
        risk_threshold=float(supplier.get("risk_threshold", 60)),
        last_alerted=supplier.get("last_alerted"),
    )

    alerted = False
    alert_level = None
    if alert_decision.get("should_fire"):
        alert_level = alert_decision.get("level", "NORMAL")
        alerted = send_alert(
            supplier={**supplier, "last_filing_period": new_filing},
            current_score=current_score,
            trend=trend,
            components=components,
            level=alert_level,
        )
        if alerted:
            update_last_alerted(ticker, datetime.now(tz=timezone.utc).isoformat())

    return {
        "status": "OK",
        "score": current_score,
        "trend": trend,
        "filing": new_filing,
        "alerted": alerted,
        "alert_level": alert_level,
    }


# ── API Gateway routing ───────────────────────────────────────────────────────

def _handle_api(event: dict) -> dict:
    method = event.get("httpMethod", "GET").upper()
    path = event.get("path", "/").rstrip("/")
    path_params = event.get("pathParameters") or {}
    raw_body = event.get("body") or "{}"

    try:
        body = json.loads(raw_body)
    except (json.JSONDecodeError, TypeError):
        body = {}

    ticker = (path_params.get("ticker") or "").upper()
    segments = [s for s in path.split("/") if s]

    # Route matching
    try:
        if method == "GET" and segments == ["suppliers"]:
            return _api_list_suppliers()

        if method == "POST" and segments == ["suppliers"]:
            return _api_add_supplier(body)

        if method == "GET" and len(segments) == 2 and segments[0] == "suppliers":
            return _api_get_supplier(ticker or segments[1].upper())

        if method == "GET" and len(segments) == 3 and segments[2] == "brief":
            return _api_get_brief(ticker or segments[1].upper())

        if method == "PUT" and len(segments) == 3 and segments[2] == "override":
            return _api_set_override(ticker or segments[1].upper(), body)

        if method == "PUT" and len(segments) == 3 and segments[2] == "alert-action":
            return _api_alert_action(ticker or segments[1].upper(), body)

        return _json_response(404, {"error": "Endpoint not found", "path": path})

    except Exception as exc:
        logger.error(f"API error: {exc}", exc_info=True)
        return _json_response(500, {"error": "Internal server error"})


def _api_list_suppliers() -> dict:
    suppliers = get_all_suppliers()
    return _json_response(200, {"suppliers": suppliers, "count": len(suppliers)})


def _api_get_supplier(ticker: str) -> dict:
    supplier = get_supplier(ticker)
    if not supplier:
        return _json_response(404, {"error": f"Supplier '{ticker}' not found in watchlist"})
    return _json_response(200, supplier)


def _api_get_brief(ticker: str) -> dict:
    supplier = get_supplier(ticker)
    if not supplier:
        return _json_response(404, {"error": f"Supplier '{ticker}' not found in watchlist"})
    html = generate_supplier_brief(supplier)
    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "text/html",
            "Access-Control-Allow-Origin": "*",
        },
        "body": html,
    }


def _api_add_supplier(body: dict) -> dict:
    missing = [f for f in ("ticker", "supplier_name") if f not in body]
    if missing:
        return _json_response(400, {"error": f"Missing required fields: {missing}"})
    try:
        record = put_supplier(body)
        return _json_response(201, record)
    except Exception as exc:
        logger.error(f"Failed to add supplier: {exc}")
        return _json_response(500, {"error": str(exc)})


def _api_set_override(ticker: str, body: dict) -> dict:
    if not get_supplier(ticker):
        return _json_response(404, {"error": f"Supplier '{ticker}' not found"})
    override_status = body.get("override_status")
    if not override_status:
        return _json_response(400, {"error": "'override_status' is required"})
    override_score = body.get("override_score")
    set_override(ticker, override_status, override_score)
    return _json_response(200, {
        "ticker": ticker,
        "override_status": override_status,
        "override_score": override_score,
    })


def _api_alert_action(ticker: str, body: dict) -> dict:
    if not get_supplier(ticker):
        return _json_response(404, {"error": f"Supplier '{ticker}' not found"})
    action = body.get("action")
    if action not in VALID_ALERT_ACTIONS:
        return _json_response(
            400,
            {"error": f"'action' must be one of: {sorted(VALID_ALERT_ACTIONS)}"},
        )
    mark_alert_action(ticker, action)
    return _json_response(200, {"ticker": ticker, "action": action})


# ── Utilities ─────────────────────────────────────────────────────────────────

def _json_response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body),
    }
