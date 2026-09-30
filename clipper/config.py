"""Central config + small shared helpers."""
from __future__ import annotations
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

# Runtime data belongs to the user, never to site-packages (uv tool/pipx installs).
ASSETS = Path(__file__).resolve().parent / "assets"
_config_home = Path(os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config")))
load_dotenv(Path(os.getenv("HYPECUT_ENV_FILE", str(_config_home / "hypecut" / ".env"))).expanduser())
load_dotenv(Path.cwd() / ".env")
ROOT = Path(os.getenv("HYPECUT_CACHE_DIR", str(Path(os.getenv("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "hypecut"))).expanduser()

# Final vertical canvas (9:16).
OUT_W, OUT_H = 1080, 1920


@dataclass
class Settings:
    # transcription
    whisper_model: str = os.getenv("CLIPPER_WHISPER_MODEL", "small")
    whisper_device: str = os.getenv("CLIPPER_WHISPER_DEVICE", "auto")
    whisper_compute: str = os.getenv("CLIPPER_WHISPER_COMPUTE", "auto")
    language: str | None = os.getenv("CLIPPER_LANGUAGE") or None
    # "transcribe" = captions in the spoken language; "translate" = always English captions
    task: str = os.getenv("CLIPPER_TASK", "transcribe")

    # highlight picking (Claude). Cost-tiered per task: pick is taste-critical and a
    # SINGLE call -> opus; jury/coarse-discover are pattern-matching vision fan-outs
    # (a call per candidate/window) -> haiku. `model` covers everything else
    # (fine-refine, hooks). Blank per-task override = fall back to `model`.
    model: str = os.getenv("CLIPPER_MODEL", "claude-sonnet-4-6")
    pick_model: str = os.getenv("CLIPPER_PICK_MODEL", os.getenv("CLIPPER_MODEL", "claude-sonnet-4-6"))
    jury_model: str = os.getenv("CLIPPER_JURY_MODEL", "claude-haiku-4-5")
    discover_model: str = os.getenv("CLIPPER_DISCOVER_MODEL", "claude-haiku-4-5")  # coarse pass only

    # LLM provider (see llm.py): "anthropic" (default, has the 50%-off Batches API)
    # or "kimi" (Moonshot AI, OpenAI-compatible; fan-outs run live+parallel instead).
    provider: str = os.getenv("CLIPPER_PROVIDER", "anthropic")
    kimi_api_key: str = os.getenv("KIMI_API_KEY") or os.getenv("MOONSHOT_API_KEY", "")
    kimi_base_url: str = os.getenv("KIMI_BASE_URL", "https://api.moonshot.ai/v1")
    # kimi cost tiers mirror the claude ones (flagship pick, cheap vision fan-outs)
    kimi_model: str = os.getenv("KIMI_MODEL", "kimi-k2.6")
    kimi_pick_model: str = os.getenv("KIMI_PICK_MODEL", "kimi-k3")
    kimi_jury_model: str = os.getenv("KIMI_JURY_MODEL", "kimi-k2.6")
    kimi_discover_model: str = os.getenv("KIMI_DISCOVER_MODEL", "kimi-k2.6")
    num_clips: int = int(os.getenv("CLIPPER_NUM_CLIPS", "5"))
    hint: str = os.getenv("CLIPPER_HINT", "")  # human steer for highlight picking
    hook: bool = os.getenv("CLIPPER_HOOK", "0") in ("1", "true", "yes")  # prepend a cold-open teaser
    min_secs: float = float(os.getenv("CLIPPER_MIN_SECS", "15"))
    max_secs: float = float(os.getenv("CLIPPER_MAX_SECS", "60"))

    # fast tier (clipify ethos): small whisper + skip the jury, for cheap quick iteration
    fast: bool = os.getenv("CLIPPER_FAST", "0") in ("1", "true", "yes")
    fast_whisper: str = os.getenv("CLIPPER_FAST_WHISPER", "base.en")

    # audience-jury reranking (sprite-sheet vision + personas)
    jury: bool = os.getenv("CLIPPER_JURY", "0") in ("1", "true", "yes")
    discover: str = os.getenv("CLIPPER_DISCOVER", "transcript")  # "transcript" | "vision"
    candidate_pool: int = int(os.getenv("CLIPPER_CANDIDATE_POOL", "24"))  # haiku+batch jury is cheap: search wide
    hint_bonus: float = float(os.getenv("CLIPPER_HINT_BONUS", "1.5"))  # viral-score boost for steer-matched clips
    batch: bool = os.getenv("CLIPPER_BATCH", "1") in ("1", "true", "yes")  # jury via Batches API (50% off, async, default)

    # reframe: "center" | "track" | "facecam"
    reframe: str = os.getenv("CLIPPER_REFRAME", "track-fast")
    # "facecam" mode (streams: webcam in a corner over a screen-share). Instead of a
    # full-height strip that wastes the frame on dead screen space, crop a tight 9:16
    # box SIZED TO THE FACE so the streamer fills the vertical frame.
    facecam_zoom: float = float(os.getenv("CLIPPER_FACECAM_ZOOM", "3.2"))  # crop height = face height * this
    facecam_headroom: float = float(os.getenv("CLIPPER_FACECAM_HEADROOM", "0.40"))  # face center's y within the crop

    # montage / sizzle-reel mode (trailer of many beat-synced micro-cuts)
    montage: bool = os.getenv("CLIPPER_MONTAGE", "0") in ("1", "true", "yes")
    music: str = os.getenv("CLIPPER_MUSIC", "auto")  # file | URL | "auto" (newest reel's audio)
    montage_secs: float = float(os.getenv("CLIPPER_MONTAGE_SECS", "16"))
    hook_secs: float = float(os.getenv("CLIPPER_HOOK_SECS", "2.2"))  # title-card hook length
    # two-phase structure: a captioned HERO BIT (setup->punchline, dialogue-forward, music
    # building) then the music DROPS into the rapid montage (synced to the mic-drop).
    hero_secs: float = float(os.getenv("CLIPPER_HERO_SECS", "6.0"))  # 0 disables phase 1
    music_build_vol: float = float(os.getenv("CLIPPER_MUSIC_BUILD_VOL", "0.22"))  # music under the bit
    m_glass: bool = os.getenv("CLIPPER_M_GLASS", "0") in ("1", "true", "yes")  # big glass punch word (off by default)
    m_aftermath: int = int(os.getenv("CLIPPER_M_AFTERMATH", "2"))  # # of on-the-hero reaction cuts before the varied montage
    title_font: str = os.getenv("CLIPPER_TITLE_FONT", "Anton")  # ultra-condensed display
    fps: int = int(os.getenv("CLIPPER_MONTAGE_FPS", "60"))  # 60 = smoother synthetic motion (zoom/shake/transitions)
    music_vol: float = float(os.getenv("CLIPPER_MUSIC_VOL", "0.35"))  # bed, ducked under dialogue
    orig_vol: float = float(os.getenv("CLIPPER_ORIG_VOL", "1.0"))     # the show audio (matches picture)
    # montage "edit texture": punchy grade + on-beat flash + shake (the produced look)
    mstyle: bool = os.getenv("CLIPPER_MONTAGE_STYLE", "1") in ("1", "true", "yes")
    m_contrast: float = float(os.getenv("CLIPPER_M_CONTRAST", "1.12"))
    m_saturation: float = float(os.getenv("CLIPPER_M_SATURATION", "1.18"))
    m_shake_px: float = float(os.getenv("CLIPPER_M_SHAKE_PX", "10"))
    m_zoom: float = float(os.getenv("CLIPPER_M_ZOOM", "0.05"))  # per-beat punch-in fraction
    m_transitions: bool = os.getenv("CLIPPER_M_TRANSITIONS", "1") in ("1", "true", "yes")  # xfade whip/zoom/flash cuts
    m_trans_dur: float = float(os.getenv("CLIPPER_M_TRANS_DUR", "0.13"))  # transition length (s)

    # captions
    font: str = os.getenv("CLIPPER_FONT", "Anton")  # Helvetica clone; "TeX Gyre Heros Cn" / "Anton" also bundled
    fonts_dir: Path = ASSETS / "fonts"
    words_per_group: int = int(os.getenv("CLIPPER_WORDS_PER_GROUP", "3"))
    active_color: str = os.getenv("CLIPPER_ACTIVE_COLOR", "00FFFF")  # ASS = &HBBGGRR, yellow
    base_color: str = os.getenv("CLIPPER_BASE_COLOR", "FFFFFF")      # white

    # encode
    vcodec: str = os.getenv("CLIPPER_VCODEC", "auto")
    crf: str = os.getenv("CLIPPER_CQ", "21")

    # distribution loop: trend -> clip -> publish -> measure -> learn
    # (ported from oxcorp-loop; dry-run by default — nothing posts without --live)
    goal: str = os.getenv("CLIPPER_GOAL", "")           # what the run is about (trend search query)
    trend: bool = os.getenv("CLIPPER_TREND", "0") in ("1", "true", "yes")  # bias picking with a trend brief
    target_platform: str = os.getenv("CLIPPER_TARGET_PLATFORM", "instagram")
    youtube_api_key: str = os.getenv("YOUTUBE_API_KEY", "")   # trend signal; blank = offline brief
    youtube_region: str = os.getenv("YOUTUBE_REGION", "IN")   # jury is calibrated for India tier-1
    ig_user_id: str = os.getenv("IG_USER_ID", "")             # consented IG Business/Creator account id
    ig_access_token: str = os.getenv("IG_ACCESS_TOKEN", "")   # long-lived Graph API token
    public_media_base_url: str = os.getenv("PUBLIC_MEDIA_BASE_URL", "")  # where /media/<clip_id> is public
    trend_brief: dict = field(default_factory=dict)  # runtime: brief from trend.research
    publish_after: bool = False  # runtime: --publish
    live: bool = False           # runtime: --live (ACTUALLY post)

    roster_seats: list = field(default_factory=list)  # runtime: source-px x of each panel seat
    roster: list = field(default_factory=list)  # runtime: full who's-who [{name, x_px, role}]
    heatmap: list = field(default_factory=list)  # runtime: YouTube most-replayed [{start,end,value}]
    reaction_peaks: list = field(default_factory=list)  # runtime: audience laughter/applause timestamps
    title: str = ""  # runtime: video title, for the roster naming prompt

    work: Path = field(default=ROOT / "work")
    out: Path = field(default_factory=lambda: Path.cwd() / "hypecut-out")
    data: Path = field(default=ROOT / "data")  # loop assets: loop.sqlite, format_library.json


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """Run a command, raising with captured stderr on failure."""
    cp = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if cp.returncode != 0:
        tail = "\n".join((cp.stderr or "").strip().splitlines()[-8:])
        raise RuntimeError(f"{cmd[0]} failed (exit {cp.returncode}):\n{tail}")
    return cp


def ffprobe_dims(path: Path) -> tuple[int, int]:
    cp = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
              "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path)])
    w, h = cp.stdout.strip().split("x")
    return int(w), int(h)
