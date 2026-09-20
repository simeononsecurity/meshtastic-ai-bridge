#!/usr/bin/env python3
"""Unit tests for the bridge's reply-shaping, gating and classification logic.

These cover the parts of bridge.py that decide what actually goes on the air:
how a long answer is split into Meshtastic-sized chunks, whether a channel
message is answered at all, and how replies and delivery failures are
classified. They need the bridge requirements installed, but no radio, no
Ollama and no network.

Run from the repository root:
    python3 -m unittest discover -s tests -v
Set REQUIRE_BRIDGE_DEPS=1 to fail instead of skip when the deps are missing.
"""

import contextlib
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
for extra in (str(ROOT), str(ROOT / "bridge")):
    if extra not in sys.path:
        sys.path.insert(0, extra)

BRIDGE_IMPORT_ERROR = None
try:
    import bridge
except Exception as exc:  # pragma: no cover - depends on the environment
    bridge = None
    BRIDGE_IMPORT_ERROR = exc

REQUIRE_DEPS = os.environ.get("REQUIRE_BRIDGE_DEPS") == "1"
DEPS_READY = bridge is not None
SKIP_REASON = f"bridge dependencies unavailable: {BRIDGE_IMPORT_ERROR}"


def setUpModule():
    """Fail loudly under CI when the bridge dependencies are missing."""
    if not DEPS_READY and REQUIRE_DEPS:
        raise RuntimeError(f"REQUIRE_BRIDGE_DEPS=1 but importing bridge failed: {BRIDGE_IMPORT_ERROR}")


@contextlib.contextmanager
def patched(**values):
    """Temporarily replace module-level bridge settings."""
    originals = {name: getattr(bridge, name) for name in values}
    for name, value in values.items():
        setattr(bridge, name, value)
    try:
        yield
    finally:
        for name, value in originals.items():
            setattr(bridge, name, value)


class FakeClock:
    """Stand-in for the time module, so no test ever really sleeps."""

    def __init__(self, now=1000.0):
        self.slept = []
        self.now = now

    def sleep(self, seconds):
        self.slept.append(seconds)

    def time(self):
        return self.now


class _AlwaysSet:
    def is_set(self):
        return True


class _Packet:
    def __init__(self, packet_id):
        self.id = packet_id


class FakeInterface:
    """Record what the bridge would transmit, and optionally fail."""

    def __init__(self, fail_data=False):
        self.texts = []
        self.datas = []
        self.fail_data = fail_data
        self.isConnected = _AlwaysSet()

    def sendText(self, text, **kwargs):
        self.texts.append((text, kwargs))
        return _Packet(1000 + len(self.texts))

    def sendData(self, data, **kwargs):
        self.datas.append((data, kwargs))
        if self.fail_data:
            raise RuntimeError("simulated radio failure")
        return _Packet(2000 + len(self.datas))


