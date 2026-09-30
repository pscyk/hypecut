# Hypecut product film

A 32-second, 1920 × 1080, 30 fps product film, built with Remotion. The source-to-clips
layout uses real media, animated card transitions, word-level caption examples,
and an original electronic score. There is no voiceover or borrowed music.

## Render

```sh
npm ci
npm run typecheck
npm run render
```

Remotion downloads its browser on first use. An existing Chrome Headless Shell can
be selected with `npm run render -- --browser-executable /path/to/chrome-headless-shell`.
For a machine with limited memory, also pass `--concurrency 2`.

```sh
npm run studio  # inspect the timeline
npm run still   # export the comedy transformation frame
```

The final file is `output/hypecut-product-1080p.mp4`. Intermediate renders are ignored
by Git. Source clips and licensed fonts are in `public/`; the score is generated
by `make_audio.py` (Python + NumPy), and its WAV is included for repeatable renders.

## Storyboard

| Time | Beat |
| --- | --- |
| 0:00–0:03 | One link. All the highlights. A command types on. |
| 0:03–0:12 | A long comedy video fans into three captioned clips. |
| 0:12–0:20 | A coding stream becomes two facecam clips. |
| 0:20–0:26 | Find the moment, frame the speaker, make every word land. |
| 0:26–0:32 | Hypecut wordmark, command, and repository address. |

## Design system

- Canvas: graphite `#0A0D0E`; surface `#14191B`; ink `#F3F5EB`; secondary ink
  `#A0AAA7`; accent `#D6FC75`.
- Font families: Inter for product copy; IBM Plex Mono for commands and labels.
  Font licenses are bundled in `public/fonts/`.
- Type scale at 1080p: 132–136 px hero, 74 px scene title, 54 px feature title,
  24 px supporting copy, 18–20 px labels. The closing command is 32 px.
- Weights: 450 supporting copy, 600–650 titles, 720–760 wordmarks. Supporting
  paragraphs use 1.5 line height; display lines use 1.02–1.15.
- Tracking: tighter display text, neutral body copy, spaced monospace labels.
- Motion: eased title reveals, damped card springs, staggered transitions, and
  clean directional wipes. Readable poses stay on screen for multiple seconds.

Media excerpts show example outputs, not deterministic promises about highlight
selection. Active-speaker tracking in the featured samples uses the optional
LR-ASD setup; the portable CLI also offers face-motion tracking.
