#!/usr/bin/env python3
"""Meshtastic <-> AI bridge.

Connects to the local meshtasticd node over TCP (port 4403) and answers text
messages using any OpenAI-compatible chat API. Direct messages to the node get
an AI reply; broadcast channel messages are ignored unless REPORT_TO_BROADCAST
is enabled in the environment.

Configure via environment (see .env.example). The OpenAI-compatible endpoint
works with OpenAI, Ollama, LM Studio, vLLM, Groq, OpenRouter, and others.
"""

import os
import threading
import time

import requests
from dotenv import load_dotenv
from pubsub import pub
from meshtastic.tcp_interface import TCPInterface

load_dotenv()

BROADCAST_ADDR = 0xFFFFFFFF

MESHTASTIC_HOST = os.environ.get("MESHTASTIC_HOST", "localhost")
MESHTASTIC_PORT = int(os.environ.get("MESHTASTIC_PORT", "4403"))

AI_API_BASE = os.environ.get("AI_API_BASE", "http://127.0.0.1:11434/v1").rstrip("/")
AI_API_KEY = os.environ.get("AI_API_KEY", "ollama")
AI_MODEL = os.environ.get("AI_MODEL", "qwen2.5:1.5b-instruct-q4_K_M")
AI_SYSTEM_PROMPT = os.environ.get(
    "AI_SYSTEM_PROMPT",
    "You answer over low-bandwidth mesh radio. Be accurate and concise.",
)
AI_MAX_TOKENS = int(os.environ.get("AI_MAX_TOKENS", "300"))
AI_TEMPERATURE = float(os.environ.get("AI_TEMPERATURE", "0.7"))

REPLY_TO_BROADCAST = os.environ.get("REPLY_TO_BROADCAST", "false").lower() in (
    "1", "true", "yes", "on",
)
REPLY_MAX_CHARS = int(os.environ.get("REPLY_MAX_CHARS", "180"))

_my_id = None
_inflight = set()
_lock = threading.Lock()


def log(msg):
    print(msg, flush=True)


def ask_ai(prompt):
    """Call an OpenAI-compatible chat completions endpoint."""
    url = f"{AI_API_BASE}/chat/completions"
    headers = {}
    if AI_API_KEY:
        headers["Authorization"] = f"Bearer {AI_API_KEY}"
    payload = {
        "model": AI_MODEL,
        "messages": [
            {"role": "system", "content": AI_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": AI_MAX_TOKENS,
        "temperature": AI_TEMPERATURE,
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()


def send_chunks(interface, text, destination_id=None):
    """Send a text reply, splitting into Meshtastic-sized chunks."""
    text = text.strip()
    if not text:
        return
    while text:
        chunk, text = text[:REPLY_MAX_CHARS], text[REPLY_MAX_CHARS:]
        interface.sendText(chunk, destinationId=destination_id, wantAck=False)
        time.sleep(1)


def ensure_my_id(interface):
    """Resolve our own node id once, so we can ignore our own echoes."""
    global _my_id
    if _my_id:
        return _my_id
    try:
        info = interface.getMyNodeInfo()
        if info and "num" in info:
            _my_id = "!" + format(int(info["num"]), "08x")
    except Exception:
        _my_id = ""
    return _my_id


def on_receive(packet, interface=None):  # pylint: disable=unused-argument
    decoded = packet.get("decoded") or {}
    if decoded.get("portnum") != "TEXT_MESSAGE_APP":
        return
    text = (decoded.get("text") or "").strip()
    if not text:
        return

    sender = packet.get("fromId")
    if not sender:
        return

    if ensure_my_id(interface) and _my_id and sender == _my_id:
        return  # ignore our own echoed replies

    to = packet.get("to")
    if to == BROADCAST_ADDR:
        if not REPLY_TO_BROADCAST:
            return
        destination_id = None  # broadcast to the channel
    else:
        destination_id = sender  # direct message to the node

    with _lock:
        if sender in _inflight:
            return
        _inflight.add(sender)

    try:
        try:
            reply = ask_ai(text)
        except Exception as exc:  # surface API errors over the mesh
            reply = f"AI error: {exc}"
        send_chunks(interface, reply, destination_id)
    finally:
        with _lock:
            _inflight.discard(sender)


def on_connection(interface, topic=pub.AUTO_TOPIC):  # pylint: disable=unused-argument
    ensure_my_id(interface)
    log(f"Connected to meshtasticd at {MESHTASTIC_HOST}:{MESHTASTIC_PORT} (mine={_my_id})")


def main():
    pub.subscribe(on_receive, "meshtastic.receive")
    pub.subscribe(on_connection, "meshtastic.connection.established")

    try:
        interface = TCPInterface(hostname=MESHTASTIC_HOST, portNumber=MESHTASTIC_PORT)
    except TypeError:
        interface = TCPInterface(hostname=MESHTASTIC_HOST)

    log(f"Bridge started. Listening on {MESHTASTIC_HOST}:{MESHTASTIC_PORT}.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        interface.close()


if __name__ == "__main__":
    main()
