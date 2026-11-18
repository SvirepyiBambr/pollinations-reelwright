---
name: reelwright
description: Turn a short brief into a narrated, captioned mini-documentary MP4 using Pollinations text, image and speech models plus ffmpeg. Use when someone asks for a short explainer video, a narrated slideshow, a "documentary in a minute", a social reel with voice-over, or wants a script turned into scenes with pictures and audio.
license: MIT
---

# Reelwright

Reelwright takes one paragraph of intent and returns a finished, 1080p, H.264/AAC
video: a written scene plan, one illustration per scene, a voice-over, burned-in
captions, cross-fades, a slow Ken Burns move on every shot, and a verification
report that proves the file is really playable.

Everything creative comes from Pollinations. The only local dependency is
ffmpeg/ffprobe.

## When to use it

- "Make me a 60-second video about X."
- "Turn this article into a narrated slideshow."
- "I need a reel for social media with voice-over and captions."
- "Explain X in five scenes with pictures."

Not for: long-form editing, talking heads, footage you already have, or anything
that needs a real camera. Reelwright is an illustrated explainer, not a video editor.

## Setup

```bash
export POLLINATIONS_API_KEY=...      # https://enter.pollinations.ai
npm install                          # pulls ffmpeg-static + ffprobe-static
```

If ffmpeg is already on PATH, Reelwright uses it. Otherwise it falls back to the
static binaries in `node_modules`, then to `$FFMPEG` / `$FFPROBE`.

## Run it

One command, brief to finished file:

```bash
python3 skills/reelwright/scripts/reelwright.py make \
  --brief examples/salt-road/brief.txt \
  --out examples/salt-road \
  --scenes 5 \
  --transcribe
```

Or step by step, which is the better way when the wording matters:

```bash
# 1. plan: text model writes title, subtitle, style and N scenes
python3 skills/reelwright/scripts/reelwright.py plan --brief brief.txt --out myreel --scenes 5

# 2. edit myreel/project.json by hand if any line is weak — this is the cheap moment to fix it

# 3. render: images, speech, clips, mux
python3 skills/reelwright/scripts/reelwright.py render --project myreel

# 4. check: ffprobe the result, optionally transcribe the audio and compare to the script
python3 skills/reelwright/scripts/reelwright.py check --project myreel --transcribe
```

Outputs land in the project directory:

| File | What it is |
| --- | --- |
| `project.json` | The plan: title, style, scenes, model choices. Editable. |
| `images/scNN.jpg` | One illustration per scene, plus `scNN.prompt.txt` with the exact prompt. |
| `audio/scNN.mp3` | Narration per scene. |
| `clips/scNN.mp4` | Silent Ken Burns clip per scene. |
| `timings.json` | Scene start/end, narration length, derived scene duration. |
| `captions.ass` | Title, subtitle, per-scene captions, credit line. |
| `reel.mp4` | The deliverable. |
| `poster.jpg` | Frame at 1.2 s, for thumbnails. |
| `render.json`, `check.json` | Machine-readable render and verification reports. |

## How the timing works

Scene duration is not guessed: Reelwright measures each narration MP3 with ffprobe,
adds a fixed tail pad, and builds the timeline from those numbers. Captions are
written into the same timeline, so a caption cannot drift away from the voice that
belongs to it. Cross-fades overlap clips by a fixed amount, and the xfade offsets
are computed from the same durations.

This is the part most slideshow generators get wrong. If you change `PAD`, `FADE`
or `MIN_SCENE`, everything downstream follows automatically.

## Verification

`check` is not a vibe check. It reads the finished file and fails loudly when:

- there is no video or no audio stream;
- the codec is not H.264 / AAC;
- the resolution is not 1920x1080 or the frame rate is not 30;
- the real duration drifts more than 0.8 s from the planned timeline;
- an image or an audio file referenced by the timeline is missing;
- `captions.ass` has fewer dialogue lines than scenes;
- with `--transcribe`: the speech-to-text of the final audio covers less than 80%
  of the script's words, which catches silent, truncated or wrong-language tracks.

That last check is the interesting one. It is a cheap end-to-end proof that the
audio in the MP4 is actually the narration you asked for, not a 200 OK with an
empty body.

## Cost and models

Defaults, all replaceable with flags:

- text: `openai/gpt-5.4-nano` — one plan call, plus one repair call if validation fails
- image: `tongyi-mai/z-image-turbo` — one call per scene
- speech: `openai/tts-1`, voice `onyx` — one call per scene

A five-scene reel is five images and five short speech clips. Re-running `render`
reuses anything already on disk; `--force` regenerates. `--seed` makes image
generation reproducible.

## Brief writing

The brief is the script's north star, so give it beats, not adjectives:

```
Title: The Salt Road.

A 50-second mini-documentary explaining why the sea is salty. Follow one drop of
rainwater from a granite mountain to the ocean. Five beats, in order:

1. Rain lands on a granite mountain and begins to run downhill.
2. A mountain stream cuts through rock and carries dissolved minerals.
3. The river widens and carries its mineral load toward the sea.
4. The sun heats the ocean; water rises as vapour, salt stays behind.
5. Salt crystals on the shore at dusk.

Visual style: editorial gouache illustration, muted teal and amber palette,
textured cold-press paper, soft grain, cinematic wide 16:9 composition.
```

A style line in the brief is worth more than any prompt engineering later: it is
carried into every image prompt verbatim, which is what keeps five separate
generations looking like one film.

## Failure modes worth knowing

- **402 from Pollinations** — the account behind the key is out of pollen. The
  script says so instead of dumping a JSON blob.
- **`image_prompt` containing words** — models love writing "a map labeled
  Evaporation". The system prompt forbids text, and every image prompt gets a
  "no text, no lettering, no logos" tail, but a bad plan is cheaper to fix in
  `project.json` than to re-render.
- **Narration longer than expected** — scenes stretch to fit the voice. Nothing is
  cut off; the video gets longer. Use fewer words per scene if you need a hard
  duration.
- **No ffmpeg** — `npm install` in the repo is the intended fix. The error message
  names both the package and the env var.
- **Captions in a non-Latin script** — ASS is UTF-8 and DejaVu Sans covers Latin,
  Cyrillic and Greek. For CJK or Arabic, change the font in `captions.ass` and make
  sure it is installed on the rendering machine.