@unittest.skipUnless(DEPS_READY, SKIP_REASON)
class ReplyChunkingTests(unittest.TestCase):
    """A reply must fit Meshtastic limits without losing or reordering text."""

    def setUp(self):
        self.clock = FakeClock()
        self.interface = FakeInterface()
        self.stack = contextlib.ExitStack()
        self.stack.enter_context(patched(CHUNK_DELAY_SECONDS=0.0))
        self.stack.enter_context(mock.patch.object(bridge, "time", self.clock))
        self.stack.enter_context(mock.patch.object(bridge, "log", lambda *a, **k: None))
        self.addCleanup(self.stack.close)

    def test_empty_reply_transmits_nothing(self):
        bridge.send_chunks(self.interface, "   ")
        self.assertEqual(self.interface.texts, [])
        self.assertEqual(self.interface.datas, [])

    def test_short_reply_is_one_broadcast_without_ack(self):
        bridge.send_chunks(self.interface, "Short answer.")
        self.assertEqual(len(self.interface.texts), 1)
        chunk, kwargs = self.interface.texts[0]
        self.assertEqual(chunk, "Short answer.")
        self.assertFalse(kwargs["wantAck"])
        self.assertEqual(kwargs["destinationId"], bridge.BROADCAST_ADDR)
        self.assertEqual(self.interface.datas, [])

    def test_long_reply_respects_the_character_limit(self):
        text = " ".join(["water purification step"] * 40)
        bridge.send_chunks(self.interface, text)
        self.assertGreater(len(self.interface.texts), 1)
        for chunk, _ in self.interface.texts:
            self.assertLessEqual(len(chunk), bridge.REPLY_MAX_CHARS)

    def test_long_reply_preserves_all_words_in_order(self):
        text = " ".join(f"word{index}" for index in range(120))
        bridge.send_chunks(self.interface, text)
        sent = " ".join(chunk for chunk, _ in self.interface.texts)
        self.assertEqual(sent.split(), text.split())

    def test_split_prefers_sentence_boundaries(self):
        text = "A" * 100 + ". " + "B" * 100
        bridge.send_chunks(self.interface, text)
        self.assertEqual(self.interface.texts[0][0], "A" * 100 + ".")

    def test_direct_reply_uses_reliable_send_with_ack(self):
        bridge.send_chunks(self.interface, "Direct answer.", destination_id=0x1234)
        self.assertEqual(len(self.interface.datas), 1)
        payload, kwargs = self.interface.datas[0]
        self.assertEqual(payload, b"Direct answer.")
        self.assertTrue(kwargs["wantAck"])
        self.assertTrue(kwargs["onResponseAckPermitted"])
        self.assertTrue(callable(kwargs["onResponse"]))
        self.assertEqual(kwargs["destinationId"], 0x1234)

    def test_direct_reply_retries_then_raises(self):
        failing = FakeInterface(fail_data=True)
        with patched(DIRECT_RETRY_COUNT=2, DIRECT_RETRY_DELAY_SECONDS=0.5):
            with self.assertRaises(RuntimeError):
                bridge.send_chunks(failing, "Hello", destination_id=0x1234)
        self.assertEqual(len(failing.datas), 3)
        self.assertEqual(self.clock.slept, [0.5, 0.5])

    def test_broadcast_is_not_retried(self):
        failing = FakeInterface(fail_data=True)
        bridge.send_chunks(failing, "Hello", destination_id=bridge.BROADCAST_ADDR)
        self.assertEqual(len(failing.texts), 1)

    def test_delay_is_applied_once_per_chunk(self):
        with patched(CHUNK_DELAY_SECONDS=1.5):
            bridge.send_chunks(self.interface, " ".join(["filler"] * 80))
        self.assertEqual(len(self.clock.slept), len(self.interface.texts))
        self.assertTrue(all(value == 1.5 for value in self.clock.slept))

    def test_direct_retry_is_counted_in_health(self):
        interface = FakeInterface()
        calls = {"n": 0}

        def flaky(data, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("first attempt fails")
            return _Packet(999)

        interface.sendData = flaky
        before = bridge._health["direct_retries"]
        bridge.send_chunks(interface, "Hello", destination_id=0x1234)
        self.assertEqual(bridge._health["direct_retries"], before + 1)


@unittest.skipUnless(DEPS_READY, SKIP_REASON)
class ChannelPolicyTests(unittest.TestCase):
    """Channel gating decides whether a broadcast is answered at all."""

    def test_allowlist_blocks_unlisted_channels(self):
        with patched(REPLY_CHANNELS={1, 2}):
            self.assertTrue(bridge.channel_allowed(1))
            self.assertFalse(bridge.channel_allowed(4))

    def test_empty_allowlist_permits_any_channel(self):
        with patched(REPLY_CHANNELS=set()):
            self.assertTrue(bridge.channel_allowed(3))

    def test_longfast_channel_zero_is_blocked_by_default(self):
        with patched(REPLY_CHANNELS=set(), CHANNEL_0_NAME="LongFast", REPLY_ON_LONGFAST=False):
            self.assertFalse(bridge.channel_allowed(0))

    def test_longfast_channel_zero_allowed_when_enabled(self):
        with patched(REPLY_CHANNELS=set(), CHANNEL_0_NAME="LongFast", REPLY_ON_LONGFAST=True):
            self.assertTrue(bridge.channel_allowed(0))

    def test_renamed_channel_zero_is_always_allowed(self):
        with patched(REPLY_CHANNELS=set(), CHANNEL_0_NAME="FieldOps", REPLY_ON_LONGFAST=False):
            self.assertTrue(bridge.channel_allowed(0))


@unittest.skipUnless(DEPS_READY, SKIP_REASON)
class LoopSuppressionTests(unittest.TestCase):
    """Other bots, and our own echoes, must not start a reply loop."""

    def setUp(self):
        self.clock = FakeClock(now=5000.0)
        self.stack = contextlib.ExitStack()
        self.stack.enter_context(mock.patch.object(bridge, "time", self.clock))
        self.stack.enter_context(
            patched(BOT_LOOP_MARKERS=("m@i", "~ai"), BOT_LOOP_WINDOW_SECONDS=300)
        )
        self.addCleanup(self.stack.close)
        with bridge._lock:
            bridge._recent_replies.clear()

    def test_other_bot_markers_are_ignored(self):
        self.assertTrue(bridge.is_loop_message("m@i hello there"))
        self.assertTrue(bridge.is_loop_message("~ai status"))

    def test_normal_question_is_not_a_loop(self):
        self.assertFalse(bridge.is_loop_message("What is the weather in Boulder?"))

    def test_recent_reply_echo_is_ignored(self):
        with bridge._lock:
            bridge._recent_replies["short answer."] = self.clock.now
        self.assertTrue(bridge.is_loop_message("Short answer."))

    def test_expired_reply_echo_is_allowed_again(self):
        with bridge._lock:
            bridge._recent_replies["short answer."] = self.clock.now - 400
        self.assertFalse(bridge.is_loop_message("Short answer."))
        with bridge._lock:
            self.assertNotIn("short answer.", bridge._recent_replies)


@unittest.skipUnless(DEPS_READY, SKIP_REASON)
class DeliveryClassificationTests(unittest.TestCase):
    """Radio failures should turn into actionable diagnostics."""

    def test_no_channel_error(self):
        self.assertEqual(
            bridge.classify_delivery_error(RuntimeError("NO_CHANNEL available")),
            "delivery failed: no usable channel or channel key",
        )

    def test_no_route_error(self):
        self.assertIn("no route", bridge.classify_delivery_error(RuntimeError("NO_ROUTE")))

    def test_ack_timeout_error(self):
        self.assertIn(
            "acknowledgement", bridge.classify_delivery_error(RuntimeError("ACK timeout"))
        )

    def test_connection_loss_error(self):
        self.assertIn("connection lost", bridge.classify_delivery_error(RuntimeError("Broken pipe")))

    def test_unknown_error_is_truncated(self):
        message = bridge.classify_delivery_error(RuntimeError("x" * 500))
        self.assertTrue(message.startswith("delivery failed: "))
        self.assertLessEqual(len(message), len("delivery failed: ") + 100)

    def test_response_classification(self):
        self.assertEqual(bridge.classify_delivery_response({"error": "NO_CHANNEL"}), "nack_no_channel")
        self.assertEqual(bridge.classify_delivery_response({"error": "NO_ROUTE"}), "nack_no_route")
        self.assertEqual(bridge.classify_delivery_response({"error": "FAILED"}), "nack")
        self.assertEqual(bridge.classify_delivery_response({"ok": "ACK"}), "acknowledged")
        self.assertEqual(bridge.classify_delivery_response({}), "delivery_response")


@unittest.skipUnless(DEPS_READY, SKIP_REASON)
class CommandAndCompletenessTests(unittest.TestCase):
    """Built-in commands stay deterministic, and truncation is detected."""

    def test_help_is_deterministic_and_offline(self):
        reply = bridge.deterministic_command("help")
        self.assertIn("wiki <topic>", reply)
        self.assertEqual(reply, bridge.deterministic_command("  HELP  "))

    def test_unknown_command_is_empty(self):
        self.assertEqual(bridge.deterministic_command("tell me a joke"), "")

    def test_status_command_reports_state(self):
        with mock.patch.object(bridge, "read_live_model", lambda: "unit-test-model"):
            reply = bridge.deterministic_command("status")
        self.assertIn("unit-test-model", reply)
        self.assertIn("queue", reply)

    def test_complete_sentence_is_accepted(self):
        self.assertFalse(bridge._looks_incomplete("Move the person somewhere cooler."))

    def test_truncated_endings_are_detected(self):
        for text in ("Symptoms include nausea,", "Open the valve and", "see section (", ""):
            self.assertTrue(bridge._looks_incomplete(text), text)

    def test_missing_final_punctuation_is_incomplete(self):
        self.assertTrue(bridge._looks_incomplete("Symptoms include nausea and dizziness"))

    def test_packet_fingerprint_prefers_packet_id(self):
        self.assertEqual(bridge.packet_fingerprint({"id": 7}, "!abc", "hi", 1, 0), ("!abc", 7))

    def test_packet_fingerprint_falls_back_to_content(self):
        self.assertEqual(
            bridge.packet_fingerprint({}, "!abc", "hi", 1, 0), ("!abc", 1, 0, "hi")
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
