# The Salt Road — demo project

This is the worked example from the README: a 49-second, five-scene,
1080p mini-documentary about why the sea is salty.

Everything in this directory except `brief.txt` was produced by the commands
below. No image, line of narration or caption was edited by hand afterwards.

```bash
export POLLINATIONS_API_KEY=...
npm install

python3 skills/reelwright/scripts/reelwright.py make \
  --brief examples/salt-road/brief.txt \
  --out examples/salt-road \
  --scenes 5 \
  --voice onyx \
  --transcribe
```

`make` runs plan → render → check. To iterate on wording instead, use the three
commands separately and edit `project.json` between them.

## What to look at

| File | Why it is interesting |
| --- | --- |
| `brief.txt` | 684 bytes. That is the entire human input. |
| `project.json` | The text model's plan: title, subtitle, reusable art direction, five scenes. |
| `timings.json` | Real narration lengths and the derived timeline. |
| `check.json` | Verification: codecs, resolution, duration vs plan, transcript coverage 99%. |
| `reel.mp4` | 49.4 s, 1920x1080, 30 fps, H.264 + AAC, 16.4 MB. |

## Verification output

```json
{
  "ok": true,
  "duration": 49.367,
  "expected_duration": 49.378,
  "video": { "codec": "h264", "width": 1920, "height": 1080, "fps": "30/1" },
  "audio": { "codec": "aac", "channels": 2 },
  "transcript_coverage": 0.992,
  "failures": []
}
```

## Reproducing the images

`--seed` was not used for this run, so the illustrations are not byte-identical
on re-render. To get a reproducible set, pass a base seed:

```bash
python3 skills/reelwright/scripts/reelwright.py render --project examples/salt-road --force --seed 42
```

Seeds are applied as `seed + scene_index`, so one flag pins the whole film.
