"""Offline tests for Reelwright's pure logic. No network, no ffmpeg."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "reelwright" / "scripts"))

import reelwright as rw  # noqa: E402


def scene(**overrides):
    data = {
        "caption": "Rain hits granite and starts downhill",
        "narration": "A drop of rain lands on rough granite and gravity pulls it over tiny grooves toward lower ground.",
        "image_prompt": "Granite mountain face under overcast light, rain channels into small rivulets down the slope.",
    }
    data.update(overrides)
    return data


def plan(count=3, **overrides):
    data = {
        "title": "The Salt Road",
        "subtitle": "One drop travels from granite to sea salt",
        "style": "Editorial gouache illustration, muted teal and amber palette, textured paper.",
        "scenes": [scene() for _ in range(count)],
    }
    data.update(overrides)
    return data


class ExtractJsonTest(unittest.TestCase):
    def test_plain_object(self):
        self.assertEqual(rw.extract_json('{"a": 1}'), {"a": 1})

    def test_fenced_block(self):
        raw = '```json\n{"title": "X"}\n```'
        self.assertEqual(rw.extract_json(raw), {"title": "X"})

    def test_prose_around_object(self):
        raw = 'Sure, here is the plan:\n{"title": "X"}\nHope it helps.'
        self.assertEqual(rw.extract_json(raw), {"title": "X"})

    def test_nested_braces(self):
        raw = '{"scenes": [{"caption": "a {b} c"}]}'
        self.assertEqual(rw.extract_json(raw)["scenes"][0]["caption"], "a {b} c")

    def test_no_object_raises(self):
        with self.assertRaises(ValueError):
            rw.extract_json("no json here")


class ValidatePlanTest(unittest.TestCase):
    def test_good_plan_passes(self):
        self.assertEqual(rw.validate_plan(plan(3), 3), [])

    def test_missing_fields(self):
        errors = rw.validate_plan({"title": "X"}, 3)
        self.assertTrue(any("subtitle" in error for error in errors))
        self.assertTrue(any("scenes" in error for error in errors))

    def test_scene_count_mismatch(self):
        errors = rw.validate_plan(plan(3), 5)
        self.assertTrue(any("exactly 5" in error for error in errors))

    def test_short_narration_rejected(self):
        short = scene(narration="Rain falls.")
        errors = rw.validate_plan(plan(3, scenes=[short, scene(), scene()]), 3)
        self.assertTrue(any("narration" in error for error in errors))

    def test_long_caption_rejected(self):
        long = scene(caption="x" * 121)
        errors = rw.validate_plan(plan(3, scenes=[long, scene(), scene()]), 3)
        self.assertTrue(any("caption" in error for error in errors))

    def test_short_image_prompt_rejected(self):
        thin = scene(image_prompt="a rock")
        errors = rw.validate_plan(plan(3, scenes=[thin, scene(), scene()]), 3)
        self.assertTrue(any("image_prompt" in error for error in errors))

    def test_short_style_rejected(self):
        errors = rw.validate_plan(plan(3, style="gouache"), 3)
        self.assertTrue(any("style" in error for error in errors))


class NormalizePlanTest(unittest.TestCase):
    def test_collapses_whitespace(self):
        messy = plan(3, title="  The   Salt\n Road ", scenes=[scene(caption="  a   b  "), scene(), scene()])
        clean = rw.normalize_plan(messy, 3)
        self.assertEqual(clean["title"], "The Salt Road")
        self.assertEqual(clean["scenes"][0]["caption"], "a b")

    def test_truncates_extra_scenes(self):
        clean = rw.normalize_plan(plan(5), 3)
        self.assertEqual(len(clean["scenes"]), 3)

    def test_drops_unknown_scene_keys(self):
        extra = scene(extra="nope")
        clean = rw.normalize_plan(plan(3, scenes=[extra, scene(), scene()]), 3)
        self.assertEqual(set(clean["scenes"][0]), {"caption", "narration", "image_prompt"})


class TimingTest(unittest.TestCase):
    def test_ass_time_formats(self):
        self.assertEqual(rw.ass_time(0), "0:00:00.00")
        self.assertEqual(rw.ass_time(1.5), "0:00:01.50")
        self.assertEqual(rw.ass_time(61.25), "0:01:01.25")
        self.assertEqual(rw.ass_time(3600), "1:00:00.00")

    def test_ass_time_clamps_negative(self):
        self.assertEqual(rw.ass_time(-5), "0:00:00.00")

    def test_ass_time_rounds_within_second(self):
        self.assertEqual(rw.ass_time(9.999), "0:00:10.00")


class WrapTest(unittest.TestCase):
    def test_wraps_long_caption(self):
        text = "A widening river carries its mineral load toward the sea"
        wrapped = rw.wrap_text(text, 24)
        self.assertIn("\\N", wrapped)
        for line in wrapped.split("\\N"):
            self.assertLessEqual(len(line), 24)

    def test_keeps_short_caption_on_one_line(self):
        self.assertEqual(rw.wrap_text("Rain on granite", 24), "Rain on granite")

    def test_long_word_is_not_dropped(self):
        wrapped = rw.wrap_text("supercalifragilisticexpialidocious", 10)
        self.assertEqual(wrapped, "supercalifragilisticexpialidocious")

    def test_ass_escape_neutralises_braces(self):
        self.assertEqual(rw.ass_escape("a {b} c"), "a (b) c")


class WordsTest(unittest.TestCase):
    def test_normalize_words_lowercases_and_strips_punctuation(self):
        self.assertEqual(rw.normalize_words("Rain, hits GRANITE — and runs."), ["rain", "hits", "granite", "and", "runs"])

    def test_word_count(self):
        self.assertEqual(rw.word_count("one two three"), 3)


class CaptionsTest(unittest.TestCase):
    def test_write_ass_has_one_dialogue_per_scene_plus_title_and_credit(self):
        import tempfile

        timings = [
            {"caption": f"Caption {i}", "start": i * 5.0, "end": (i + 1) * 5.0, "duration": 5.0}
            for i in range(3)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = rw.write_ass(plan(3), timings, Path(tmp))
            text = path.read_text(encoding="utf-8")
        dialogues = [line for line in text.splitlines() if line.startswith("Dialogue:")]
        # title, subtitle, 3 captions, credit (credit appears once the reel is > 8 s)
        self.assertEqual(len(dialogues), 6)
        self.assertTrue(any(dialogue.split(",")[3] == "Credit" for dialogue in dialogues))
        self.assertIn("Made with Pollinations", text)
        self.assertIn("PlayResX: 1920", text)


class ProjectFileTest(unittest.TestCase):
    def test_committed_demo_project_is_consistent(self):
        root = Path(__file__).resolve().parents[1]
        project = json.loads((root / "examples" / "salt-road" / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(rw.validate_plan(project, len(project["scenes"])), [])
        timings = json.loads((root / "examples" / "salt-road" / "timings.json").read_text(encoding="utf-8"))
        self.assertEqual(len(timings["scenes"]), len(project["scenes"]))
        cursor = 0.0
        for timing in timings["scenes"]:
            self.assertAlmostEqual(timing["start"], cursor, places=2)
            self.assertGreaterEqual(timing["duration"], rw.MIN_SCENE - 0.001)
            self.assertAlmostEqual(
                timing["duration"], max(rw.MIN_SCENE, timing["audio_duration"] + rw.PAD), places=2
            )
            cursor += timing["duration"]
        self.assertAlmostEqual(timings["total"], cursor, places=2)


if __name__ == "__main__":
    unittest.main()
