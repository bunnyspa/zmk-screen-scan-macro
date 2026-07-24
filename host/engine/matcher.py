"""Masked template matching against a live captured frame.

Per VisionGraph's DecisionNode design: `region` is where the reference was
cropped from in the original window screenshot, so it directly locates the
expected content in the live window - no separate search pass. This assumes
the target window's layout hasn't shifted since the reference was captured
(VisionGraph's own docs flag this as an unvalidated assumption).
"""
from __future__ import annotations

import cv2
import numpy as np


def _compute_score(frame_bgr: np.ndarray, reference_bgra: np.ndarray,
                   region: tuple[int, int, int, int]) -> float:
    x, y, w, h = region
    if y < 0 or x < 0 or y + h > frame_bgr.shape[0] or x + w > frame_bgr.shape[1]:
        return 0.0

    crop = frame_bgr[y:y + h, x:x + w]
    ref_bgr = reference_bgra[:, :, :3]
    mask = reference_bgra[:, :, 3]

    if crop.shape[:2] != ref_bgr.shape[:2]:
        return 0.0

    result = cv2.matchTemplate(crop, ref_bgr, cv2.TM_CCORR_NORMED, mask=mask)
    score = float(result[0, 0])
    # TM_CCORR_NORMED divides by the patch's own norm - a flat/blank crop
    # (zero norm, e.g. a loading screen not yet rendered) produces NaN, not
    # just a low score. Treat that as "no match" rather than letting NaN
    # leak into a >= threshold comparison or a live percentage display.
    return score if np.isfinite(score) else 0.0


def match(frame_bgr: np.ndarray, reference_bgra: np.ndarray,
          region: tuple[int, int, int, int], threshold: float) -> bool:
    """True if reference_bgra's masked content matches frame_bgr at region."""
    return _compute_score(frame_bgr, reference_bgra, region) >= threshold


def match_score(frame_bgr: np.ndarray, reference_bgra: np.ndarray,
                region: tuple[int, int, int, int]) -> float:
    """Raw match score (TM_CCORR_NORMED output, not compared against any
    threshold) - 0.0 if region is out of bounds or the crop/reference
    shapes don't line up. Used for live match-percentage display (Wait
    Until True polling / confirmation mode - see runner.py), not the
    pass/fail decision itself (see match())."""
    return _compute_score(frame_bgr, reference_bgra, region)
