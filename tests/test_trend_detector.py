"""
Unit tests for trend_detector.py — no AWS calls, no I/O.

Covers:
  - All five trend labels
  - Boundary conditions (exact ±5 delta)
  - DETERIORATING requires two consecutive declines
  - Empty / single-entry history
"""

import pytest

from trend_detector import detect_trend


class TestTrendDetector:
    # ── Baseline (insufficient history) ──────────────────────────────────────

    def test_empty_history_returns_baseline(self):
        assert detect_trend([]) == "BASELINE"

    def test_single_score_returns_baseline(self):
        assert detect_trend([75]) == "BASELINE"

    # ── IMPROVING ─────────────────────────────────────────────────────────────

    def test_improving_5_point_gain(self):
        assert detect_trend([75, 70]) == "IMPROVING"

    def test_improving_exact_5_boundary(self):
        # Exactly 5 points → IMPROVING (delta >= 5)
        assert detect_trend([75, 70]) == "IMPROVING"

    def test_improving_large_gain(self):
        assert detect_trend([90, 60]) == "IMPROVING"

    # ── STABLE ────────────────────────────────────────────────────────────────

    def test_stable_no_change(self):
        assert detect_trend([70, 70]) == "STABLE"

    def test_stable_small_gain(self):
        # +4 is within stable band
        assert detect_trend([74, 70]) == "STABLE"

    def test_stable_small_decline(self):
        # -4 is within stable band
        assert detect_trend([66, 70]) == "STABLE"

    def test_stable_boundary_minus_4(self):
        assert detect_trend([66, 70]) == "STABLE"

    # ── DECLINING (single quarter) ────────────────────────────────────────────

    def test_declining_5_point_drop(self):
        # Exactly -5 → DECLINING (delta is NOT > -5)
        assert detect_trend([65, 70]) == "DECLINING"

    def test_declining_significant_drop(self):
        assert detect_trend([55, 70]) == "DECLINING"

    def test_declining_does_not_escalate_with_one_point(self):
        # Only two points — cannot confirm DETERIORATING
        assert detect_trend([60, 70]) == "DECLINING"

    # ── DETERIORATING (two consecutive declines) ──────────────────────────────

    def test_deteriorating_two_consecutive_declines(self):
        # 78 → 68 → 58: both drops > 5
        assert detect_trend([58, 68, 78]) == "DETERIORATING"

    def test_deteriorating_requires_both_drops_over_5(self):
        # Second drop (70→65) is only 5 pts = NOT > -5, so just DECLINING
        assert detect_trend([58, 68, 72]) == "DECLINING"

    def test_deteriorating_second_decline_must_also_exceed_5(self):
        # current=70 prev=74: delta=-4, which is > -5 → falls in STABLE band, not DECLINING
        assert detect_trend([70, 74, 80]) == "STABLE"

    def test_deteriorating_with_longer_history(self):
        # Extra history entries should not affect classification
        assert detect_trend([40, 52, 65, 80, 85]) == "DETERIORATING"

    def test_not_deteriorating_if_only_one_bad_quarter_in_three(self):
        # 80 → 85 → 75: most recent delta = +10 → IMPROVING
        assert detect_trend([85, 80, 75]) == "IMPROVING"

    # ── Edge values ───────────────────────────────────────────────────────────

    def test_score_of_zero(self):
        result = detect_trend([0, 10, 20])
        assert result == "DETERIORATING"

    def test_score_of_100(self):
        # newest first: current=80, prev=90, prior=100 → two drops of 10 → DETERIORATING
        result = detect_trend([80, 90, 100])
        assert result == "DETERIORATING"

    def test_identical_three_scores_are_stable(self):
        assert detect_trend([60, 60, 60]) == "STABLE"

    def test_float_scores(self):
        # Scores may be floats internally
        # current=62.5 prev=70.0 delta=-7.5; prior_delta=70.0-78.5=-8.5 → DETERIORATING
        assert detect_trend([62.5, 70.0, 78.5]) == "DETERIORATING"
