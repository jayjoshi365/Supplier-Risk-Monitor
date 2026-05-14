"""
Unit tests for alert_engine.py — should_alert logic.
send_alert is mocked (no real SNS calls in unit tests).

Covers:
  - Alert fires only on DETERIORATING + breach
  - 30-day cooldown suppresses normal alerts
  - CRITICAL level bypasses cooldown
  - Cooldown edge cases (exactly 30 days, 29 days)
  - Non-DETERIORATING trends don't alert
  - Score above threshold doesn't alert
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

from alert_engine import should_alert


def _days_ago(n: int) -> str:
    dt = datetime.now(tz=timezone.utc) - timedelta(days=n)
    return dt.isoformat()


class TestShouldAlert:
    # ── Conditions that prevent alerting ────────────────────────────────────

    def test_stable_trend_does_not_alert(self):
        result = should_alert(50, "STABLE", 60, None)
        assert result["should_fire"] is False

    def test_improving_trend_does_not_alert(self):
        result = should_alert(50, "IMPROVING", 60, None)
        assert result["should_fire"] is False

    def test_declining_trend_does_not_alert(self):
        # DECLINING alone is not enough — must be DETERIORATING
        result = should_alert(50, "DECLINING", 60, None)
        assert result["should_fire"] is False

    def test_baseline_trend_does_not_alert(self):
        result = should_alert(50, "BASELINE", 60, None)
        assert result["should_fire"] is False

    def test_score_above_threshold_does_not_alert(self):
        result = should_alert(70, "DETERIORATING", 60, None)
        assert result["should_fire"] is False

    def test_score_equal_to_threshold_does_not_alert(self):
        # "below threshold" means strictly less than
        result = should_alert(60, "DETERIORATING", 60, None)
        assert result["should_fire"] is False

    # ── Conditions that trigger alerting ─────────────────────────────────────

    def test_deteriorating_below_threshold_fires(self):
        result = should_alert(55, "DETERIORATING", 60, None)
        assert result["should_fire"] is True

    def test_alert_level_is_normal_when_not_critical(self):
        # 55 < 60 threshold, but 55 >= 60-15=45 → NORMAL
        result = should_alert(55, "DETERIORATING", 60, None)
        assert result["level"] == "NORMAL"

    def test_alert_level_is_critical_when_15_below_threshold(self):
        # 44 < 60-15=45 → CRITICAL
        result = should_alert(44, "DETERIORATING", 60, None)
        assert result["should_fire"] is True
        assert result["level"] == "CRITICAL"

    def test_alert_level_critical_exact_boundary(self):
        # score == threshold - 15 is NOT < critical threshold
        result = should_alert(45, "DETERIORATING", 60, None)
        assert result["level"] == "NORMAL"

    def test_no_previous_alert_fires(self):
        result = should_alert(50, "DETERIORATING", 60, None)
        assert result["should_fire"] is True

    # ── Cooldown logic ────────────────────────────────────────────────────────

    def test_cooldown_suppresses_alert_within_30_days(self):
        last = _days_ago(15)
        result = should_alert(55, "DETERIORATING", 60, last)
        assert result["should_fire"] is False
        assert "cooldown" in result["reason"].lower()

    def test_cooldown_suppresses_alert_at_29_days(self):
        last = _days_ago(29)
        result = should_alert(55, "DETERIORATING", 60, last)
        assert result["should_fire"] is False

    def test_cooldown_allows_alert_at_30_days(self):
        last = _days_ago(30)
        result = should_alert(55, "DETERIORATING", 60, last)
        assert result["should_fire"] is True

    def test_cooldown_allows_alert_after_30_days(self):
        last = _days_ago(45)
        result = should_alert(55, "DETERIORATING", 60, last)
        assert result["should_fire"] is True

    # ── CRITICAL bypasses cooldown ────────────────────────────────────────────

    def test_critical_alert_bypasses_cooldown(self):
        # Score 40 is 20 pts below threshold 60 → CRITICAL → bypass cooldown
        last = _days_ago(5)
        result = should_alert(40, "DETERIORATING", 60, last)
        assert result["should_fire"] is True
        assert result["level"] == "CRITICAL"

    def test_critical_fires_even_with_recent_alert(self):
        last = _days_ago(1)
        result = should_alert(30, "DETERIORATING", 60, last)
        assert result["should_fire"] is True

    # ── Malformed timestamp handling ──────────────────────────────────────────

    def test_malformed_last_alerted_does_not_crash(self):
        result = should_alert(55, "DETERIORATING", 60, "not-a-date")
        # Should still fire (can't parse → treat as no cooldown)
        assert result["should_fire"] is True

    def test_z_suffix_timestamp_is_handled(self):
        last = _days_ago(5).replace("+00:00", "Z")
        result = should_alert(55, "DETERIORATING", 60, last)
        assert result["should_fire"] is False

    # ── Result structure ──────────────────────────────────────────────────────

    def test_no_fire_result_has_reason(self):
        result = should_alert(70, "STABLE", 60, None)
        assert "reason" in result

    def test_fire_result_has_level_and_reason(self):
        result = should_alert(50, "DETERIORATING", 60, None)
        assert "level" in result
        assert "reason" in result

    # ── Custom thresholds ─────────────────────────────────────────────────────

    def test_strategic_supplier_high_threshold(self):
        # threshold=70 (strategic sole-source)
        result = should_alert(65, "DETERIORATING", 70, None)
        assert result["should_fire"] is True

    def test_commodity_supplier_low_threshold(self):
        # threshold=45 (dual-sourced commodity)
        result = should_alert(50, "DETERIORATING", 45, None)
        assert result["should_fire"] is False  # 50 > 45
