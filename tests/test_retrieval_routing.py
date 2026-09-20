#!/usr/bin/env python3
"""Unit tests for the retrieval adapters that feed the model its context.

The adapters are the bridge's "tools": weather, news, the local reference
search and the optional sensor endpoint. These tests check the routing
(``handles``), the disabled/empty behaviour and the injected local search, and
they stub the two public HTTP calls so nothing touches the network.

Run from the repository root:
    python3 -m unittest discover -s tests -v
Set REQUIRE_BRIDGE_DEPS=1 to fail instead of skip when requests is missing.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
for extra in (str(ROOT), str(ROOT / "bridge")):
    if extra not in sys.path:
        sys.path.insert(0, extra)

RETRIEVAL_IMPORT_ERROR = None
try:
    import retrieval
except Exception as exc:  # pragma: no cover - depends on the environment
    retrieval = None
    RETRIEVAL_IMPORT_ERROR = exc

REQUIRE_DEPS = os.environ.get("REQUIRE_BRIDGE_DEPS") == "1"
DEPS_READY = retrieval is not None
SKIP_REASON = f"retrieval dependencies unavailable: {RETRIEVAL_IMPORT_ERROR}"


def setUpModule():
    """Fail loudly under CI when the bridge dependencies are missing."""
    if not DEPS_READY and REQUIRE_DEPS:
        raise RuntimeError(
            f"REQUIRE_BRIDGE_DEPS=1 but importing retrieval failed: {RETRIEVAL_IMPORT_ERROR}"
        )


def build(enabled=True, weather_location="", news_topic="", local_search=None):
    return retrieval.build_adapters(
        enabled=enabled,
        user_agent="test-agent",
        weather_location=weather_location,
        news_topic=news_topic,
        local_search=local_search or (lambda value: ""),
    )


@unittest.skipUnless(DEPS_READY, SKIP_REASON)
class RoutingTests(unittest.TestCase):
    """A command must reach exactly the adapter that owns it."""

    def setUp(self):
        self.adapters = build()

    def test_every_adapter_is_wired(self):
        self.assertEqual(
            [adapter.name for adapter in self.adapters],
            ["weather", "news", "medical_reference", "sensor"],
        )

    def test_command_routing(self):
        expected = {
            "weather": "weather",
            "news": "news",
            "medical": "medical_reference",
            "health": "medical_reference",
            "firstaid": "medical_reference",
            "reference": "medical_reference",
            "sensor": "sensor",
            "telemetry": "sensor",
        }
        for command, owner in expected.items():
            matched = [a.name for a in self.adapters if a.handles(command)]
            self.assertEqual(matched, [owner], command)

    def test_unknown_command_matches_nothing(self):
        for command in ("wiki", "hello", ""):
            self.assertEqual([a.name for a in self.adapters if a.handles(command)], [], command)


@unittest.skipUnless(DEPS_READY, SKIP_REASON)
class DisabledAndEmptyBehaviourTests(unittest.TestCase):
    """Disabled or unconfigured adapters return empty/guidance, never touch the wire."""

    def test_disabled_weather_returns_empty(self):
        self.assertEqual(retrieval.WeatherAdapter(enabled=False, location="Boulder").retrieve("Boulder"), "")

    def test_weather_without_location_asks_for_one(self):
        adapter = retrieval.WeatherAdapter(enabled=True, location="")
        self.assertIn("No weather location", adapter.retrieve(""))

    def test_weather_uses_default_location_and_reports_source(self):
        adapter = retrieval.WeatherAdapter(enabled=True, location="Boulder")
        payload = {
            "current_condition": [
                {
                    "weatherDesc": [{"value": "Sunny"}],
                    "temp_F": "70",
                    "FeelsLikeF": "70",
                    "humidity": "30",
                    "winddir16Point": "W",
                    "windspeedMiles": "5",
                }
            ]
        }
        response = mock.Mock()
        response.json.return_value = payload
        with mock.patch.object(retrieval.requests, "get", return_value=response) as get:
            text = adapter.retrieve("")
        self.assertIn("SOURCE: wttr.in", text)
        self.assertIn("Location: Boulder", text)
        self.assertIn("70F", text)
        get.assert_called_once()

    def test_disabled_news_returns_empty(self):
        self.assertEqual(retrieval.NewsAdapter(enabled=False, topic="flood").retrieve("flood"), "")

    def test_news_without_topic_asks_for_one(self):
        adapter = retrieval.NewsAdapter(enabled=True, topic="")
        self.assertIn("No news topic", adapter.retrieve(""))

    def test_news_strips_rss_wrappers(self):
        adapter = retrieval.NewsAdapter(enabled=True, topic="flood")
        response = mock.Mock()
        response.text = (
            "<title>Feed title</title><title><![CDATA[Bridge reopened]]></title>"
            "<title>Boil-water notice extended</title>"
        )
        with mock.patch.object(retrieval.requests, "get", return_value=response):
            text = adapter.retrieve("flood")
        self.assertIn("SOURCE: Google News RSS", text)
        self.assertIn("Bridge reopened", text)
        self.assertNotIn("<![CDATA[", text)

    def test_sensor_without_url_reports_it_is_configured_off(self):
        self.assertIn("No local sensor adapter", retrieval.SensorAdapter(url="").retrieve("sensor"))

    def test_sensor_reports_source_line(self):
        adapter = retrieval.SensorAdapter(url="http://127.0.0.1:9000/state")
        response = mock.Mock()
        response.json.return_value = {"temperature_c": 21.5}
        with mock.patch.object(retrieval.requests, "get", return_value=response):
            text = adapter.retrieve("sensor")
        self.assertIn("SOURCE: local sensor adapter", text)
        self.assertIn("temperature_c", text)


@unittest.skipUnless(DEPS_READY, SKIP_REASON)
class MedicalReferenceTests(unittest.TestCase):
    """The reference adapter must use the local index, not the network."""

    def test_empty_value_returns_empty(self):
        adapter = retrieval.MedicalReferenceAdapter(search_local=lambda value: "unused")
        self.assertEqual(adapter.retrieve(""), "")

    def test_delegates_to_the_injected_local_search(self):
        calls = []

        def fake_search(value):
            calls.append(value)
            return "SOURCE: Local Wikipedia index\nTITLE: Heat exhaustion\ntext"

        adapter = retrieval.MedicalReferenceAdapter(search_local=fake_search)
        text = adapter.retrieve("heat exhaustion")
        self.assertEqual(calls, ["heat exhaustion"])
        self.assertIn("TITLE: Heat exhaustion", text)

    def test_build_adapters_passes_the_local_search_through(self):
        adapters = build(enabled=False, local_search=lambda value: "local-result")
        medical = next(a for a in adapters if a.name == "medical_reference")
        self.assertEqual(medical.retrieve("water"), "local-result")


if __name__ == "__main__":
    unittest.main(verbosity=2)
