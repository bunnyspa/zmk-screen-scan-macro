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


def match(frame_bgr: np.ndarray, reference_bgra: np.ndarray,
          region: tuple[int, int, int, int], threshold: float) -> bool:
    """True if reference_bgra's masked content matches frame_bgr at region."""
    x, y, w, h = region
    if y < 0 or x < 0 or y + h > frame_bgr.shape[0] or x + w > frame_bgr.shape[1]:
        return False

    crop = frame_bgr[y:y + h, x:x + w]
    ref_bgr = reference_bgra[:, :, :3]
    mask = reference_bgra[:, :, 3]

    if crop.shape[:2] != ref_bgr.shape[:2]:
        return False

    result = cv2.matchTemplate(crop, ref_bgr, cv2.TM_CCORR_NORMED, mask=mask)
    return float(result[0, 0]) >= threshold
