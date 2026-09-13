#!/usr/bin/env python3
"""Meshtastic <-> AI bridge.

Answers text messages using any OpenAI-compatible chat API. Direct messages to
the node get an AI reply; broadcast channel messages are ignored unless
REPORT_TO_BROADCAST is enabled in the environment.

Two connection types are supported, selected by MESHTASTIC_CONNECTION:
  - "tcp"    (default) a meshtasticd daemon on the same machine (port 4403)
  - "serial" a standalone Meshtastic node over USB serial (e.g. a RAK4631 /
             RAK4630 / RAK19713 running Meshtastic, exposed as /dev/ttyACM0)

Configure via environment (see .env.example). The OpenAI-compatible endpoint
works with OpenAI, Ollama, LM Studio, vLLM, Groq, OpenRouter, and others.
"""

import json
import os
import queue
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, quote_plus

import requests
from dotenv import load_dotenv
from pubsub import pub
from meshtastic.tcp_interface import TCPInterface
from meshtastic.serial_interface import SerialInterface
from meshtastic.protobuf import portnums_pb2
from local_wiki import search as search_local_wiki
from local_kiwix import search as search_local_kiwix

load_dotenv()

BROADCAST_ADDR = 0xFFFFFFFF

MESHTASTIC_HOST = os.environ.get("MESHTASTIC_HOST", "localhost")
MESHTASTIC_PORT = int(os.environ.get("MESHTASTIC_PORT", "4403"))
MESHTASTIC_CONNECTION = os.environ.get("MESHTASTIC_CONNECTION", "tcp").lower()
MESHTASTIC_SERIAL_PORT = os.environ.get("MESHTASTIC_SERIAL_PORT", "/dev/ttyACM0")

if MESHTASTIC_CONNECTION == "serial":
    CONN_DESC = f"serial node {MESHTASTIC_SERIAL_PORT}"
else:
    CONN_DESC = f"meshtasticd at {MESHTASTIC_HOST}:{MESHTASTIC_PORT}"

AI_API_BASE = os.environ.get("AI_API_BASE", "http://127.0.0.1:11434/v1").rstrip("/")
AI_API_KEY = os.environ.get("AI_API_KEY", "ollama")
AI_MODEL = os.environ.get("AI_MODEL", "qwen3.5:0.8b")
AI_SYSTEM_PROMPT = os.environ.get(
    "AI_SYSTEM_PROMPT",
    "You answer over low-bandwidth mesh radio. Be accurate and concise.",
)
AI_MAX_TOKENS = int(os.environ.get("AI_MAX_TOKENS", "300"))
AI_TEMPERATURE = float(os.environ.get("AI_TEMPERATURE", "0.7"))
AI_QUEUE_MAX = int(os.environ.get("AI_QUEUE_MAX", "8"))
PACKET_DEDUPE_SECONDS = int(os.environ.get("PACKET_DEDUPE_SECONDS", "120"))

REPLY_TO_BROADCAST = os.environ.get("REPLY_TO_BROADCAST", "false").lower() in (
    "1", "true", "yes", "on",
)
REPLY_MAX_CHARS = int(os.environ.get("REPLY_MAX_CHARS", "180"))
BOT_PREFIX = os.environ.get("BOT_PREFIX", "!bot ")
AI_SYSTEM_PROMPT_FILE = os.environ.get(
    "AI_SYSTEM_PROMPT_FILE", "/opt/meshtastic-ai-bridge/agent_prompt.txt"
)
WEB_RETRIEVAL_ENABLED = os.environ.get("WEB_RETRIEVAL_ENABLED", "true").lower() in (
    "1", "true", "yes", "on",
)
LOCAL_WIKI_ENABLED = os.environ.get("LOCAL_WIKI_ENABLED", "true").lower() in (
    "1", "true", "yes", "on",
)
LOCAL_KIWIX_ENABLED = os.environ.get("LOCAL_KIWIX_ENABLED", "true").lower() in (
    "1", "true", "yes", "on",
)
DEFAULT_WEATHER_LOCATION = os.environ.get("DEFAULT_WEATHER_LOCATION", "").strip()
DEFAULT_NEWS_TOPIC = os.environ.get("DEFAULT_NEWS_TOPIC", "").strip()
HTTP_USER_AGENT = os.environ.get(
    "HTTP_USER_AGENT", "Local-Meshtastic-Assistant/1.0 (Meshtastic AI bridge)"
).strip()

