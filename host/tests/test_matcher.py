import numpy as np

from engine.matcher import match, match_score


def _blank_frame(shape=(300, 300, 3)):
    return np.zeros(shape, dtype=np.uint8)


def test_match_true_when_content_matches():
    frame = _blank_frame()
    region = (50, 50, 20, 20)
    content = np.random.default_rng(0).integers(0, 255, size=(20, 20, 3), dtype=np.uint8)
    frame[50:70, 50:70] = content

    reference_bgra = np.dstack([content, np.full((20, 20), 255, dtype=np.uint8)])

    assert match(frame, reference_bgra, region, threshold=0.99)


def test_match_false_when_content_differs():
    frame = _blank_frame()
    region = (50, 50, 20, 20)
    rng = np.random.default_rng(0)
    frame[50:70, 50:70] = rng.integers(0, 255, size=(20, 20, 3), dtype=np.uint8)

    different_content = rng.integers(0, 255, size=(20, 20, 3), dtype=np.uint8)
    reference_bgra = np.dstack([different_content, np.full((20, 20), 255, dtype=np.uint8)])

    assert not match(frame, reference_bgra, region, threshold=0.99)


def test_match_ignores_masked_out_pixels():
    frame = _blank_frame()
    region = (50, 50, 20, 20)
    rng = np.random.default_rng(1)
    content = rng.integers(0, 255, size=(20, 20, 3), dtype=np.uint8)
    frame[50:70, 50:70] = content

    reference_bgr = content.copy()
    alpha = np.full((20, 20), 255, dtype=np.uint8)
    # Punch a "hole" (alpha=0) and deliberately mismatch that region's color -
    # since it's masked out, it must not affect the match result.
    alpha[0:5, 0:5] = 0
    reference_bgr[0:5, 0:5] = 255 - reference_bgr[0:5, 0:5]
    reference_bgra = np.dstack([reference_bgr, alpha])

    assert match(frame, reference_bgra, region, threshold=0.99)


def test_match_false_when_region_out_of_bounds():
    frame = _blank_frame(shape=(50, 50, 3))
    region = (40, 40, 20, 20)  # extends past the 50x50 frame
    reference_bgra = np.zeros((20, 20, 4), dtype=np.uint8)

    assert not match(frame, reference_bgra, region, threshold=0.5)


def test_match_score_high_when_content_matches():
    frame = _blank_frame()
    region = (50, 50, 20, 20)
    content = np.random.default_rng(0).integers(0, 255, size=(20, 20, 3), dtype=np.uint8)
    frame[50:70, 50:70] = content
    reference_bgra = np.dstack([content, np.full((20, 20), 255, dtype=np.uint8)])

    assert match_score(frame, reference_bgra, region) >= 0.99


def test_match_score_zero_when_region_out_of_bounds():
    frame = _blank_frame(shape=(50, 50, 3))
    region = (40, 40, 20, 20)  # extends past the 50x50 frame
    reference_bgra = np.zeros((20, 20, 4), dtype=np.uint8)

    assert match_score(frame, reference_bgra, region) == 0.0


def test_match_score_zero_when_shapes_mismatch():
    frame = _blank_frame()
    region = (50, 50, 20, 20)
    reference_bgra = np.zeros((5, 5, 4), dtype=np.uint8)  # doesn't match region's 20x20

    assert match_score(frame, reference_bgra, region) == 0.0


def test_match_score_zero_not_nan_when_region_is_blank():
    # TM_CCORR_NORMED divides by the crop's own norm - an all-zero (blank)
    # region has zero norm, which would otherwise produce NaN rather than a
    # low score (a real scenario: polling against a not-yet-rendered/loading
    # screen). NaN must not leak into a >= threshold comparison or a live
    # percentage display.
    frame = _blank_frame()  # region stays all-zero - never painted with content
    region = (50, 50, 20, 20)
    content = np.random.default_rng(0).integers(1, 255, size=(20, 20, 3), dtype=np.uint8)
    reference_bgra = np.dstack([content, np.full((20, 20), 255, dtype=np.uint8)])

    score = match_score(frame, reference_bgra, region)
    assert score == 0.0
    assert not match(frame, reference_bgra, region, threshold=0.5)
