"""Coarse-scan window gating by reaction peaks / heatmap."""
from clipper import vision_discover as vd


def test_full_sweep_without_signals():
    w = vd._windows(300.0, [], [])
    assert w[0] == (0.0, 75.0)
    assert w[-1][1] == 300.0
    assert all(b - a <= vd.WINDOW for a, b in w)


def test_peaks_gate_windows():
    # 1h video, one laugh at 1800s -> only windows near it survive
    w = vd._windows(3600.0, [1800.0], [])
    full = vd._windows(3600.0, [], [])
    assert len(w) < len(full) / 4
    assert all(a < 1805.0 and b > 1780.0 for a, b in w)  # all overlap the peak span


def test_heatmap_spans_gate_windows():
    w = vd._windows(3600.0, [], [{"start": 100.0, "end": 130.0, "value": 1.0}])
    assert w and all(a < 130.0 and b > 100.0 for a, b in w)


def test_unmatchable_signals_fall_back_to_full_sweep():
    # a peak beyond the video duration matches nothing -> scan everything, not nothing
    w = vd._windows(300.0, [9999.0], [])
    assert w == vd._windows(300.0, [], [])


def test_peak_covers_the_runup_gag():
    # the gag PRECEDES its reaction: a peak at 76s must keep the 0-75s window too
    w = vd._windows(300.0, [76.0], [])
    assert (0.0, 75.0) in w
