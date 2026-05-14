"""
Brief Generator — produces HTML supplier briefs and the main dashboard.

Output:
  - Per-supplier brief:  s3://{bucket}/briefs/{ticker}.html
  - Main dashboard:      s3://{bucket}/index.html

Trust model (from spec §7):
  Every rendered page shows data source, filing period, data age with colour
  coding, and score components. No score is presented without the components
  that generated it. This is not optional — it is the mechanism by which
  procurement leadership can verify signals before acting.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional

import boto3

logger = logging.getLogger(__name__)

S3_BUCKET = os.environ.get("S3_BUCKET", "")
_S3 = None


def _get_s3():
    global _S3
    if _S3 is None:
        _S3 = boto3.client("s3")
    return _S3


# ── Colour helpers ────────────────────────────────────────────────────────────

def _risk_color(score: float) -> str:
    if score >= 70:
        return "#22c55e"   # green
    if score >= 50:
        return "#f59e0b"   # amber
    return "#ef4444"        # red


def _freshness_color(months: int) -> str:
    if months <= 12:
        return "#22c55e"
    if months <= 18:
        return "#f59e0b"
    return "#ef4444"


def _trend_icon(trend: str) -> str:
    return {
        "IMPROVING": "&#8593;",        # ↑
        "STABLE": "&#8594;",           # →
        "DECLINING": "&#8595;",        # ↓
        "DETERIORATING": "&#8595;&#8595;",  # ↓↓
        "BASELINE": "&mdash;",         # —
    }.get(trend, "?")


def _trend_badge_color(trend: str) -> str:
    return {
        "IMPROVING": "#dcfce7",
        "STABLE": "#f1f5f9",
        "DECLINING": "#fef3c7",
        "DETERIORATING": "#fee2e2",
        "BASELINE": "#f1f5f9",
    }.get(trend, "#f1f5f9")


# ── Supplier brief ────────────────────────────────────────────────────────────

def generate_supplier_brief(supplier: dict) -> str:
    """Generate a standalone HTML page for a single supplier."""
    ticker = supplier.get("ticker", "")
    name = supplier.get("supplier_name", ticker)
    score = supplier.get("last_score")
    trend = supplier.get("trend", "BASELINE")
    history = supplier.get("score_history", [])
    category = supplier.get("category", "—")
    kraljic = supplier.get("kraljic_position", "—")
    contract_value = supplier.get("contract_value_usd") or 0
    contract_end = supplier.get("contract_end_date", "—")
    threshold = supplier.get("risk_threshold", 60)
    filing = supplier.get("last_filing_period", "—")
    age = supplier.get("data_age_months") or 0
    cik = supplier.get("cik", "")
    assessment_status = supplier.get("assessment_status", "")
    tags = supplier.get("tags_used") or {}

    score_color = _risk_color(score) if score is not None else "#6b7280"
    freshness_color = _freshness_color(age)
    trend_color = _trend_badge_color(trend)
    score_display = f"{score:.0f}" if score is not None else "N/A"

    # Pull components from most recent score history entry
    components = history[0].get("components", {}) if history else {}
    rev = components.get("revenue_growth", {})
    margin = components.get("profit_margin", {})
    debt = components.get("debt_to_assets", {})
    fresh = components.get("data_freshness", {})

    # Build score history table rows
    history_rows = ""
    for h in history[:6]:
        ts = str(h.get("timestamp", ""))[:10]
        sc = h.get("score") or 0
        tr = h.get("trend", "—")
        fp = h.get("filing_period", "—")
        history_rows += (
            f"<tr>"
            f"<td>{ts}</td>"
            f"<td style='color:{_risk_color(float(sc))};font-weight:700'>{float(sc):.0f}</td>"
            f"<td>{tr} {_trend_icon(tr)}</td>"
            f"<td>{fp}</td>"
            f"</tr>"
        )
    if not history_rows:
        history_rows = "<tr><td colspan='4' class='empty'>No score history yet</td></tr>"

    # Assessment-required banner
    assessment_banner = ""
    if assessment_status == "ASSESSMENT_REQUIRED":
        reason = supplier.get("assessment_reason", "No SEC EDGAR data available.")
        assessment_banner = f"""
        <div class="banner-warning">
            <strong>Manual Assessment Required</strong><br>
            {reason}<br>
            Provide a manual score: <code>PUT /suppliers/{ticker}/override</code>
            with <code>{{"override_status": "MANUAL", "override_score": 55}}</code>
        </div>"""

    # XBRL tags provenance
    tags_rows = "".join(
        f"<tr><td>{concept}</td><td><code>{tag}</code></td></tr>"
        for concept, tag in tags.items()
        if tag
    )

    edgar_url = (
        f"https://www.sec.gov/cgi-bin/browse-edgar"
        f"?action=getcompany&CIK={cik or ticker}&type=10-K"
    )

    threshold_breach = (score is not None and score < threshold)
    alert_badge = (
        f"<span class='badge-alert'>BELOW THRESHOLD ({threshold:.0f})</span>"
        if threshold_breach else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SRIM &mdash; {name} ({ticker})</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         margin: 0; background: #f8fafc; color: #1e293b; font-size: 15px; }}
  .container {{ max-width: 860px; margin: 0 auto; padding: 24px; }}
  .header {{ background: #1e293b; color: #fff; padding: 28px 32px;
             border-radius: 12px; margin-bottom: 24px; }}
  .header h1 {{ margin: 0 0 6px; font-size: 26px; font-weight: 700; }}
  .header .meta {{ opacity: .65; font-size: 14px; }}
  .card {{ background: #fff; border-radius: 12px; padding: 24px;
           margin-bottom: 20px; box-shadow: 0 1px 4px rgba(0,0,0,.08); }}
  .card h2 {{ margin: 0 0 18px; font-size: 16px; color: #475569; text-transform: uppercase;
              letter-spacing: .05em; font-weight: 600; }}
  .score-big {{ font-size: 72px; font-weight: 800; line-height: 1; }}
  .score-row {{ display: flex; align-items: flex-start; gap: 32px; flex-wrap: wrap; }}
  .badge {{ display: inline-block; padding: 5px 14px; border-radius: 20px;
            font-size: 13px; font-weight: 600; }}
  .badge-alert {{ background: #fee2e2; color: #b91c1c; padding: 3px 10px;
                  border-radius: 12px; font-size: 12px; font-weight: 700; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th {{ text-align: left; padding: 9px 14px; background: #f8fafc;
        font-size: 12px; color: #64748b; text-transform: uppercase;
        letter-spacing: .05em; border-bottom: 2px solid #e2e8f0; }}
  td {{ padding: 10px 14px; border-bottom: 1px solid #f1f5f9; font-size: 14px; }}
  tr:last-child td {{ border-bottom: none; }}
  .empty {{ text-align: center; color: #94a3b8; padding: 28px; }}
  .banner-warning {{ background: #fef3c7; border: 1px solid #f59e0b; border-radius: 8px;
                     padding: 16px 20px; margin-bottom: 20px; font-size: 14px; }}
  .disclaimer {{ font-size: 12px; color: #94a3b8; margin-top: 24px;
                 background: #fff; border-radius: 8px; padding: 16px; line-height: 1.6; }}
  .back {{ font-size: 14px; color: #64748b; margin-bottom: 16px; display: block; }}
  a {{ color: #3b82f6; }}
  code {{ background: #f1f5f9; padding: 2px 6px; border-radius: 4px; font-size: 13px; }}
</style>
</head>
<body>
<div class="container">
  <a class="back" href="../index.html">&larr; Back to dashboard</a>

  <div class="header">
    <h1>{name}</h1>
    <div class="meta">
      {ticker} &nbsp;&bull;&nbsp; {category} &nbsp;&bull;&nbsp; {kraljic}
      &nbsp;&bull;&nbsp; Contract: ${contract_value:,.0f} &nbsp;&bull;&nbsp; Ends: {contract_end}
    </div>
  </div>

  {assessment_banner}

  <div class="card">
    <h2>Risk Score</h2>
    <div class="score-row">
      <div>
        <div class="score-big" style="color:{score_color}">{score_display}</div>
        <div style="font-size:13px;color:#64748b;margin-top:6px">
          out of 100 &nbsp;&bull;&nbsp; Threshold: {threshold:.0f}
          &nbsp;{alert_badge}
        </div>
      </div>
      <div>
        <div style="font-size:13px;color:#64748b;margin-bottom:8px">Trend</div>
        <div class="badge" style="background:{trend_color}">
          {trend} {_trend_icon(trend)}
        </div>
      </div>
    </div>
    <hr style="border:none;border-top:1px solid #f1f5f9;margin:22px 0">
    <table>
      <thead>
        <tr><th>Component</th><th>Score</th><th>Detail</th></tr>
      </thead>
      <tbody>
        <tr>
          <td>Revenue Growth <span style="color:#94a3b8">(max 30)</span></td>
          <td><strong>{rev.get("score", "—")}</strong></td>
          <td>{rev.get("note", "—")}</td>
        </tr>
        <tr>
          <td>Profit Margin <span style="color:#94a3b8">(max 35)</span></td>
          <td><strong>{margin.get("score", "—")}</strong></td>
          <td>{margin.get("note", "—")}</td>
        </tr>
        <tr>
          <td>Debt-to-Assets <span style="color:#94a3b8">(max 25)</span></td>
          <td><strong>{debt.get("score", "—")}</strong></td>
          <td>{debt.get("note", "—")}</td>
        </tr>
        <tr>
          <td>Data Freshness <span style="color:#94a3b8">(max 10)</span></td>
          <td><strong>{fresh.get("score", "—")}</strong></td>
          <td style="color:{freshness_color}">{fresh.get("note", "—")}</td>
        </tr>
      </tbody>
    </table>
  </div>

  <div class="card">
    <h2>Score History</h2>
    <table>
      <thead>
        <tr><th>Date</th><th>Score</th><th>Trend</th><th>Filing Period</th></tr>
      </thead>
      <tbody>{history_rows}</tbody>
    </table>
  </div>

  <div class="card">
    <h2>Data Source</h2>
    <table>
      <tbody>
        <tr><td>Source</td><td>SEC EDGAR / XBRL (10-K annual filing)</td></tr>
        <tr><td>Filing Period End</td><td>{filing}</td></tr>
        <tr>
          <td>Data Age</td>
          <td style="color:{freshness_color}">{age} months
            {"&mdash; stale, scoring penalised" if age > 18 else
             "&mdash; aging" if age > 12 else "&mdash; current"}
          </td>
        </tr>
        <tr>
          <td>EDGAR Filing</td>
          <td><a href="{edgar_url}" target="_blank" rel="noopener">View 10-K Filings &rarr;</a></td>
        </tr>
      </tbody>
    </table>
    {f"<hr style='border:none;border-top:1px solid #f1f5f9;margin:16px 0'><h2>XBRL Tags Resolved</h2><table><thead><tr><th>Concept</th><th>Tag Used</th></tr></thead><tbody>{tags_rows}</tbody></table>" if tags_rows else ""}
  </div>

  <div class="disclaimer">
    <strong>Disclaimer:</strong> This tool is for procurement risk monitoring purposes only.
    It is not investment advice. SRIM monitors public company financial data from SEC EDGAR.
    Private supplier risk signals are limited to user-provided manual assessments.
    Risk signals are one input to procurement judgment, not a substitute for it.
    SRIM does not perform sanctions screening &mdash; use a certified OFAC process before
    any supplier action.
    <br><br>
    Generated by SRIM &nbsp;&bull;&nbsp;
    {datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
  </div>
</div>
</body>
</html>"""


