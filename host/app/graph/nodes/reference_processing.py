"""One-time (upload-time, not per-frame) processing of a user-supplied
reference image into a tight, mask-aware crop.

The user marks pixels to ignore either with a 0-alpha channel, or by
painting a single flat color on everything that should be ignored
(border and/or interior "holes"). This is detected once here, and the
result is cropped to the bounding box of the surviving (comparison)
pixels - so a later matching pass (engine/matcher.py) only ever scans that
smaller area instead of paying mask + full-image cost on every check."""

import cv2
import numpy as np


class MaskDetectionError(Exception):
    pass


def process_masked_reference(src_path):
    """Returns (cropped_bgra, bounding_box, mask_pixel_count).

    cropped_bgra is a BGRA image cropped to the bounding box of
    non-ignored pixels, with alpha=0 on any ignored pixels that remain
    inside that box (interior "holes") and alpha=255 elsewhere - so
    downstream matching only needs to understand one mask format
    regardless of how the source image originally marked its mask.

    bounding_box is (x, y, w, h) of that same crop within the *source*
    image. If the user uploads a full, unmodified screenshot of the target
    window (not an arbitrary crop from elsewhere) with masking painted
    on top, this position is exactly where that content sits within the
    window - so it can be used directly to show the reference region in
    the right place in the window, with no separate live matching pass needed."""
    raw = cv2.imread(src_path, cv2.IMREAD_UNCHANGED)
    if raw is None:
        raise MaskDetectionError(f"Could not read image: {src_path}")

    if raw.ndim == 2:
        raw = cv2.cvtColor(raw, cv2.COLOR_GRAY2BGR)

    if raw.shape[2] == 4:
        bgr = raw[:, :, :3]
        keep_mask = raw[:, :, 3] > 0
    else:
        bgr = raw
        border_color = _sample_border_color(bgr)
        keep_mask = np.any(bgr != border_color, axis=-1)

    ys, xs = np.where(keep_mask)
    if ys.size == 0:
        raise MaskDetectionError(
            'No comparison pixels found - the whole image looks like the '
            'ignore color/alpha. Leave at least one region unmasked.'
        )

    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1

    cropped_bgr = bgr[y0:y1, x0:x1]
    cropped_keep = keep_mask[y0:y1, x0:x1]
    alpha = np.where(cropped_keep, 255, 0).astype(np.uint8)
    cropped_bgra = cv2.merge([
        cropped_bgr[:, :, 0], cropped_bgr[:, :, 1], cropped_bgr[:, :, 2], alpha,
    ])

    bounding_box = (x0, y0, x1 - x0, y1 - y0)
    return cropped_bgra, bounding_box, int(cropped_keep.sum())


def _sample_border_color(bgr):
    """Majority-vote color across the outer 1px ring, so a few
    anti-aliased edge pixels don't throw off detection."""
    border_pixels = np.concatenate([
        bgr[0, :], bgr[-1, :], bgr[:, 0], bgr[:, -1],
    ])
    colors, counts = np.unique(border_pixels.reshape(-1, 3), axis=0, return_counts=True)
    return colors[np.argmax(counts)]
