"""Caption grouping / ASS emission — pure logic, no ffmpeg."""
from clipper import captions
from clipper.config import Settings


def w(start, end, text):
    return {"start": start, "end": end, "text": text}


def test_ts_format():
    assert captions._ts(0) == "0:00:00.00"
    assert captions._ts(3661.5) == "1:01:01.50"
    assert captions._ts(-2) == "0:00:00.00"  # clamped, never negative


def test_group_by_size():
    words = [w(i, i + 0.4, f"w{i}") for i in range(7)]
    groups = captions._group(words, 3)
    assert [len(g) for g in groups] == [3, 3, 1]


def test_group_breaks_on_pause():
    words = [w(0, 0.4, "a"), w(0.5, 0.9, "b"), w(2.0, 2.4, "c")]  # 1.1s gap b->c
    groups = captions._group(words, 5)
    assert [len(g) for g in groups] == [2, 1]


def test_build_ass_one_event_per_word(tmp_path):
    s = Settings()
    words = [w(0, 0.4, "hello"), w(0.5, 0.9, "world"), w(1.0, 1.4, "again")]
    out = captions.build_ass(words, tmp_path / "t.ass", s)
    events = [l for l in out.read_text().splitlines() if l.startswith("Dialogue:")]
    assert len(events) == len(words)
    assert all(f"\\c&H{s.active_color}&" in e for e in events)  # active word colored


def test_build_ass_escapes_braces(tmp_path):
    s = Settings()
    out = captions.build_ass([w(0, 0.4, "{evil}")], tmp_path / "t.ass", s)
    body = out.read_text().splitlines()[-1]
    assert "{evil}" not in body and "(evil)" in body  # no ASS tag injection


def test_last_word_tail_never_overlaps_next_group(tmp_path):
    s = Settings()
    # group of 1, then next group starts 0.05s after the word ends
    words = [w(0, 1.0, "one."), w(1.05, 1.5, "two")]
    out = captions.build_ass(words, tmp_path / "t.ass", s)
    events = [l for l in out.read_text().splitlines() if l.startswith("Dialogue:")]
    end_first = events[0].split(",")[2]
    start_second = events[1].split(",")[1]
    assert end_first <= start_second
