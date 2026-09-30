"""Reframe keyframe math + render word-windowing — pure logic, no ffmpeg."""
from clipper import reframe, render, vision_discover


def test_crop_dims_16x9_source():
    cw, ch = reframe._crop_dims(1920, 1080)
    assert (cw, ch) == (608, 1080)  # largest 9:16 window, even dims
    assert cw % 2 == 0 and ch % 2 == 0


def test_crop_dims_vertical_source_fits():
    cw, ch = reframe._crop_dims(1080, 1920)
    assert (cw, ch) == (1080, 1920)


def test_decimate_drops_static_keyframes():
    kf = [(t / 10, 100) for t in range(50)]  # never moves
    out = reframe._decimate(kf)
    assert len(out) == 2  # just endpoints


def test_decimate_caps_count():
    kf = [(float(i), i * 40) for i in range(200)]  # always moving
    out = reframe._decimate(kf)
    assert len(out) <= 24


def test_piecewise_single_keyframe_is_constant():
    assert reframe._piecewise([(0.0, 55)]) == "55"


def test_piecewise_interpolates_endpoints():
    expr = reframe._piecewise([(0.0, 0), (2.0, 100)])
    assert expr.startswith("if(")
    assert "100" in expr and "(t-0.0)" in expr


def test_smooth_preserves_constant():
    assert reframe._smooth([5.0] * 10, 5) == [5.0] * 10


def test_rel_words_windows_and_offsets():
    words = [{"start": 9.0, "end": 10.5, "text": "a"},
             {"start": 11.0, "end": 12.0, "text": "b"},
             {"start": 25.0, "end": 26.0, "text": "c"}]
    rel = render._rel_words(words, 10.0, 20.0)
    assert [r["text"] for r in rel] == ["a", "b"]
    assert rel[0]["start"] == 0.0            # clamped to clip start
    assert rel[1] == {"text": "b", "start": 1.0, "end": 2.0}


def test_merge_joins_close_moments():
    m = vision_discover._merge(
        [{"start": 0, "end": 10, "strength": 5, "reason": "x"},
         {"start": 12, "end": 20, "strength": 8, "reason": "longer reason"}],
        min_secs=5, max_secs=60)
    assert len(m) == 1
    assert m[0]["end"] == 20 and m[0]["strength"] == 8


def test_merge_enforces_min_max():
    m = vision_discover._merge(
        [{"start": 0, "end": 2, "strength": 5, "reason": "short"},
         {"start": 100, "end": 300, "strength": 5, "reason": "long"}],
        min_secs=15, max_secs=60)
    assert m[0]["end"] - m[0]["start"] == 15
    assert m[1]["end"] - m[1]["start"] == 60
