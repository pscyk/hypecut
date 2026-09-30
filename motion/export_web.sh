#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
input=output/hypecut-product-1080p.mp4
out=../docs/demo
# H.264/AAC with metadata first, for progressive playback after clicking.
ffmpeg -nostdin -hide_banner -loglevel error -y -i "$input" \
  -vf 'scale=1280:720:flags=lanczos' -c:v libx264 -preset slow -crf 28 \
  -maxrate 900k -bufsize 1800k -pix_fmt yuv420p -c:a aac -b:a 80k \
  -movflags +faststart "$out/hypecut-product-720p.mp4"
# A six-second preview shows the existing comedy and Tsoding/Twitch examples.
ffmpeg -nostdin -hide_banner -loglevel error -y -i "$input" \
  -filter_complex "[0:v]split=2[a][b];[a]trim=start=7:duration=3,setpts=PTS-STARTPTS[a1];[b]trim=start=16:duration=3,setpts=PTS-STARTPTS[b1];[a1][b1]concat=n=2:v=1:a=0,fps=5,scale=560:-1:flags=lanczos,split[x][y];[x]palettegen=max_colors=64:stats_mode=diff[p];[y][p]paletteuse=dither=bayer:bayer_scale=5" \
  -an -loop 0 "$out/hypecut-product-preview.gif"
ffmpeg -nostdin -hide_banner -loglevel error -y -ss 17 -i "$input" \
  -frames:v 1 -vf scale=960:-2 -q:v 4 "$out/hypecut-product-poster.jpg"
# Keep README bytes measurable, and fail if a later edit outgrows these budgets.
python3 - <<'PY'
from pathlib import Path
for name,limit in [('hypecut-product-preview.gif',600_000),('hypecut-product-720p.mp4',3_000_000)]:
    size=(Path('../docs/demo')/name).stat().st_size
    print(f'{name}: {size:,} bytes (budget {limit:,})')
    if size > limit:
        raise SystemExit(f'{name} exceeds its web size budget')
PY