# ── Dashboard ─────────────────────────────────────────────────────────────────

def generate_dashboard_html(suppliers: list) -> str:
    """Generate the main watchlist dashboard HTML."""
    # Compute alert precision
    alert_actions = [s.get("last_alert_action") for s in suppliers if s.get("last_alerted")]
    false_positives = sum(1 for a in alert_actions if a == "FALSE_POSITIVE")
    total_actioned = len(alert_actions)
    if total_actioned > 0:
        precision = f"{100 - (false_positives / total_actioned * 100):.0f}%"
    else:
        precision = "N/A"

    at_risk = sum(
        1 for s in suppliers
        if s.get("last_score") is not None
        and s.get("last_score") < s.get("risk_threshold", 60)
    )
    assessment_needed = sum(
        1 for s in suppliers
        if s.get("assessment_status") == "ASSESSMENT_REQUIRED"
    )

    # Sort: lowest score first (highest risk at top)
    def sort_key(s):
        sc = s.get("last_score")
        if sc is None:
            return 999
        return sc

    rows = ""
    for s in sorted(suppliers, key=sort_key):
        ticker = s.get("ticker", "")
        name = s.get("supplier_name", ticker)
        score = s.get("last_score")
        trend = s.get("trend", "BASELINE")
        status = s.get("assessment_status", "OK")
        threshold = s.get("risk_threshold", 60)
        category = s.get("category", "—")
        age = s.get("data_age_months") or 0
        contract_end = s.get("contract_end_date", "—")
        filing = s.get("last_filing_period", "—")
        last_run = str(s.get("last_run", ""))[:10] or "—"

        if status == "ASSESSMENT_REQUIRED":
            score_cell = "<span style='color:#f59e0b;font-weight:600'>Manual Required</span>"
        elif score is None:
            score_cell = "<span style='color:#94a3b8'>Pending</span>"
        else:
            color = _risk_color(score)
            breach = score < threshold
            badge = (
                f" <span style='background:#fee2e2;color:#b91c1c;"
                f"padding:2px 8px;border-radius:10px;font-size:11px;"
                f"font-weight:700'>ALERT</span>"
                if breach else ""
            )
            score_cell = (
                f"<span style='color:{color};font-weight:700'>{score:.0f}</span>"
                f"{badge}"
            )

        trend_color = _trend_badge_color(trend)
        rows += f"""
        <tr>
          <td>
            <a href="briefs/{ticker.lower()}.html" style="font-weight:600;color:#1e293b;text-decoration:none">
              {name}
            </a><br>
            <span style="color:#94a3b8;font-size:12px">{ticker}</span>
          </td>
          <td>{category}</td>
          <td>{score_cell}</td>
          <td>
            <span class="badge" style="background:{trend_color};font-size:12px">
              {trend} {_trend_icon(trend)}
            </span>
          </td>
          <td style="color:{_freshness_color(age)}">{age}mo</td>
          <td>{contract_end}</td>
          <td>{last_run}</td>
          <td><a href="briefs/{ticker.lower()}.html" style="font-size:13px">View &rarr;</a></td>
        </tr>"""

    if not rows:
        rows = (
            "<tr><td colspan='8' style='text-align:center;color:#94a3b8;padding:48px'>"
            "No suppliers in watchlist. Add one: <code>POST /suppliers</code>"
            "</td></tr>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SRIM &mdash; Supplier Risk Intelligence Monitor</title>
<meta http-equiv="refresh" content="3600">
<style>
  *, *::before, *::after {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         margin: 0; background: #f8fafc; color: #1e293b; font-size: 15px; }}
  .container {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}
  .header {{ background: #1e293b; color: #fff; padding: 28px 32px;
             border-radius: 12px; margin-bottom: 24px; }}
  .header h1 {{ margin: 0 0 4px; font-size: 28px; font-weight: 700; }}
  .header .sub {{ opacity: .6; font-size: 14px; margin-bottom: 16px; }}
  .stat {{ display: inline-block; background: rgba(255,255,255,.1);
           padding: 8px 18px; border-radius: 8px; margin-right: 10px;
           font-size: 14px; font-weight: 500; }}
  .stat.red {{ background: rgba(239,68,68,.25); }}
  .stat.amber {{ background: rgba(245,158,11,.25); }}
  table {{ width: 100%; border-collapse: collapse; background: #fff;
           border-radius: 12px; overflow: hidden;
           box-shadow: 0 1px 4px rgba(0,0,0,.08); }}
  th {{ text-align: left; padding: 11px 16px; background: #f8fafc;
        font-size: 12px; color: #64748b; text-transform: uppercase;
        letter-spacing: .05em; border-bottom: 2px solid #e2e8f0; }}
  td {{ padding: 12px 16px; border-bottom: 1px solid #f1f5f9; vertical-align: middle; }}
  tr:hover {{ background: #fafafa; }}
  .badge {{ display: inline-block; padding: 4px 12px; border-radius: 20px;
            font-weight: 600; }}
  .disclaimer {{ font-size: 12px; color: #94a3b8; margin-top: 24px;
                 text-align: center; line-height: 1.6; }}
  code {{ background: #f1f5f9; padding: 2px 6px; border-radius: 4px; font-size: 13px; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>Supplier Risk Intelligence Monitor</h1>
    <div class="sub">Continuous financial health monitoring via SEC EDGAR / XBRL</div>
    <div>
      <span class="stat">{len(suppliers)} suppliers monitored</span>
      <span class="stat {'red' if at_risk > 0 else ''}">{at_risk} below threshold</span>
      <span class="stat {'amber' if assessment_needed > 0 else ''}">{assessment_needed} need manual assessment</span>
      <span class="stat">Alert precision: {precision}</span>
      <span class="stat">Updated: {datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}</span>
    </div>
  </div>

  <table>
    <thead>
      <tr>
        <th>Supplier</th>
        <th>Category</th>
        <th>Risk Score</th>
        <th>Trend</th>
        <th>Data Age</th>
        <th>Contract End</th>
        <th>Last Run</th>
        <th>Brief</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>

  <div class="disclaimer">
    SRIM &nbsp;&bull;&nbsp; Procurement risk monitoring via SEC EDGAR &nbsp;&bull;&nbsp;
    Not investment advice &nbsp;&bull;&nbsp; Not sanctions screening<br>
    Data source: SEC EDGAR / XBRL (publicly mandated under the Securities Exchange Act).
    Private company data is limited to manually-provided assessments.
  </div>
</div>
</body>
</html>"""


# ── S3 upload helpers ─────────────────────────────────────────────────────────

def upload_brief(ticker: str, html: str) -> Optional[str]:
    """Upload supplier brief HTML to S3. Returns S3 URL or None on failure."""
    if not S3_BUCKET:
        logger.warning("S3_BUCKET not set — brief not uploaded")
        return None
    key = f"briefs/{ticker.lower()}.html"
    try:
        _get_s3().put_object(
            Bucket=S3_BUCKET,
            Key=key,
            Body=html.encode("utf-8"),
            ContentType="text/html",
            CacheControl="max-age=3600",
        )
        logger.info(f"Brief uploaded: s3://{S3_BUCKET}/{key}")
        return f"https://{S3_BUCKET}.s3.amazonaws.com/{key}"
    except Exception as exc:
        logger.error(f"Failed to upload brief for {ticker}: {exc}")
        return None


def generate_and_upload_dashboard(suppliers: list) -> Optional[str]:
    """Generate and upload the main dashboard. Returns S3 URL or None."""
    if not S3_BUCKET:
        logger.warning("S3_BUCKET not set — dashboard not uploaded")
        return None
    html = generate_dashboard_html(suppliers)
    try:
        _get_s3().put_object(
            Bucket=S3_BUCKET,
            Key="index.html",
            Body=html.encode("utf-8"),
            ContentType="text/html",
            CacheControl="max-age=3600",
        )
        url = f"https://{S3_BUCKET}.s3.amazonaws.com/index.html"
        logger.info(f"Dashboard uploaded: {url}")
        return url
    except Exception as exc:
        logger.error(f"Failed to upload dashboard: {exc}")
        return None
