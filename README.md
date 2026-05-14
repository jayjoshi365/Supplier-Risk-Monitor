# Supplier Risk Intelligence Monitor (SRIM)

Post-award supplier financial health monitoring via SEC EDGAR/XBRL.  
Continuous risk scoring, trend detection, and SNS alerting for strategic supplier watchlists.

---

## The Problem

ProcureIQ answers *"Which supplier should we choose?"* at the point of award.  
No system then asks *"Is that supplier still safe to rely on?"*

The signals that predict instability — declining revenue, rising debt-to-assets ratio, stale regulatory filings — are publicly available in SEC EDGAR for every public company, refreshed quarterly. Procurement teams do not systematically monitor them between contracts.

**SRIM's thesis:** The same financial health logic that informs the pre-award decision should run continuously against the post-award supplier base. When a strategic supplier's financial profile deteriorates, procurement leadership needs to know *before* operations feels it.

---

## Architecture

```
┌─────────────────────────────────────┐
│       EventBridge Scheduler         │
│     (daily at 08:00 UTC)            │
└────────────────┬────────────────────┘
                 │
          ┌──────▼──────┐
          │   Lambda    │  ◄── API Gateway (on-demand)
          │ Orchestrator│
          └──────┬──────┘
     ┌───────────┼────────────┐
     ▼           ▼            ▼
┌─────────┐ ┌────────┐ ┌──────────────┐
│  EDGAR  │ │  Risk  │ │    Trend     │
│ Fetcher │→│ Scorer │→│  Detector    │
└─────────┘ └────────┘ └──────┬───────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
       ┌──────────┐     ┌──────────┐    ┌──────────┐
       │ DynamoDB │     │  Alert   │    │  Brief   │
       │  Writer  │     │  Engine  │    │Generator │
       └──────────┘     └────┬─────┘    └────┬─────┘
                             │               │
                        ┌────▼────┐    ┌─────▼────┐
                        │   SNS   │    │    S3    │
                        │  Email  │    │Dashboard │
                        └─────────┘    └──────────┘
```

**AWS services used — $0/month on Free Tier:**

| Service | Role | Free Tier Limit | SRIM Usage |
|---|---|---|---|
| Lambda | Orchestrator + API | 1M req/month, 400K GB-sec | ~30 invocations/month |
| DynamoDB | Supplier watchlist + score history | 25 GB, 25 WCU/RCU | < 1 MB |
| EventBridge | Daily schedule trigger | 14M events/month | 30 events/month |
| SNS | Alert emails | 1M publishes/month | < 20/month |
| S3 | Static HTML dashboard + briefs | 5 GB, 20K GET | < 10 MB |
| CloudWatch | Logs + error alarms | 10 metrics, 5 GB logs | < 100 MB |

---

## Data Sources

**Primary — SEC EDGAR / XBRL (free, no API key, publicly mandated):**

| Signal | Primary XBRL Tag | Fallback |
|---|---|---|
| Revenue | `Revenues` | `RevenueFromContractWithCustomerExcludingAssessedTax` |
| Net income | `NetIncomeLoss` | `ProfitLoss` |
| Total assets | `Assets` | — |
| Total liabilities | `Liabilities` | — |

EDGAR is legally the cleanest data source available: publicly mandated under the Securities Exchange Act, refreshed quarterly, no license required, independently verifiable by anyone.

**Deliberately excluded:**
- Vendor-submitted financial data — not independently verifiable
- Credit rating scores — paid, not auditable in this context
- News sentiment — poor signal-to-noise without domain-specific NLP fine-tuning
- Stock price — correlated to market sentiment, not operational financial health

---

## Scoring Model

