#!/usr/bin/env python3
"""Reelwright: turn a brief into a narrated, captioned mini-documentary.

Pipeline: Pollinations text -> scene plan, Pollinations image -> illustrations,
Pollinations speech -> narration, ffmpeg -> 1080p MP4 with burned-in captions.

Only the Python standard library is used. ffmpeg/ffprobe are external.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

API_BASE = "https://gen.pollinations.ai"
DEFAULT_TEXT_MODEL = "openai/gpt-5.4-nano"
DEFAULT_IMAGE_MODEL = "tongyi-mai/z-image-turbo"
DEFAULT_SPEECH_MODEL = "openai/tts-1"
DEFAULT_VOICE = "onyx"
DEFAULT_IMAGE_SIZE = "1792x1024"
WIDTH = 1920
HEIGHT = 1080
FPS = 30
FADE = 0.6
PAD = 0.65
MIN_SCENE = 4.0
USER_AGENT = "reelwright/1.0 (+https://github.com/SvirepyiBambr/pollinations-reelwright)"

# Written by `polli auth login`; Reelwright reads it so an agent that signed in
# with Pollinations never has to handle a key itself.
POLLI_CREDENTIALS = Path.home() / ".pollinations" / "credentials.json"
POLLI_CREDENTIALS_STAGING = Path.home() / ".pollinations" / "credentials.staging.json"


def log(message: str) -> None:
    print(message, flush=True)


def die(message: str) -> None:
    print(f"error: {message}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def read_brief(value: str) -> str:
    """Accept either a path to a text file or the brief itself."""
    try:
        path = Path(value)
        if path.is_file():
            return path.read_text(encoding="utf-8")
    except OSError:
        pass
    return value


def polli_key() -> str:
    """Return the key stored by `polli auth login`, or an empty string."""
    staging = os.environ.get("POLLINATIONS_ENV", "").strip().lower() == "staging"
    for path in ((POLLI_CREDENTIALS_STAGING, POLLI_CREDENTIALS) if staging else (POLLI_CREDENTIALS,)):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        key = str(data.get("apiKey") or "").strip()
        if key:
            return key
    return ""


def api_key(explicit: str | None) -> str:
    key = explicit or os.environ.get("POLLINATIONS_API_KEY", "").strip() or polli_key()
    if not key:
        die(
            "Not signed in to Pollinations. Run `polli auth login` once "
            "(https://www.npmjs.com/package/@pollinations/cli), or export "
            "POLLINATIONS_API_KEY, or pass --api-key."
        )
    return key


def _http_error_text(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", "replace")[:500]
    except Exception:
        return ""


def _sleep_for_rate_limit(exc: urllib.error.HTTPError, attempt: int) -> None:
    retry_after = exc.headers.get("Retry-After") if exc.headers else None
    if retry_after and re.fullmatch(r"\d+(?:\.\d+)?", retry_after.strip()):
        wait = min(60.0, float(retry_after))
    else:
        wait = min(30.0, 2.0 ** attempt)
    log(f"  rate limited; waiting {wait:.0f}s")
    time.sleep(wait)


def request_json(
    path: str,
    payload: dict[str, Any],
    key: str,
    *,
    timeout: int = 240,
    retries: int = 4,
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(
            API_BASE + path,
            data=data,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read()
            return json.loads(body.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            message = _http_error_text(exc)
            if exc.code == 429 and attempt < retries:
                _sleep_for_rate_limit(exc, attempt)
                last_error = exc
                continue
            if 500 <= exc.code < 600 and attempt < retries:
                time.sleep(min(20.0, 2.0 ** attempt))
                last_error = exc
                continue
            if exc.code == 401:
                die("Pollinations rejected the API key (401). Check POLLINATIONS_API_KEY.")
            if exc.code == 402:
                die(
                    "Pollinations says there is not enough pollen (402). "
                    "Top up the account behind this key, or use a free model."
                )
            die(f"Pollinations {path} returned HTTP {exc.code}: {message}")
        except urllib.error.URLError as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(min(20.0, 2.0 ** attempt))
                continue
            die(f"Cannot reach Pollinations: {exc.reason}")
    die(f"Pollinations request failed after retries: {last_error}")


def request_bytes(
    path: str,
    payload: dict[str, Any],
    key: str,
    *,
    timeout: int = 300,
    retries: int = 4,
) -> bytes:
    data = json.dumps(payload).encode("utf-8")
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(
            API_BASE + path,
            data=data,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read()
                content_type = response.headers.get("Content-Type", "")
            if "application/json" in content_type:
                die(f"Pollinations {path} returned JSON instead of audio: {body[:300]!r}")
            if not body:
                die(f"Pollinations {path} returned an empty response")
            return body
        except urllib.error.HTTPError as exc:
            message = _http_error_text(exc)
            if exc.code == 429 and attempt < retries:
                _sleep_for_rate_limit(exc, attempt)
                last_error = exc
                continue
            if 500 <= exc.code < 600 and attempt < retries:
                time.sleep(min(20.0, 2.0 ** attempt))
                last_error = exc
                continue
            if exc.code == 401:
                die("Pollinations rejected the API key (401). Check POLLINATIONS_API_KEY.")
            if exc.code == 402:
                die("Pollinations says there is not enough pollen (402). Top up the account.")
            die(f"Pollinations {path} returned HTTP {exc.code}: {message}")
        except urllib.error.URLError as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(min(20.0, 2.0 ** attempt))
                continue
            die(f"Cannot reach Pollinations: {exc.reason}")
    die(f"Pollinations request failed after retries: {last_error}")


def resolve_tool(name: str, env_var: str, node_expression: str) -> str:
    explicit = os.environ.get(env_var)
    if explicit and Path(explicit).expanduser().exists():
        return str(Path(explicit).expanduser())
    found = shutil.which(name)
    if found:
        return found
    repo_root = Path(__file__).resolve().parents[3]
    local = repo_root / "node_modules"
    candidates = [local / "ffmpeg-static" / name, local / "ffprobe-static" / "bin" / "linux" / "x64" / name]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    try:
        result = subprocess.run(
            ["node", "-p", node_expression],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            candidate = Path(result.stdout.strip())
            if candidate.exists():
                return str(candidate)
    except Exception:
        pass
    die(
        f"{name} was not found. Install it with your package manager, or run "
        "`npm install ffmpeg-static ffprobe-static` in the repository and retry. "
        f"You can also set {env_var}=/path/to/{name}."
    )


def run_ffmpeg(command: list[str], cwd: Path) -> None:
    result = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip()[-3000:]
        die("ffmpeg failed:\n" + tail)


def probe_duration(ffprobe: str, path: Path) -> float:
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        die(f"ffprobe could not read {path}: {result.stderr.strip()[:500]}")
    try:
        return float(result.stdout.strip())
    except ValueError:
        die(f"ffprobe returned no duration for {path}")


def probe_json(ffprobe: str, path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        die(f"ffprobe could not read {path}: {result.stderr.strip()[:500]}")
    return json.loads(result.stdout)


def chat(messages: list[dict[str, str]], model: str, key: str, max_tokens: int = 2200) -> str:
    response = request_json(
        "/v1/chat/completions",
        {
            "model": model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": 0.8,
            "max_tokens": max_tokens,
        },
        key,
    )
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        die(f"Unexpected text response: {json.dumps(response)[:500]}")
    if not isinstance(content, str) or not content.strip():
        die("The text model returned an empty answer")
    return content


def extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object found")
    return json.loads(cleaned[start : end + 1])


def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9']+", text))


def plan_prompt(brief: str, scene_count: int, title: str | None, style: str | None) -> list[dict[str, str]]:
    system = f"""You are a documentary writer for a {scene_count * 10}-second illustrated mini-documentary.
