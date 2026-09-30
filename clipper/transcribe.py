"""Step 1: word-level transcription via faster-whisper on the GPU."""
from __future__ import annotations
import json
from pathlib import Path
from faster_whisper import WhisperModel
from .config import Settings

_model: WhisperModel | None = None


def _load(s: Settings) -> WhisperModel:
    global _model
    if _model is None:
        from . import _preload_cuda_libs
        if s.whisper_device == "cuda":
            _preload_cuda_libs()
        print(f"[transcribe] loading {s.whisper_model} on {s.whisper_device} ({s.whisper_compute})")
        _model = WhisperModel(s.whisper_model, device=s.whisper_device, compute_type=s.whisper_compute)
    return _model


def transcribe(video: Path, s: Settings, cache: Path) -> list[dict]:
    """Return a flat list of words: [{start, end, text}]. Cached to `cache`,
    keyed by the settings that change the output (model/task/language)."""
    meta = {"model": s.whisper_model, "task": s.task, "language": s.language or "auto"}
    if cache.exists():
        data = json.loads(cache.read_text())
        if isinstance(data, list):  # legacy cache (no meta recorded): accept as-is
            print(f"[transcribe] using cache {cache.name}")
            return data
        if data.get("meta") == meta:
            print(f"[transcribe] using cache {cache.name}")
            return data["words"]
        print(f"[transcribe] cache stale ({data.get('meta')} -> {meta}); re-transcribing")

    model = _load(s)
    segments, info = model.transcribe(
        str(video), task=s.task, language=s.language, word_timestamps=True,
        vad_filter=True, beam_size=5,
    )
    arrow = " -> en" if s.task == "translate" else ""
    print(f"[transcribe] language={info.language} ({info.language_probability:.2f}){arrow}")

    words: list[dict] = []
    for seg in segments:
        for w in (seg.words or []):
            t = w.word.strip()
            if t:
                words.append({"start": round(w.start, 3), "end": round(w.end, 3), "text": t})
    print(f"[transcribe] {len(words)} words")
    cache.write_text(json.dumps({"meta": meta, "words": words}, ensure_ascii=False, indent=1))
    return words