Deterministic rule-based scoring (0–100). Not ML — see [Design Decisions](#design-decisions).

```
Score = Revenue Growth (30 pts)
      + Profit Margin  (35 pts)
      + Debt-to-Assets (25 pts)
      + Data Freshness (10 pts)
```

| Component | Range | Score |
|---|---|---|
| Revenue growth | > 10% | 30 |
| | 3–10% | 22 |
| | 0–3% | 15 |
| | –5–0% | 8 |
| | < –5% | 2 |
| Profit margin | > 15% | 35 |
| | 8–15% | 26 |
| | 3–8% | 18 |
| | 0–3% | 10 |
| | < 0% | 3 |
| Debt-to-assets | < 0.30 | 25 |
| | 0.30–0.50 | 18 |
| | 0.50–0.70 | 10 |
| | > 0.70 | 3 |
| Data freshness | ≤ 12 months | 10 |
| | 13–18 months | 5 |
| | > 18 months | 0 |

**Trend detection** — rule-based, two-quarter confirmation required:

| Label | Condition |
|---|---|
| `BASELINE` | Fewer than 2 scored quarters |
| `IMPROVING` | Current score ≥ previous + 5 |
| `STABLE` | Within ±5 of previous score |
| `DECLINING` | Current score < previous – 5 (single quarter) |
| `DETERIORATING` | Two consecutive declines of > 5 pts each |

**Alert fires when:** `trend == DETERIORATING` AND `score < supplier.risk_threshold`  
**Suppressed if:** last alert was within 30 days (cooldown)  
**CRITICAL escalation:** if `score < threshold – 15`, bypasses cooldown  

---

## API Reference

Base URL: `https://{api-id}.execute-api.us-east-1.amazonaws.com/prod`

### Endpoints

**`GET /suppliers`** — list all suppliers in watchlist

```bash
curl https://{base}/suppliers
```

```json
{
  "suppliers": [...],
  "count": 3
}
```

---

**`POST /suppliers`** — add a supplier to the watchlist

```bash
curl -X POST https://{base}/suppliers \
  -H "Content-Type: application/json" \
  -d '{
    "ticker": "WDAY",
    "supplier_name": "Workday Inc.",
    "category": "HR Technology",
    "kraljic_position": "Strategic",
    "contract_value_usd": 480000,
    "contract_end_date": "2027-03-31",
    "risk_threshold": 60,
    "alert_email": "procurement@company.com"
  }'
```

Required fields: `ticker`, `supplier_name`  
`risk_threshold` guidance: 70 for strategic sole-source, 45 for dual-sourced commodity

---

**`GET /suppliers/{ticker}`** — get one supplier record with full score history

```bash
curl https://{base}/suppliers/WDAY
```

---

**`GET /suppliers/{ticker}/brief`** — HTML supplier brief (renders in browser)

```bash
open https://{base}/suppliers/WDAY/brief
```

---

**`PUT /suppliers/{ticker}/override`** — set manual score for private suppliers

```bash
curl -X PUT https://{base}/suppliers/PRIV/override \
  -H "Content-Type: application/json" \
  -d '{"override_status": "MANUAL", "override_score": 55}'
```

---

**`PUT /suppliers/{ticker}/alert-action`** — record procurement team response to alert

```bash
curl -X PUT https://{base}/suppliers/WDAY/alert-action \
  -H "Content-Type: application/json" \
  -d '{"action": "FALSE_POSITIVE"}'
```

Valid actions: `ACTIONED` · `INVESTIGATING` · `FALSE_POSITIVE`  
Used to compute alert precision metric displayed in dashboard header.

---

## Watchlist Record Schema

```json
{
  "ticker": "WDAY",
  "supplier_name": "Workday Inc.",
  "category": "HR Technology",
  "kraljic_position": "Strategic",
  "contract_value_usd": 480000,
  "contract_end_date": "2027-03-31",
  "risk_threshold": 60,
  "alert_email": "procurement@company.com",
  "added_date": "2026-05-14",
  "last_score": 74,
  "trend": "STABLE",
  "score_history": [
    {
      "score": 74,
      "trend": "STABLE",
      "timestamp": "2026-05-14T08:00:00+00:00",
      "filing_period": "2024-01-31",
      "components": {
        "revenue_growth": {"score": 30, "note": "+16.8%"},
        "profit_margin":  {"score": 26, "note": "9.2%"},
        "debt_to_assets": {"score": 18, "note": "0.36"},
        "data_freshness": {"score": 10, "note": "8mo"}
      }
    }
  ],
  "last_alerted": null,
  "last_filing_period": "2024-01-31",
  "data_age_months": 8,
  "assessment_status": "OK",
  "tags_used": {
    "revenue": "Revenues",
    "net_income": "NetIncomeLoss",
    "assets": "Assets",
    "liabilities": "Liabilities"
  }
}
```

---

## Deployment

### Prerequisites

- AWS account (Free Tier) with `srim-dev` IAM user  
  Policies: `AWSLambdaFullAccess`, `AmazonDynamoDBFullAccess`, `AmazonS3FullAccess`, `AmazonSNSFullAccess`, `AmazonEventBridgeFullAccess`, `CloudWatchFullAccess`
- AWS CLI and SAM CLI installed
- Python 3.12

### First-time setup (Codespaces)

```bash
# Install AWS CLI
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip && sudo ./aws/install

# Install SAM CLI
pip install aws-sam-cli

# Configure credentials (use Codespaces secrets for CI/CD)
aws configure
# Prompt: access key ID, secret access key, region (us-east-1), output (json)

# Verify connection
aws sts get-caller-identity
```

### Deploy

```bash
make install   # install dev dependencies
make test      # 122 unit tests, ~0.3s, no AWS calls
make build     # sam build
make deploy    # sam deploy --guided (interactive first run)
```

`sam deploy --guided` will ask:
- Stack name: `srim-prod`
- Region: `us-east-1`
- `AlertEmail` parameter: your email (will receive SNS subscription confirmation)

After deploy, CloudFormation Outputs gives you:
- `ApiUrl` — REST API base URL
- `DashboardUrl` — S3 static dashboard URL

### CI/CD (GitHub Actions)

Add to GitHub → Settings → Secrets → Actions:
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`

Every push to `main`: unit tests → SAM build → SAM deploy.

---

## Running Locally

```bash
# Run unit tests (no AWS, no network)
make test

# Run integration tests (hits real SEC EDGAR — requires network)
make test-integration

# Invoke Lambda locally with scheduled event (requires: sam build + AWS creds for DynamoDB)
make invoke-scheduled

# Tail live Lambda logs
make logs
```

### Manual pipeline trigger

```bash
# Add a supplier
curl -X POST https://{base}/suppliers \
  -H "Content-Type: application/json" \
  -d '{"ticker":"WDAY","supplier_name":"Workday Inc.","risk_threshold":60}'

# Trigger the daily pipeline immediately (no need to wait for 8 AM UTC)
aws lambda invoke \
  --function-name srim-prod \
  --payload file://events/scheduled.json \
  --cli-binary-format raw-in-base64-out \
  output.json && cat output.json
```

---

## Test Coverage

122 unit tests. All pass without network or AWS credentials.

| Test file | Coverage |
|---|---|
| `test_risk_scorer.py` | All scoring bands, boundary conditions, None inputs, score ceiling, idempotency guarantee |
| `test_trend_detector.py` | All 5 trend labels, exact delta boundaries, float scores, DETERIORATING two-decline requirement |
| `test_alert_engine.py` | Cooldown logic, CRITICAL bypass, threshold variants, malformed timestamp handling |
| `test_edgar_fetcher.py` | Tag resolution, fallback chain, private company detection, metric math validation |
| `test_brief_generator.py` | HTML content, color thresholds, assessment banner, dashboard with multiple suppliers |
| `test_integration.py` | Live EDGAR call for MSFT and WDAY — run manually, excluded from CI |

```bash
pytest tests/ --ignore=tests/test_integration.py -q
# 122 passed in 0.31s
```

---

## Design Decisions

### Why deterministic rules, not ML?

- **Dataset size:** one data point per quarter per supplier — too small for statistical learning
- **Explainability:** the CFO must understand why a supplier was flagged; "two consecutive 5-point drops in revenue margin" is auditable, a model weight is not
- **False positive cost:** a bad signal triggers procurement review, relationship scrutiny, and potentially contract renegotiation — the cost of errors is high
- **Idempotency:** same EDGAR filing must always produce the same score; ML models introduce non-determinism via version drift

### Why Lambda over ECS?

This is a daily scheduled monitor, not a real-time API. Lambda cold start latency of 500ms–2s has zero impact on user experience. ECS would add container management complexity and cost with no benefit for this access pattern.

### Why DynamoDB over RDS?

The access pattern is single-key lookup by ticker and full-table scan for the daily run. No joins, no transactions, no relational constraints. DynamoDB's Free Tier (25 GB, 25 WCU/RCU) comfortably covers the entire workload. RDS Free Tier expires after 12 months.

### Why SNS over SES for alerting?

SNS subscriptions are managed outside the code — procurement leads subscribe themselves, no email infrastructure to maintain. Adding a new alert recipient is a console operation, not a deployment.

### Why EDGAR over commercial credit scores?

Credit rating scores are paid, opaque, and not independently verifiable in a portfolio context. EDGAR data is free, legally mandated, publicly auditable, and sourced directly from the company's accountants — not from a third-party intermediary.

### Private company handling

Many strategic suppliers are private — no SEC EDGAR data exists. SRIM detects this case (EDGAR returns no CIK for ticker), flags the supplier as `ASSESSMENT_REQUIRED`, and surfaces a prompt for manual score input via `PUT /suppliers/{ticker}/override`. The system never fabricates a score. Honesty about data limitations is part of the trust model.

---

## Human-in-the-Loop Design

Three explicit human touchpoints — designed, not accidental:

**1. Watchlist curation (human owns the input)**  
The system monitors only suppliers a procurement professional explicitly adds. No auto-discovery.

**2. Alert review before any action**  
Every alert email contains: score breakdown, component scores, EDGAR filing link, and three response options (`ACTIONED` / `INVESTIGATING` / `FALSE_POSITIVE`). The system never escalates, modifies contracts, or contacts suppliers directly.

**3. Threshold configuration per supplier**  
Strategic sole-source: `risk_threshold: 70` (earlier warning)  
Dual-sourced commodity: `risk_threshold: 45` (higher tolerance)  
The procurement team sets thresholds — not a system default.

**What SRIM never does autonomously:** removes suppliers from approved vendor lists, triggers contract modifications, makes procurement recommendations, or communicates externally with suppliers.

---

## Failure Modes

| Failure | Detection | Mitigation |
|---|---|---|
| EDGAR API timeout | CloudWatch Lambda error rate | Exponential backoff, 3 retries, 1s base delay |
| Missing XBRL tag | CloudWatch custom metric | Fallback tag chain; logs which tag resolved |
| Stale filing (no new 10-K) | Data freshness scoring penalty | Staleness is itself scored as risk; alert at 18 months |
| DynamoDB write failure | Lambda error rate alarm | Lambda retry policy; dead letter queue |
| False positive storm | False positive rate metric | 30-day cooldown; human override path |
| Private company in watchlist | EDGAR returns no CIK | Flag `ASSESSMENT_REQUIRED`; prompt manual input |
| Same filing processed twice | Duplicate alert in logs | Idempotency check on `filing_period_end` before scoring |

---

## Amazon Leadership Principles

| LP | How SRIM demonstrates it |
|---|---|
| Customer Obsession | Built for the procurement leader who needs to know before the disruption hits, not after |
| Invent & Simplify | Three AWS services and existing public EDGAR data solve a problem that expensive SRM platforms don't address |
| Are Right, A Lot | SEC filings over vendor calls; every score cites the specific EDGAR filing and date |
| Think Big | ProcureIQ (pre-award) + SRIM (post-award) = complete procurement intelligence layer |
| Insist on Highest Standards | 122 tests, idempotency guarantee, explicit false positive tracking, measurable quality targets set before first line of code |
| Dive Deep | Full XBRL tag fallback chain, audit log of which tag resolved, DynamoDB reserved-word handling (`#tr`) |
| Bias for Action | Shipped in one week with honest limitations; simple feedback loop; designed for iteration |
| Frugality | $0/month on AWS Free Tier; three services not nine; Lambda cold start acceptable for daily schedule |
| Deliver Results | Live deployed system, real EDGAR data, working alerts, CloudWatch dashboard |
| Ownership | Every architectural decision is documented and defensible end-to-end |

---

## Limitations and Disclaimers

- **Public companies only.** EDGAR covers publicly traded US companies. Private suppliers — often representing significant supply chain concentration risk — are flagged for manual assessment. The system communicates this limitation rather than fabricating a score.
- **Quarterly cadence.** Financial data updates once per quarter at most. SRIM is a leading indicator system, not a real-time feed.
- **Not investment advice.** This tool is for procurement risk monitoring purposes only.
- **Not sanctions screening.** SRIM does not perform OFAC or sanctions checks. Use a certified OFAC process before any supplier action.
- **No personal data collected.** All monitored entities are corporate. GDPR and CCPA do not apply to corporate financial data.