Write tight, factual, visually concrete narration in English. Return only a JSON object, no markdown.

Schema:
{{"title":"...","subtitle":"...","style":"...","scenes":[{{"caption":"...","narration":"...","image_prompt":"..."}}]}}

Rules:
- exactly {scene_count} scenes, in narrative order, following the beats in the brief.
- title: 2-7 words. subtitle: one short line, max 14 words.
- caption: the on-screen line, max 90 characters, sentence case, no period at the end.
- narration: 18-30 words, present tense, plain language, no stage directions, no quotes.
- image_prompt: one concrete visual scene, 20-45 words, describe subject, setting, light and composition.
  Do not mention text, letters, diagrams, labels, maps or logos.
- style: a single reusable art-direction string, 15-30 words, no artist names.
- Keep the same visual style across all scenes.
- Do not invent statistics, dates or quotes. If the brief is vague, choose a clear factual angle."""
    user = brief.strip()
    if title:
        user += f"\nUse this exact title: {title}"
    if style:
        user += f"\nUse this exact art direction: {style}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def validate_plan(plan: dict[str, Any], scene_count: int) -> list[str]:
    errors: list[str] = []
    for field in ("title", "subtitle", "style", "scenes"):
        if field not in plan:
            errors.append(f"missing field: {field}")
    if errors:
        return errors
    if not isinstance(plan["title"], str) or not (2 <= len(plan["title"].strip()) <= 90):
        errors.append("title must be a string of 2-90 characters")
    if not isinstance(plan["subtitle"], str) or len(plan["subtitle"].strip()) > 160:
        errors.append("subtitle must be a string of at most 160 characters")
    if not isinstance(plan["style"], str) or not (10 <= len(plan["style"].strip()) <= 400):
        errors.append("style must be a string of 10-400 characters")
    scenes = plan.get("scenes")
    if not isinstance(scenes, list):
        return errors + ["scenes must be an array"]
    if len(scenes) != scene_count:
        errors.append(f"scenes must contain exactly {scene_count} items, got {len(scenes)}")
    for index, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict):
            errors.append(f"scene {index} must be an object")
            continue
        for field in ("caption", "narration", "image_prompt"):
            if not isinstance(scene.get(field), str) or not scene[field].strip():
                errors.append(f"scene {index} is missing {field}")
        if isinstance(scene.get("caption"), str) and len(scene["caption"].strip()) > 120:
            errors.append(f"scene {index} caption is longer than 120 characters")
        if isinstance(scene.get("narration"), str):
            words = word_count(scene["narration"])
            if not (12 <= words <= 45):
                errors.append(f"scene {index} narration has {words} words; keep it between 12 and 45")
        if isinstance(scene.get("image_prompt"), str) and len(scene["image_prompt"].strip()) < 15:
            errors.append(f"scene {index} image_prompt is too short")
    return errors


def normalize_plan(plan: dict[str, Any], scene_count: int) -> dict[str, Any]:
    scenes = []
    for scene in plan["scenes"][:scene_count]:
        scenes.append(
            {
                "caption": re.sub(r"\s+", " ", scene["caption"]).strip(),
                "narration": re.sub(r"\s+", " ", scene["narration"]).strip(),
                "image_prompt": re.sub(r"\s+", " ", scene["image_prompt"]).strip(),
            }
        )
    return {
        "title": re.sub(r"\s+", " ", plan["title"]).strip(),
        "subtitle": re.sub(r"\s+", " ", plan["subtitle"]).strip(),
        "style": re.sub(r"\s+", " ", plan["style"]).strip(),
        "scenes": scenes,
    }


def make_project(
    brief: str,
    out: Path,
    scene_count: int,
    title: str | None,
    style: str | None,
    text_model: str,
    image_model: str,
    speech_model: str,
    voice: str,
    key: str,
) -> dict[str, Any]:
    messages = plan_prompt(brief, scene_count, title, style)
    raw = chat(messages, text_model, key)
    try:
        plan = extract_json(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        die(f"The text model did not return valid JSON: {exc}\n{raw[:500]}")
    errors = validate_plan(plan, scene_count)
    if errors:
        log("  plan needs a repair pass:")
        for error in errors:
            log(f"    - {error}")
        repair_messages = messages + [
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": "Your JSON has these problems:\n- "
                + "\n- ".join(errors)
                + "\nReturn only the corrected JSON object.",
            },
        ]
        raw = chat(repair_messages, text_model, key)
        try:
            plan = extract_json(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            die(f"The repaired plan is not valid JSON: {exc}\n{raw[:500]}")
        errors = validate_plan(plan, scene_count)
        if errors:
            die("The text model could not produce a valid plan:\n- " + "\n- ".join(errors))
    plan = normalize_plan(plan, scene_count)
    project = {
        "version": 1,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "brief": brief.strip(),
        "title": plan["title"],
        "subtitle": plan["subtitle"],
        "style": plan["style"],
        "scenes": plan["scenes"],
        "models": {
            "text": text_model,
            "image": image_model,
            "speech": speech_model,
            "voice": voice,
        },
        "video": {
            "width": WIDTH,
            "height": HEIGHT,
            "fps": FPS,
            "fade": FADE,
            "pad": PAD,
            "image_size": DEFAULT_IMAGE_SIZE,
        },
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "project.json").write_text(json.dumps(project, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return project


def load_project(project_dir: Path) -> dict[str, Any]:
    path = project_dir / "project.json"
    if not path.exists():
        die(f"{path} not found; run the plan command first")
    return json.loads(path.read_text(encoding="utf-8"))


def save_image_bytes(data: bytes, path_stem: Path) -> Path:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        path = path_stem.with_suffix(".png")
    elif data.startswith(b"\xff\xd8\xff"):
        path = path_stem.with_suffix(".jpg")
    else:
        path = path_stem.with_suffix(".jpg")
    path.write_bytes(data)
    return path


def generate_image(
    scene: dict[str, Any],
    index: int,
    project: dict[str, Any],
    key: str,
    image_model: str,
    image_size: str,
    seed: int | None,
    force: bool,
    project_dir: Path,
) -> Path:
    images_dir = project_dir / "images"
    images_dir.mkdir(exist_ok=True)
    existing = sorted(images_dir.glob(f"sc{index:02d}.*"))
    if existing and not force:
        return existing[0]
    style = project["style"].rstrip(". ")
    prompt = (
        f"{scene['image_prompt'].rstrip('. ')}. Style: {style}. "
        "No text, no lettering, no labels, no logos, no watermark."
    )
    payload: dict[str, Any] = {
        "model": image_model,
        "prompt": prompt,
        "size": image_size,
        "n": 1,
        "response_format": "b64_json",
    }
    if seed is not None:
        payload["seed"] = seed + index
    log(f"  [image {index}] {image_model} ...")
    started = time.time()
    response = request_json("/v1/images/generations", payload, key)
    try:
        item = response["data"][0]
    except (KeyError, IndexError, TypeError):
        die(f"Unexpected image response: {json.dumps(response)[:500]}")
    if item.get("b64_json"):
        data = base64.b64decode(item["b64_json"])
    elif item.get("url"):
        download = urllib.request.Request(item["url"], headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(download, timeout=180) as response_stream:
            data = response_stream.read()
    else:
        die(f"Image response has no b64_json or url: {json.dumps(item)[:300]}")
    path = save_image_bytes(data, images_dir / f"sc{index:02d}")
    (images_dir / f"sc{index:02d}.prompt.txt").write_text(prompt + "\n", encoding="utf-8")
    log(f"  [image {index}] {path.name}, {len(data) // 1024} KiB, {time.time() - started:.1f}s")
    return path


def generate_speech(
    scene: dict[str, Any],
    index: int,
    key: str,
    speech_model: str,
    voice: str,
    force: bool,
    project_dir: Path,
) -> Path:
    audio_dir = project_dir / "audio"
    audio_dir.mkdir(exist_ok=True)
    path = audio_dir / f"sc{index:02d}.mp3"
    if path.exists() and path.stat().st_size > 1024 and not force:
        return path
    log(f"  [speech {index}] {speech_model} / {voice} ...")
    started = time.time()
    data = request_bytes(
        "/v1/audio/speech",
        {
            "model": speech_model,
            "input": scene["narration"],
            "voice": voice,
            "response_format": "mp3",
        },
        key,
    )
    path.write_bytes(data)
    log(f"  [speech {index}] {len(data) // 1024} KiB, {time.time() - started:.1f}s")
    return path


def build_clip(
    image: Path,
    clip: Path,
    duration: float,
    index: int,
    ffmpeg: str,
    project_dir: Path,
    force: bool,
) -> None:
    if clip.exists() and clip.stat().st_size > 2048 and not force:
        return
    frames = max(1, int(round(duration * FPS)))
    if index % 2 == 0:
        zoom = f"1+0.045*on/{frames}"
    else:
        zoom = f"1.045-0.045*on/{frames}"
    video_filter = (
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},"
        f"zoompan=z='{zoom}':d=1:x='iw/2-(iw/zoom)/2':y='ih/2-(ih/zoom)/2':"
        f"s={WIDTH}x{HEIGHT}:fps={FPS},format=yuv420p"
    )
    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-loop",
        "1",
        "-framerate",
        str(FPS),
        "-i",
        str(image),
        "-t",
        f"{duration:.3f}",
        "-vf",
        video_filter,
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(FPS),
        str(clip),
    ]
    log(f"  [clip {index}] {duration:.2f}s ...")
    started = time.time()
    run_ffmpeg(command, project_dir)
    log(f"  [clip {index}] {time.time() - started:.1f}s")


def ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def wrap_text(text: str, max_chars: int) -> str:
    words = text.replace("\n", " ").split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return "\\N".join(lines)


def ass_escape(text: str) -> str:
    return text.replace("{", "(").replace("}", ")").replace("\n", " ")


def write_ass(project: dict[str, Any], timings: list[dict[str, Any]], project_dir: Path) -> Path:
    total = timings[-1]["end"] if timings else 0.0
    title = ass_escape(wrap_text(project["title"], 24))
    subtitle = ass_escape(wrap_text(project["subtitle"], 52))
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {WIDTH}",
        f"PlayResY: {HEIGHT}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Title,DejaVu Sans,88,&H00FFFFFF,&H000000FF,&H00101010,&H80000000,"
        "-1,0,0,0,100,100,0,0,1,3,0,8,100,100,80,1",
        "Style: Subtitle,DejaVu Sans,38,&H00F0F0F0,&H000000FF,&H00101010,&H80000000,"
        "-1,0,0,0,100,100,0,0,1,2,0,8,100,100,180,1",
        "Style: Caption,DejaVu Sans,44,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
        "-1,0,0,0,100,100,0,0,3,14,0,2,80,80,64,1",
        "Style: Credit,DejaVu Sans,26,&H00D8D8D8,&H000000FF,&H00101010,&H80000000,"
        "-1,0,0,0,100,100,0,0,1,1,0,8,100,100,32,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        f"Dialogue: 0,{ass_time(0.2)},{ass_time(min(5.0, total))},Title,,0,0,0,,{title}",
        f"Dialogue: 0,{ass_time(0.7)},{ass_time(min(5.0, total))},Subtitle,,0,0,0,,{subtitle}",
    ]
    for timing in timings:
        text = ass_escape(wrap_text(timing["caption"], 56))
        start = timing["start"] + 0.2
        end = max(start + 1.0, timing["end"] - 0.2)
        lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Caption,,0,0,0,,{text}")
    if total > 8:
        lines.append(
            f"Dialogue: 0,{ass_time(max(0.0, total - 3.0))},{ass_time(total)},Credit,,0,0,0,,"
            "Made with Pollinations"
        )
    path = project_dir / "captions.ass"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def render_project(
    project_dir: Path,
    project: dict[str, Any],
    key: str,
    ffmpeg: str,
    ffprobe: str,
    force: bool,
    seed: int | None,
) -> dict[str, Any]:
    images_dir = project_dir / "images"
    audio_dir = project_dir / "audio"
    clips_dir = project_dir / "clips"
    for directory in (images_dir, audio_dir, clips_dir):
        directory.mkdir(exist_ok=True)
    image_model = project["models"]["image"]
    speech_model = project["models"]["speech"]
    voice = project["models"]["voice"]
    image_size = project.get("video", {}).get("image_size", DEFAULT_IMAGE_SIZE)
    timings: list[dict[str, Any]] = []
    cursor = 0.0
    log(f"Generating {len(project['scenes'])} scenes...")
    for index, scene in enumerate(project["scenes"], 1):
        image = generate_image(
            scene, index, project, key, image_model, image_size, seed, force, project_dir
        )
        audio = generate_speech(scene, index, key, speech_model, voice, force, project_dir)
        audio_duration = probe_duration(ffprobe, audio)
        duration = max(MIN_SCENE, audio_duration + PAD)
        timings.append(
            {
                "index": index,
                "caption": scene["caption"],
                "narration": scene["narration"],
                "image": image.name,
                "audio": audio.name,
                "audio_duration": round(audio_duration, 3),
                "duration": round(duration, 3),
                "start": round(cursor, 3),
                "end": round(cursor + duration, 3),
            }
        )
        log(f"  [scene {index}] narration {audio_duration:.2f}s -> scene {duration:.2f}s")
        cursor += duration
    total = round(cursor, 3)
    (project_dir / "timings.json").write_text(
        json.dumps({"total": total, "scenes": timings}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_ass(project, timings, project_dir)
    log(f"Building {len(timings)} clips (total {total:.1f}s)...")
    clips: list[Path] = []
    for index, timing in enumerate(timings, 1):
        clip_duration = timing["duration"] + (FADE if index < len(timings) else 0.0)
        clip = clips_dir / f"sc{index:02d}.mp4"
        build_clip(
            images_dir / timing["image"],
            clip,
            clip_duration,
            index,
            ffmpeg,
            project_dir,
            force,
        )
        clips.append(clip)
    log("Assembling video and audio...")
    inputs: list[str] = []
    for clip in clips:
        inputs.extend(["-i", str(clip.relative_to(project_dir))])
    for timing in timings:
        inputs.extend(["-i", str(Path("audio") / timing["audio"])])
    filters: list[str] = []
    for index in range(len(timings)):
        filters.append(
            f"[{index}:v]settb=AVTB,setpts=PTS-STARTPTS,fps={FPS},format=yuv420p[v{index}]"
        )
    previous = "v0"
    for index in range(1, len(timings)):
        output = f"x{index}"
        offset = sum(item["duration"] for item in timings[:index])
        filters.append(
            f"[{previous}][v{index}]xfade=transition=fade:duration={FADE}:offset={offset:.3f}[{output}]"
        )
        previous = output
    filters.append(f"[{previous}]ass=captions.ass[vout]")
    for index, timing in enumerate(timings):
        filters.append(
            f"[{len(timings) + index}:a]aresample=48000,"
            f"aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"apad=pad_dur=1,atrim=0:{timing['duration']:.3f},asetpts=PTS-STARTPTS[a{index}]"
        )
    filters.append("".join(f"[a{index}]" for index in range(len(timings))) + f"concat=n={len(timings)}:v=0:a=1[aout]")
    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        *inputs,
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        "-t",
        f"{total:.3f}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(FPS),
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-ac",
        "2",
        "-shortest",
        "-movflags",
        "+faststart",
        "-metadata",
        f"title={project['title']}",
        "reel.mp4",
    ]
    started = time.time()
    run_ffmpeg(command, project_dir)
    log(f"  [mux] {time.time() - started:.1f}s")
    poster_command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        "1.2",
        "-i",
        "reel.mp4",
        "-frames:v",
        "1",
        "-q:v",
        "2",
        "poster.jpg",
    ]
    run_ffmpeg(poster_command, project_dir)
    result = {
        "total": total,
        "scenes": timings,
        "video": "reel.mp4",
        "poster": "poster.jpg",
        "captions": "captions.ass",
        "rendered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "size_bytes": (project_dir / "reel.mp4").stat().st_size,
    }
    (project_dir / "render.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def normalize_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def transcribe(
    project_dir: Path, ffmpeg: str, key: str, speech_model: str = "openai/whisper-large-v3"
) -> str:
    audio_path = project_dir / "build" / "transcribe.mp3"
    audio_path.parent.mkdir(exist_ok=True)
    run_ffmpeg(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            "reel.mp4",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "libmp3lame",
            "-q:a",
            "4",
            str(audio_path),
        ],
        project_dir,
    )
    boundary = "----reelwright" + uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in (("model", speech_model), ("language", "en"), ("response_format", "json")):
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
        )
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"audio.mp3\"\r\n"
        "Content-Type: audio/mpeg\r\n\r\n".encode()
    )
    parts.append(audio_path.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)
    request = urllib.request.Request(
        API_BASE + "/v1/audio/transcriptions",
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        die(f"Transcription failed with HTTP {exc.code}: {_http_error_text(exc)}")
    except urllib.error.URLError as exc:
        die(f"Transcription failed: {exc.reason}")
    text = payload.get("text", "")
    if not text:
        die(f"Transcription returned no text: {json.dumps(payload)[:300]}")
    return text.strip()


def check_project(
    project_dir: Path,
    ffprobe: str,
    ffmpeg: str | None,
    key: str | None,
    transcribe_audio: bool,
) -> dict[str, Any]:
    failures: list[str] = []
    warnings: list[str] = []
    reel = project_dir / "reel.mp4"
    timings_path = project_dir / "timings.json"
    if not reel.exists():
        die("reel.mp4 not found; run render first")
    if not timings_path.exists():
        die("timings.json not found; run render first")
    timings = json.loads(timings_path.read_text(encoding="utf-8"))
    total = float(timings["total"])
    probe = probe_json(ffprobe, reel)
    video_streams = [stream for stream in probe.get("streams", []) if stream.get("codec_type") == "video"]
    audio_streams = [stream for stream in probe.get("streams", []) if stream.get("codec_type") == "audio"]
    if not video_streams:
        failures.append("no video stream")
    else:
        video = video_streams[0]
        if video.get("codec_name") != "h264":
            failures.append(f"video codec is {video.get('codec_name')}, expected h264")
        if int(video.get("width", 0)) != WIDTH or int(video.get("height", 0)) != HEIGHT:
            failures.append(
                f"video is {video.get('width')}x{video.get('height')}, expected {WIDTH}x{HEIGHT}"
            )
        rate = video.get("avg_frame_rate", "0/1")
        try:
            numerator, denominator = rate.split("/")
            fps = float(numerator) / float(denominator or 1)
        except Exception:
            fps = 0.0
        if abs(fps - FPS) > 0.1:
            failures.append(f"frame rate is {rate}, expected {FPS}/1")
    if not audio_streams:
        failures.append("no audio stream")
    else:
        audio = audio_streams[0]
        if audio.get("codec_name") != "aac":
            failures.append(f"audio codec is {audio.get('codec_name')}, expected aac")
    duration = float(probe.get("format", {}).get("duration", 0.0))
    if abs(duration - total) > 0.8:
        failures.append(f"duration is {duration:.2f}s, expected {total:.2f}s")
    size = reel.stat().st_size
    if size < 500_000:
        failures.append(f"reel.mp4 is only {size} bytes")
    for timing in timings["scenes"]:
        if not (project_dir / "images" / timing["image"]).exists():
            failures.append(f"missing image {timing['image']}")
        if not (project_dir / "audio" / timing["audio"]).exists():
            failures.append(f"missing audio {timing['audio']}")
    captions = project_dir / "captions.ass"
    if not captions.exists():
        failures.append("captions.ass is missing")
    else:
        dialogue_count = sum(1 for line in captions.read_text(encoding="utf-8").splitlines() if line.startswith("Dialogue:"))
        expected = len(timings["scenes"]) + 2
        if dialogue_count < expected:
            failures.append(f"captions.ass has {dialogue_count} dialogue lines, expected at least {expected}")
    transcript = None
    coverage = None
    if transcribe_audio:
        if not key:
            warnings.append("transcription requested but no API key was supplied")
        else:
            if not ffmpeg:
                die("ffmpeg is required for transcription")
            log("Transcribing the final audio...")
            transcript = transcribe(project_dir, ffmpeg, key)
            script_words = normalize_words(" ".join(item["narration"] for item in timings["scenes"]))
            heard_words = normalize_words(transcript)
            script_counts: dict[str, int] = {}
            heard_counts: dict[str, int] = {}
            for word in script_words:
                script_counts[word] = script_counts.get(word, 0) + 1
            for word in heard_words:
                heard_counts[word] = heard_counts.get(word, 0) + 1
            matched = sum(min(count, heard_counts.get(word, 0)) for word, count in script_counts.items())
            coverage = matched / max(1, len(script_words))
            if coverage < 0.80:
                failures.append(f"transcript covers only {coverage:.0%} of the script")
            else:
                log(f"  transcript coverage: {coverage:.0%}")
    result = {
        "ok": not failures,
        "duration": round(duration, 3),
        "expected_duration": total,
        "size_bytes": size,
        "video": {
            "codec": video_streams[0].get("codec_name") if video_streams else None,
            "width": video_streams[0].get("width") if video_streams else None,
            "height": video_streams[0].get("height") if video_streams else None,
            "fps": video_streams[0].get("avg_frame_rate") if video_streams else None,
        },
        "audio": {
            "codec": audio_streams[0].get("codec_name") if audio_streams else None,
            "channels": audio_streams[0].get("channels") if audio_streams else None,
        },
        "scenes": len(timings["scenes"]),
        "transcript": transcript,
        "transcript_coverage": round(coverage, 3) if coverage is not None else None,
        "failures": failures,
        "warnings": warnings,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (project_dir / "check.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def print_check(result: dict[str, Any]) -> None:
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["ok"]:
        log("\nOK: video, audio, captions and timing all pass.")
    else:
        log("\nFAILED:")
        for failure in result["failures"]:
            log(f"  - {failure}")
    for warning in result.get("warnings", []):
        log(f"warning: {warning}")


def cmd_plan(args: argparse.Namespace) -> None:
    brief = read_brief(args.brief)
    out = Path(args.out).expanduser()
    project = make_project(
        brief=brief,
        out=out,
        scene_count=args.scenes,
        title=args.title,
        style=args.style,
        text_model=args.text_model,
        image_model=args.image_model,
        speech_model=args.speech_model,
        voice=args.voice,
        key=api_key(args.api_key),
    )
    log(f"\nPlan written to {out / 'project.json'}")
    log(f"Title: {project['title']}")
    log(f"Scenes: {len(project['scenes'])}")
    for scene in project["scenes"]:
        log(f"  - {scene['caption']}")
    log("\nReview the plan, edit project.json if needed, then run render.")


def cmd_render(args: argparse.Namespace) -> None:
    project_dir = Path(args.project).expanduser().resolve()
    project = load_project(project_dir)
    ffmpeg = resolve_tool("ffmpeg", "FFMPEG", "require('ffmpeg-static')")
    ffprobe = resolve_tool("ffprobe", "FFPROBE", "require('ffprobe-static').path")
    result = render_project(
        project_dir,
        project,
        api_key(args.api_key),
        ffmpeg,
        ffprobe,
        args.force,
        args.seed,
    )
    log(f"\nVideo: {project_dir / result['video']} ({result['size_bytes'] / 1_000_000:.1f} MB)")
    log(f"Poster: {project_dir / result['poster']}")


def cmd_check(args: argparse.Namespace) -> None:
    project_dir = Path(args.project).expanduser().resolve()
    ffprobe = resolve_tool("ffprobe", "FFPROBE", "require('ffprobe-static').path")
    ffmpeg = None
    if args.transcribe:
        ffmpeg = resolve_tool("ffmpeg", "FFMPEG", "require('ffmpeg-static')")
    result = check_project(
        project_dir,
        ffprobe,
        ffmpeg,
        api_key(args.api_key) if args.transcribe else None,
        args.transcribe,
    )
    print_check(result)
    if not result["ok"]:
        raise SystemExit(1)


def cmd_make(args: argparse.Namespace) -> None:
    project_dir = Path(args.out).expanduser().resolve()
    brief = read_brief(args.brief)
    key = api_key(args.api_key)
    if not (project_dir / "project.json").exists() or args.replan:
        project = make_project(
            brief=brief,
            out=project_dir,
            scene_count=args.scenes,
            title=args.title,
            style=args.style,
            text_model=args.text_model,
            image_model=args.image_model,
            speech_model=args.speech_model,
            voice=args.voice,
            key=key,
        )
        log(f"Plan: {project_dir / 'project.json'}")
    else:
        project = load_project(project_dir)
    ffmpeg = resolve_tool("ffmpeg", "FFMPEG", "require('ffmpeg-static')")
    ffprobe = resolve_tool("ffprobe", "FFPROBE", "require('ffprobe-static').path")
    result = render_project(project_dir, project, key, ffmpeg, ffprobe, args.force, args.seed)
    log(f"\nVideo: {project_dir / result['video']} ({result['size_bytes'] / 1_000_000:.1f} MB)")
    check = check_project(project_dir, ffprobe, ffmpeg, key, args.transcribe)
    print_check(check)
    if not check["ok"]:
        raise SystemExit(1)


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--api-key", help="Pollinations API key (default: POLLINATIONS_API_KEY)")
    parser.add_argument("--text-model", default=DEFAULT_TEXT_MODEL)
    parser.add_argument("--image-model", default=DEFAULT_IMAGE_MODEL)
    parser.add_argument("--speech-model", default=DEFAULT_SPEECH_MODEL)
    parser.add_argument("--voice", default=DEFAULT_VOICE, help="TTS voice, e.g. onyx, nova, alloy")
    parser.add_argument("--seed", type=int, default=None, help="Base seed for reproducible images")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="reelwright",
        description="Turn a brief into a narrated, captioned mini-documentary with Pollinations.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="write a scene plan with a text model")
    plan.add_argument("--brief", required=True, help="Brief text or a path to a text file")
    plan.add_argument("--out", default="reel", help="Project directory")
    plan.add_argument("--scenes", type=int, default=5)
    plan.add_argument("--title", default=None)
    plan.add_argument("--style", default=None)
    add_common(plan)
    plan.set_defaults(func=cmd_plan)

    render = subparsers.add_parser("render", help="generate images, speech and the MP4")
    render.add_argument("--project", default=".", help="Project directory")
    render.add_argument("--force", action="store_true", help="Regenerate images, speech and clips")
    add_common(render)
    render.set_defaults(func=cmd_render)

    check = subparsers.add_parser("check", help="verify the rendered MP4 with ffprobe")
    check.add_argument("--project", default=".", help="Project directory")
    check.add_argument("--transcribe", action="store_true", help="Also transcribe the audio and compare with the script")
    check.add_argument("--api-key", help=argparse.SUPPRESS)
    check.set_defaults(func=cmd_check)

    make = subparsers.add_parser("make", help="plan + render + check in one command")
    make.add_argument("--brief", required=True, help="Brief text or a path to a text file")
    make.add_argument("--out", default="reel", help="Project directory")
    make.add_argument("--scenes", type=int, default=5)
    make.add_argument("--title", default=None)
    make.add_argument("--style", default=None)
    make.add_argument("--force", action="store_true")
    make.add_argument("--replan", action="store_true", help="Rebuild project.json even if it exists")
    make.add_argument("--transcribe", action="store_true", help="Verify the final audio against the script")
    add_common(make)
    make.set_defaults(func=cmd_make)

    args = parser.parse_args()
    if getattr(args, "scenes", 5) < 3 or getattr(args, "scenes", 5) > 8:
        die("--scenes must be between 3 and 8")
    args.func(args)


if __name__ == "__main__":
    main()
