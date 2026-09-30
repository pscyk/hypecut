# Hypecut architecture

Hypecut is a standalone clipping engine. It runs without a database service or
a running HTTP server.

1. `cli.py` validates arguments and creates a unique output directory.
2. `runtime.py` checks keys, FFmpeg/libass, the transcription device, and the encoder.
3. `download.py` calls the installed `yt_dlp` module. The post-processing receipt
   identifies the exact downloaded file, including merged Twitch downloads.
4. `transcribe.py` produces word timestamps with faster-whisper.
5. `highlights.py` selects moments using the configured language-model provider.
6. `pipeline.py` constrains source times and orchestrates rendering.
7. `reframe.py`, `render.py`, and `captions.py` produce 9:16 H.264 clips with captions.
8. Each successful clip gets an SRT; `manifest.json` reports the actual outcome.

Model and source identity are part of the CLI's analysis-cache key. Render
intermediates get a unique directory per run. Installed package directories
contain only code and bundled assets; mutable files live in the user cache or
selected output root. No credential is written to the manifest.

The lightweight face tracker is available everywhere OpenCV runs. LR-ASD is
optional because it requires its own inference wrapper, weights, and Python
runtime. On the original Anton setup, these resources live under `~/clipper`;
configure them explicitly to use them from Hypecut. The checkout is read only
from Hypecut's perspective; inference caches are in Hypecut's cache directory.

Legacy engine modules for montage, trend research, distribution, and the HTTP
service are retained for reference. They are not part of the Hypecut CLI contract,
and are not started by its entrypoint. The inherited logic tests remain as a
regression suite. No production deployment is changed by installing this CLI.

`--min` and `--max` bound rendered moments. Source material can yield fewer
moments than requested. Failed renders are removed and recorded, and any
partial failure gives a nonzero exit status while preserving successful clips.
