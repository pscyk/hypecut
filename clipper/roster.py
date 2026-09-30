"""Who's-who roster: identify the people on screen and their seat positions.

Picks the frame with the most visible faces (the full panel), then asks Claude
(vision) to name each person and give their horizontal position. The result is a
stable seat->name map the reframer can use to snap to seats and to frame a
two-shot when two named people are in conversation.
"""
from __future__ import annotations
import glob
import json
import os
import tempfile
from pathlib import Path
import anthropic
import cv2
from .config import Settings, run, ffprobe_dims

TOOL = {
    "name": "roster",
    "description": "Report each visible person on the panel/stage.",
    "input_schema": {
        "type": "object",
        "properties": {
            "people": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "their name (use show context + transcript); 'Unknown' if unsure"},
                        "role": {"type": "string", "description": "e.g. host, judge, guest, contestant"},
                        "x_fraction": {"type": "number", "description": "horizontal center, 0.0=far left .. 1.0=far right"},
                    },
                    "required": ["name", "x_fraction"],
                },
            }
        },
        "required": ["people"],
    },
}


def _best_panel_frame(src: Path, out: Path) -> int:
    """Sample frames across the video, return the one with the most faces."""
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    dur = float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "csv=p=0", str(src)]).stdout.strip())
    tmp = tempfile.mkdtemp(prefix="roster_")
    best_n, best = -1, None
    try:
        for i in range(12):
            t = dur * (i + 1) / 13
            fp = os.path.join(tmp, f"{i}.jpg")
            run(["ffmpeg", "-nostdin", "-y", "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1",
                 "-vf", "scale=960:-1", fp])
            img = cv2.imread(fp)
            if img is None:
                continue
            faces = cascade.detectMultiScale(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), 1.2, 5, minSize=(30, 30))
            if len(faces) > best_n:
                best_n, best = len(faces), fp
        if best:
            img = cv2.imread(best)
            cv2.imwrite(str(out), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
            return img.shape[1]
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


def build(src: Path, words: list[dict], title: str, s: Settings, cache: Path) -> list[dict]:
    if cache.exists():
        return json.loads(cache.read_text())

    frame = s.work / f"{src.stem}.panel.jpg"
    width = _best_panel_frame(src, frame)
    if not width:
        return []
    import base64
    b64 = base64.standard_b64encode(frame.read_bytes()).decode()
    transcript = " ".join(w["text"] for w in words[:400])

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=s.model, max_tokens=600,
        tools=[TOOL], tool_choice={"type": "tool", "name": "roster"},
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
            {"type": "text", "text":
                f"This is a frame from the show \"{title}\". Identify every visible person on the "
                f"panel/stage left-to-right, with their name (use the show context and the transcript "
                f"below) and horizontal position. Transcript opening:\n{transcript}"},
        ]}],
    )
    people = next(b.input["people"] for b in msg.content if b.type == "tool_use")
    people.sort(key=lambda p: p.get("x_fraction", 0.5))
    src_w = ffprobe_dims(src)[0]  # x_px must be in SOURCE pixels (frame above is downscaled)
    for p in people:
        p["x_px"] = round(p.get("x_fraction", 0.5) * src_w)
    print(f"[roster] {len(people)} people identified:")
    for p in people:
        print(f"  {p.get('x_fraction', 0):.2f}  {p['name']} ({p.get('role', '?')})")
    cache.write_text(json.dumps(people, ensure_ascii=False, indent=1))
    return people
