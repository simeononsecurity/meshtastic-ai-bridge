#!/usr/bin/env python3
"""Unit tests for the benchmark harness's instruction-compliance gate.

The scorer decides whether a model is recommended, so its rules are part of the
project's logic rather than test scaffolding: an over-long or invented answer
must fail even when the decode speed looks good. These tests are pure and need
no Ollama, no model and no network.

Run from the repository root:
    python3 -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import benchmark_models as bm  # noqa: E402  (path set up above)


def prompt(name):
    """Return the prompt definition the harness would send."""
    return next(item for item in bm.PROMPTS if item["name"] == name)


class ScorerTests(unittest.TestCase):
    """One reply at a time: does it satisfy the contract?"""

    def test_grounded_answer_passes(self):
        result = bm.score_response(
            prompt("wiki_context"),
            "Warning signs: heavy sweating, clammy skin, nausea, dizziness and fainting. "
            "Move the person somewhere cooler and loosen clothing.",
        )
        self.assertTrue(result["comply"], result["reasons"])
        self.assertLessEqual(result["chunks"], 2)

    def test_invented_answer_fails_grounding(self):
        result = bm.score_response(prompt("wiki_context"), "Apply a tourniquet and give aspirin.")
        self.assertFalse(result["comply"])
        self.assertIn("not_grounded", result["reasons"])

    def test_empty_reply_fails(self):
        result = bm.score_response(prompt("help_reply"), "")
        self.assertFalse(result["comply"])
        self.assertEqual(result["reasons"], ["empty"])

    def test_over_word_limit_fails(self):
        result = bm.score_response(prompt("short_factual"), " ".join(["word"] * 60) + ".")
        self.assertIn("over_word_limit", result["reasons"])

    def test_markdown_table_fails(self):
        result = bm.score_response(
            prompt("wiki_context"), "| Sign | Action |\n|---|---|\n| Sweating | Cool down |"
        )
        self.assertIn("markdown", result["reasons"])

    def test_too_many_bullets_fail(self):
        text = "\n".join(f"- bullet {index}" for index in range(4))
        self.assertIn("too_many_bullets", bm.score_response(prompt("help_reply"), text)["reasons"])

    def test_three_bullets_without_final_period_is_fine(self):
        text = "- I answer questions\n- I summarize reference material\n- I report weather"
        result = bm.score_response(prompt("help_reply"), text)
        self.assertTrue(result["comply"], result["reasons"])

    def test_truncated_tail_fails(self):
        for text in ("Symptoms include nausea,", "Open the valve and"):
            self.assertIn("unfinished", bm.score_response(prompt("wiki_context"), text)["reasons"])

    def test_source_domain_tail_is_not_mistaken_for_truncation(self):
        """A trailing source domain must not look like a dangling connector.

        Regression: the truncation regex matched the "in" of "wttr.in" because a
        dot counts as a word boundary, so every weather reply was reported as
        cut off mid-sentence.
        """
        result = bm.score_response(
            prompt("weather_context"),
            "Boulder, CO is partly cloudy at 18 C, humidity 38%, wind 9 km/h. "
            "Reported via wttr.in. Source: wttr.in",
        )
        self.assertTrue(result["comply"], result["reasons"])
        self.assertNotIn("unfinished", result["reasons"])
        self.assertIn("no_final_stop", result["minor"])

    def test_cap_limited_stop_without_full_stop_is_truncation(self):
        """Hitting the token cap with no final stop is real truncation."""
        text = "Heat exhaustion causes heavy sweating, clammy skin, nausea and dizz"
        result = bm.score_response(prompt("wiki_context"), text, hit_token_cap=True)
        self.assertIn("unfinished", result["reasons"])

    def test_complete_reply_without_final_stop_is_only_a_note(self):
        text = "Heat exhaustion causes heavy sweating, clammy skin, nausea and dizziness"
        result = bm.score_response(prompt("wiki_context"), text, hit_token_cap=False)
        self.assertNotIn("unfinished", result["reasons"])
        self.assertIn("no_final_stop", result["minor"])

    def test_inline_bullets_are_counted(self):
        """Bullets hidden on one line must still count toward the budget."""
        text = "- one thing. - two things. - three things. - four things."
        self.assertIn("too_many_bullets", bm.score_response(prompt("help_reply"), text)["reasons"])

    def test_missing_source_citation_fails(self):
        result = bm.score_response(
            prompt("weather_context"), "Boulder is partly cloudy at 18 C with 38 percent humidity."
        )
        self.assertIn("source_not_cited", result["reasons"])

    def test_source_citation_passes(self):
        result = bm.score_response(
            prompt("weather_context"),
            "Boulder, CO: partly cloudy, 18 C, humidity 38%, wind W 9 km/h. Source: wttr.in.",
        )
        self.assertTrue(result["comply"], result["reasons"])

    def test_reasoning_leak_is_reported_but_not_a_hard_failure(self):
        result = bm.score_response(
            prompt("wiki_context"),
            "Heavy sweating, clammy skin, nausea and fainting are the warning signs.",
            thinking_chars=900,
        )
        self.assertTrue(result["reasoning_leak"])
        self.assertTrue(result["comply"], result["reasons"])

    def test_repeats_use_independent_seeds(self):
        """Repeats must shift the seed, or they measure one generation N times.

        Regression: every repeat used the same seed, so a model that was lucky on
        its one real sample scored 100% no matter how many times it was repeated.
        """
        self.assertEqual(bm.repeat_seed(42, 0), 42)
        self.assertEqual(bm.repeat_seed(42, 1), 43)
        self.assertEqual(
            len({bm.repeat_seed(42, repeat) for repeat in range(10)}), 10
        )

    def test_packet_count_uses_the_configured_chunk_size(self):
        result = bm.score_response(prompt("wiki_context"), "x" * (bm.REPLY_MAX_CHARS * 2 + 1))
        self.assertEqual(result["chunks"], 3)


class PromptContractTests(unittest.TestCase):
    """Every prompt must declare what a compliant answer looks like."""

    def test_every_prompt_sets_a_word_budget(self):
        for item in bm.PROMPTS:
            self.assertIn("max_words", item, item["name"])
            self.assertLessEqual(item["max_words"], bm.MAX_WORDS)

    def test_context_prompts_require_grounding(self):
        for name in ("wiki_context", "weather_context", "news_context"):
            item = prompt(name)
            self.assertTrue(item.get("grounding"), name)
            self.assertGreaterEqual(item.get("grounding_min", 2), 2)

    def test_public_source_prompts_require_citation(self):
        self.assertEqual(prompt("weather_context")["require_source"], "wttr")
        self.assertEqual(prompt("news_context")["require_source"], "news")

    def test_retrieval_routes_are_exercised(self):
        names = {item["name"] for item in bm.PROMPTS}
        for expected in ("short_factual", "safety_steps", "wiki_context", "weather_context",
                         "news_context", "help_reply"):
            self.assertIn(expected, names)


class VerdictTests(unittest.TestCase):
    """The recommendation thresholds are what the docs promise."""

    def test_perfect_compliance_is_recommended(self):
        self.assertEqual(bm.verdict(6, 6), "recommended")

    def test_single_miss_is_flaky(self):
        self.assertEqual(bm.verdict(15, 18), "flaky")
        self.assertEqual(bm.verdict(3, 4), "flaky")

    def test_repeated_breach_is_not_recommended(self):
        self.assertEqual(bm.verdict(11, 18), "not recommended")
        self.assertEqual(bm.verdict(0, 6), "not recommended")

    def test_no_runs_is_unknown(self):
        self.assertEqual(bm.verdict(0, 0), "unknown")


if __name__ == "__main__":
    unittest.main(verbosity=2)
