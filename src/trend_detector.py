"""
Trend Detector — rule-based trend detection across score history.

Why not ML:
  - One score per quarter = too small a dataset for statistical learning
  - CFO explainability required: "two consecutive 5-point drops" is auditable
  - False positives have real procurement consequences
  - Deterministic rules are fully testable with unit tests

Trend labels:
  BASELINE      — fewer than 2 data points; cannot determine direction
  IMPROVING     — current score ≥ previous + 5
  STABLE        — within ±5 of previous score
  DECLINING     — current score < previous - 5 (single quarter)
  DETERIORATING — two consecutive declines of > 5 pts each (confirmed trend)
"""

import logging
from typing import List

logger = logging.getLogger(__name__)


def detect_trend(score_history: List[float]) -> str:
    """
    Determine trend direction from score history.

    Args:
        score_history: List of scores, newest first. E.g. [62, 70, 78]
                       means current=62, previous=70, prior=78.

    Returns:
        One of: BASELINE, IMPROVING, STABLE, DECLINING, DETERIORATING
    """
    if len(score_history) < 2:
        logger.info(f"Trend: BASELINE (only {len(score_history)} data point(s))")
        return "BASELINE"

    current = score_history[0]
    previous = score_history[1]
    delta = current - previous

    if delta >= 5:
        direction = "IMPROVING"
    elif delta > -5:
        direction = "STABLE"
    else:
        direction = "DECLINING"

    # DETERIORATING requires two consecutive declines of > 5 pts
    # This prevents single-quarter noise from triggering escalation
    if direction == "DECLINING" and len(score_history) >= 3:
        prior = score_history[2]
        prior_delta = previous - prior
        if prior_delta < -5:
            direction = "DETERIORATING"

    logger.info(
        f"Trend: {direction} "
        f"(current={current:.1f} prev={previous:.1f} delta={delta:.1f} "
        f"history_len={len(score_history)})"
    )
    return direction