LOG_PATH = os.environ.get("RESPONSES_LOG", "/opt/meshtastic-ai-bridge/responses.jsonl")
LIVE_CONFIG_PATH = os.environ.get("LIVE_CONFIG", "/opt/meshtastic-ai-bridge/live_config.json")

_my_id = None
_lock = threading.Lock()
_seen_packets = {}
_mesh_io_lock = threading.RLock()
_interface = None
_control_queue = queue.Queue()
_ai_queue = queue.Queue(maxsize=AI_QUEUE_MAX)
_ai_active = False


def _jsonable(value):
    """Convert library/protobuf values into safe JSON-compatible values."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        if isinstance(value, dict):
            return {str(k): _jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_jsonable(v) for v in value]
        return str(value)


def mesh_snapshot():
    """Return state from the bridge-owned interface; never opens another TCP session."""
    interface = _interface
    if interface is None:
        return {"connected": False, "queue_depth": _control_queue.qsize()}
    with _mesh_io_lock:
        return _jsonable({
            "connected": interface.isConnected.is_set(),
            "my_id": _my_id,
            "my_info": interface.myInfo,
            "metadata": interface.metadata,
            "nodes": interface.nodes or {},
            "channels": getattr(interface, "_localChannels", None) or [],
            "queue_depth": _control_queue.qsize(),
        })


def run_control_job(command):
    """Execute a dashboard mesh request through the one bridge-owned interface."""
    if command == "snapshot":
        return mesh_snapshot()
    if command == "heartbeat":
        with _mesh_io_lock:
            if _interface is None or not _interface.isConnected.is_set():
                raise RuntimeError("Meshtastic interface is not connected")
            _interface.sendHeartbeat()
        return {"ok": True}
    raise ValueError(f"unknown control command: {command}")


def control_worker():
    while True:
        job = _control_queue.get()
        try:
            job["result"] = run_control_job(job["command"])
        except Exception as exc:  # surface the error to the waiting HTTP request
            job["error"] = str(exc)
        finally:
            job["done"].set()
            _control_queue.task_done()


class ControlHandler(BaseHTTPRequestHandler):
    """Small localhost-only API used by the dashboard."""

    def log_message(self, *_args):
        return

    def send_json(self, status, payload):
        body = json.dumps(_jsonable(payload)).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path in ("/health", "/snapshot"):
            job = {"command": "snapshot", "done": threading.Event()}
            _control_queue.put(job)
            if not job["done"].wait(10):
                return self.send_json(504, {"error": "control queue timeout"})
            if job.get("error"):
                return self.send_json(503, {"error": job["error"]})
            return self.send_json(200, job["result"])
        self.send_json(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        command = self.path.rsplit("/", 1)[-1]
        if command not in ("heartbeat",):
            return self.send_json(404, {"error": "not found"})
        job = {"command": command, "done": threading.Event()}
        _control_queue.put(job)
        if not job["done"].wait(10):
            return self.send_json(504, {"error": "control queue timeout"})
        if job.get("error"):
            return self.send_json(503, {"error": job["error"]})
        self.send_json(200, job["result"])


def start_control_server():
    worker = threading.Thread(target=control_worker, name="mesh-control-worker", daemon=True)
    worker.start()
    server = ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("CONTROL_PORT", "8765"))), ControlHandler)
    thread = threading.Thread(target=server.serve_forever, name="mesh-control-api", daemon=True)
    thread.start()
    log("Control API listening on 127.0.0.1:%s" % server.server_port)
    return server


def packet_fingerprint(packet, sender, text, destination, channel_index):
    """Return a stable key for duplicate radio deliveries."""
    packet_id = packet.get("id") or packet.get("rxTime")
    if packet_id:
        return (sender, packet_id)
    return (sender, destination, channel_index, text)


def ai_worker():
    """Process accepted requests sequentially to protect the Pi and radio."""
    global _ai_active
    while True:
        job = _ai_queue.get()
        with _lock:
            _ai_active = True
        try:
            interface = job["interface"]
            sender = job["sender"]
            text = job["text"]
            retrieved = retrieve_context(text)
            prompt = text
            if retrieved:
                prompt = f"User request: {text}\n\nRetrieved context:\n{retrieved}"
            log(f"Asking model {read_live_model()} for {sender} (queue={_ai_queue.qsize()})")
            try:
                reply = ask_ai(prompt)
            except Exception as exc:  # surface API errors over the mesh
                log(f"AI request failed for {sender}: {exc}")
                reply = f"AI error: {exc}"
            log_interaction(sender, text, reply)
            try:
                send_chunks(interface, reply, job["destination"], job["channel"])
            except Exception as exc:
                log(f"Reply send failed for {sender}: {exc}")
        finally:
            with _lock:
                _ai_active = False
            _ai_queue.task_done()


def log(msg):
    print(msg, flush=True)


def system_prompt():
    """Load the agent identity/policy, allowing it to be edited without code changes."""
    try:
        with open(AI_SYSTEM_PROMPT_FILE) as f:
            prompt = f.read().strip()
        if prompt:
            return prompt
    except OSError:
        pass
    return AI_SYSTEM_PROMPT


def retrieve_context(query):
    """Fetch small, source-labeled snippets on explicit wiki/weather/news requests."""
    if not WEB_RETRIEVAL_ENABLED and not LOCAL_WIKI_ENABLED:
        return ""
    normalized = re.sub(r"\s+", " ", query.strip())
    lower = normalized.lower()
    command, _, value = normalized.partition(" ")
    value = value.strip()

    # Support both explicit commands and natural requests over the mesh.
    weather_match = re.search(
        r"(?:weather|forecast|temperature)\s+(?:in|for|at)\s+(.+?)(?:\s+(?:right now|now|rn|today|tonight))?$",
        normalized, re.IGNORECASE,
    )
    if weather_match:
        command, value = "weather", weather_match.group(1).strip()
    elif re.search(r"\b(weather|forecast|temperature)\b", lower):
        command, value = "weather", ""

    if re.search(r"\b(news|headlines|current events)\b", lower):
        command = "news"
        value = re.sub(r"\b(give me|show me|what is|what's|the|some|latest|current|local|news|headlines|current events)\b", " ", normalized, flags=re.IGNORECASE)
        value = re.sub(r"\s+", " ", value).strip()

    wiki_match = re.search(r"(?:wiki|wikipedia)\s+(?:about\s+|on\s+)?(.+)$", normalized, re.IGNORECASE)
    if wiki_match:
        command, value = "wiki", wiki_match.group(1).strip()
    try:
        if command.lower() in ("wiki", "wikipedia") and value:
            if LOCAL_WIKI_ENABLED:
                local_result = search_local_wiki(value)
                if local_result:
                    return local_result
            if LOCAL_KIWIX_ENABLED:
                local_result = search_local_kiwix(value)
                if local_result:
                    return local_result
            search = requests.get(
                "https://en.wikipedia.org/w/rest.php/v1/search/page",
                params={"q": value, "limit": 1}, timeout=8,
            )
            search.raise_for_status()
            pages = search.json().get("pages", [])
            if pages:
                title = pages[0].get("title", value)
                summary = requests.get(
                    f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(title)}",
                    headers={"User-Agent": HTTP_USER_AGENT}, timeout=8,
                )
                summary.raise_for_status()
                data = summary.json()
                return f"SOURCE: Wikipedia\nTITLE: {data.get('title', title)}\n{data.get('extract', '')[:2500]}"
        if command.lower() == "weather":
            location = value or DEFAULT_WEATHER_LOCATION
            if not location:
                return "No weather location configured. Ask with: weather <city or ZIP>."
            weather = requests.get(
                f"https://wttr.in/{quote_plus(location)}",
                params={"format": "3"}, headers={"User-Agent": HTTP_USER_AGENT}, timeout=8,
            )
            weather.raise_for_status()
            return f"SOURCE: wttr.in\n{weather.text.strip()}"
        if command.lower() == "news":
            topic = value or DEFAULT_NEWS_TOPIC
            if not topic:
                return "No news topic configured. Ask with: news <topic>."
            rss = requests.get(
                "https://news.google.com/rss/search",
                params={"q": topic, "hl": "en-US", "gl": "US", "ceid": "US:en"},
                headers={"User-Agent": HTTP_USER_AGENT}, timeout=8,
            )
            rss.raise_for_status()
            titles = re.findall(r"<title><!\[CDATA\[(.*?)\]\]></title>", rss.text)
            if not titles:
                titles = re.findall(r"<title>(.*?)</title>", rss.text)
            return "SOURCE: Google News RSS\n" + "\n".join(titles[1:6])
    except (requests.RequestException, ValueError) as exc:
        return f"RETRIEVAL ERROR: {exc}"
    return ""


def read_live_model():
    """Return the active model, letting the dashboard override it live."""
    try:
        with open(LIVE_CONFIG_PATH) as f:
            cfg = json.load(f)
        if cfg.get("ai_model"):
            return cfg["ai_model"]
    except Exception:
        pass
    return AI_MODEL


def log_interaction(sender, prompt, reply):
    """Append an interaction to the response log for the dashboard."""
    try:
        record = {
            "ts": time.time(),
            "from": sender,
            "prompt": prompt,
            "reply": reply,
            "model": read_live_model(),
        }
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass


def ask_ai(prompt):
    """Call an OpenAI-compatible chat completions endpoint."""
    url = f"{AI_API_BASE}/chat/completions"
    headers = {}
    if AI_API_KEY:
        headers["Authorization"] = f"Bearer {AI_API_KEY}"
    payload = {
        "model": read_live_model(),
        "messages": [
            {"role": "system", "content": system_prompt()},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": AI_MAX_TOKENS,
        "temperature": AI_TEMPERATURE,
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()


def _delivery_callback(destination_id, channel_index, packet_id):
    """Create an ACK/NAK callback for one reliable direct-message chunk."""
    def callback(packet):
        payload = packet or {}
        log(
            f"Delivery response packet={packet_id} destination={destination_id} "
            f"channel={channel_index}: {payload}"
        )
    return callback


def send_chunks(interface, text, destination_id=BROADCAST_ADDR, channel_index=0):
    """Send a text reply, splitting into Meshtastic-sized chunks."""
    text = text.strip()
    if not text:
        return
    while text:
        if len(text) <= REPLY_MAX_CHARS:
            chunk, text = text, ""
        else:
            split_at = text.rfind(" ", 0, REPLY_MAX_CHARS + 1)
            if split_at < max(40, REPLY_MAX_CHARS // 2):
                split_at = REPLY_MAX_CHARS
            chunk, text = text[:split_at].rstrip(), text[split_at:].lstrip()
        direct = destination_id != BROADCAST_ADDR
        packet_ref = {"id": "pending"}

        def delivery_callback(packet):
            payload = packet or {}
            log(
                f"Delivery response packet={packet_ref['id']} destination={destination_id} "
                f"channel={channel_index}: {payload}"
            )

        with _mesh_io_lock:
            if direct:
                packet = interface.sendData(
                    chunk.encode("utf-8"),
                    destinationId=destination_id,
                    portNum=portnums_pb2.PortNum.TEXT_MESSAGE_APP,
                    channelIndex=channel_index,
                    wantAck=True,
                    onResponse=delivery_callback,
                    onResponseAckPermitted=True,
                )
            else:
                packet = interface.sendText(
                    chunk,
                    destinationId=destination_id,
                    channelIndex=channel_index,
                    wantAck=False,
                )
        packet_id = getattr(packet, "id", None)
        packet_ref["id"] = packet_id
        delivery = "reliable direct; awaiting ACK/NAK" if direct else "broadcast; no delivery ACK"
        log(
            f"Queued reply packet={packet_id} destination={destination_id} "
            f"channel={channel_index} chars={len(chunk)} ({delivery})"
        )
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

    log(f"Received text packet from {sender} to {packet.get('to')}: {text[:120]!r}")

    if ensure_my_id(interface) and _my_id and sender == _my_id:
        return  # ignore our own echoed replies

    to = packet.get("to")
    if to == BROADCAST_ADDR:
        if not REPLY_TO_BROADCAST or not text.lower().startswith(BOT_PREFIX.lower()):
            log(f"Ignored channel message: missing prefix {BOT_PREFIX!r}")
            return
        # The library requires the numeric broadcast address. None triggers
        # its CLI-style sys.exit path and silently loses the reply.
        destination_id = BROADCAST_ADDR
        channel_index = int(packet.get("channel", 0) or 0)
        text = text[len(BOT_PREFIX):].strip()
        if not text:
            return
    else:
        destination_id = sender  # direct message to the node
        channel_index = int(packet.get("channel", 0) or 0)

    fingerprint = packet_fingerprint(
        packet, sender, text, destination_id, channel_index,
    )
    now = time.monotonic()
    with _lock:
        _seen_packets.update({key: timestamp for key, timestamp in _seen_packets.items()
                              if now - timestamp < PACKET_DEDUPE_SECONDS})
        if fingerprint in _seen_packets:
            log(f"Ignored duplicate text packet from {sender}")
            return
        _seen_packets[fingerprint] = now

    job = {
        "interface": interface,
        "sender": sender,
        "text": text,
        "destination": destination_id,
        "channel": channel_index,
    }
    with _lock:
        was_busy = _ai_active or not _ai_queue.empty()
    try:
        _ai_queue.put_nowait(job)
    except queue.Full:
        log(f"AI queue full; rejecting request from {sender}")
        try:
            send_chunks(interface, "Busy - queue full; try again.", destination_id, channel_index)
        except Exception as exc:
            log(f"Queue-full notice failed for {sender}: {exc}")
        return

    if was_busy:
        log(f"Queued request from {sender}; queue depth={_ai_queue.qsize()}")
        try:
            send_chunks(interface, "Busy - request queued.", destination_id, channel_index)
        except Exception as exc:
            log(f"Queue notice failed for {sender}: {exc}")


def on_connection(interface, topic=pub.AUTO_TOPIC):  # pylint: disable=unused-argument
    ensure_my_id(interface)
    log(f"Connected to {CONN_DESC} (mine={_my_id})")


def main():
    global _interface
    pub.subscribe(on_receive, "meshtastic.receive")
    pub.subscribe(on_connection, "meshtastic.connection.established")
    threading.Thread(target=ai_worker, name="ai-worker", daemon=True).start()

    if MESHTASTIC_CONNECTION == "serial":
        interface = SerialInterface(devPath=MESHTASTIC_SERIAL_PORT)
    else:
        try:
            interface = TCPInterface(hostname=MESHTASTIC_HOST, portNumber=MESHTASTIC_PORT)
        except TypeError:
            interface = TCPInterface(hostname=MESHTASTIC_HOST)

    _interface = interface
    control_server = start_control_server()
    log(f"Bridge started. Connected to {CONN_DESC}.")
    try:
        last_heartbeat = 0.0
        while True:
            time.sleep(1)
            # Some Wi-Fi nodes close otherwise-idle TCP API sessions before
            # the library's default five-minute heartbeat. Keep this session
            # alive more frequently so incoming mesh packets continue to be
            # delivered to the bridge.
            now = time.monotonic()
            if now - last_heartbeat >= 30 and hasattr(interface, "sendHeartbeat"):
                interface.sendHeartbeat()
                last_heartbeat = now
            # TCPInterface's reader can terminate after a node reboot or Wi-Fi
            # interruption while the Python process remains alive.  Let
            # systemd restart us so the interface is recreated and subscribed
            # to packets again instead of silently running without a reader.
            if hasattr(interface, "isConnected") and not interface.isConnected.is_set():
                raise RuntimeError(f"Meshtastic connection lost: {CONN_DESC}")
    except KeyboardInterrupt:
        pass
    finally:
        control_server.shutdown()
        interface.close()


if __name__ == "__main__":
    main()
