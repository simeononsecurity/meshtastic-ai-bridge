#!/usr/bin/env python3
"""Local web dashboard for the Meshtastic AI bridge.

Runs as root so it can start/stop the systemd services and query meshtasticd and
ollama. Binds 0.0.0.0 by default for LAN access; see DASHBOARD_HOST/DASHBOARD_PORT.
"""

import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.request

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

INSTALL_DIR = os.environ.get("INSTALL_DIR", "/opt/meshtastic-ai-bridge")
ENV_FILE = os.path.join(INSTALL_DIR, ".env")
RESPONSES_LOG = os.path.join(INSTALL_DIR, "responses.jsonl")
LIVE_CONFIG = os.path.join(INSTALL_DIR, "live_config.json")
CONTROL_URL = os.environ.get("BRIDGE_CONTROL_URL", "http://127.0.0.1:8765")

SERVICES = ["meshtasticd", "meshtastic-ai-bridge", "ollama"]
ACTIONS = {"start", "stop", "restart"}


def sh(cmd, timeout=20):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return {"rc": r.returncode, "out": r.stdout, "err": r.stderr}
    except subprocess.TimeoutExpired:
        return {"rc": -1, "out": "", "err": "timeout"}


def env_value(name, default=""):
    try:
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if k == name:
                        return v
    except Exception:
        pass
    return default


def bridge_control(path="/snapshot", method="GET"):
    """Query the bridge-owned control API; never open a second mesh session."""
    req = urllib.request.Request(CONTROL_URL + path, method=method)
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            return json.loads(response.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"connected": False, "error": str(exc)}


def read_live_config():
    try:
        with open(LIVE_CONFIG) as f:
            return json.load(f)
    except Exception:
        return {}


def active_model():
    return read_live_config().get("ai_model") or env_value("AI_MODEL", "qwen2.5:0.5b")


def ollama_models():
    r = sh("ollama list 2>/dev/null")
    models = []
    for line in r["out"].splitlines()[1:]:
        parts = line.split()
        if parts:
            models.append(parts[0])
    return models


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    services = []
    for s in SERVICES:
        r = sh(f"systemctl is-active {s}")
        services.append({"name": s, "state": (r["out"].strip() or "unknown")})
    mesh = bridge_control()
    health = bridge_control("/health")
    return jsonify({
        "services": services,
        "model": active_model(),
        "mesh_reachable": mesh.get("connected", False),
        "mesh": mesh,
        "bot_prefix": env_value("BOT_PREFIX", "!bot"),
        "retrieval_enabled": env_value("WEB_RETRIEVAL_ENABLED", "false"),
        "mcp": {k: health.get(k) for k in ("mcp_enabled", "mcp_allow_write", "mcp_calls", "mcp_denied", "mcp_last_tool", "mcp_last_call_at")},
    })


@app.route("/api/service/<name>/<action>", methods=["POST"])
def api_service(name, action):
    if name not in SERVICES or action not in ACTIONS:
        return jsonify({"ok": False, "error": "bad service or action"}), 400
    r = sh(f"systemctl {action} {name}")
    return jsonify({"ok": r["rc"] == 0, "out": r["out"], "err": r["err"]})


@app.route("/api/responses")
def api_responses():
    limit = min(int(request.args.get("limit", "100")), 1000)
    out = []
    try:
        with open(RESPONSES_LOG) as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return jsonify(out)


@app.route("/api/models")
def api_models():
    return jsonify({"models": ollama_models(), "active": active_model()})


@app.route("/api/model", methods=["POST"])
def api_set_model():
    data = request.get_json(force=True, silent=True) or {}
    model = (data.get("model") or "").strip()
    if not model:
        return jsonify({"ok": False, "error": "model required"}), 400
    cfg = read_live_config()
    cfg["ai_model"] = model
    directory = os.path.dirname(LIVE_CONFIG)
    fd, temporary = tempfile.mkstemp(prefix="live_config.", dir=directory, text=True)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(cfg, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, LIVE_CONFIG)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return jsonify({"ok": True, "model": model})


@app.route("/api/model/pull", methods=["POST"])
def api_pull_model():
    data = request.get_json(force=True, silent=True) or {}
    model = (data.get("model") or "").strip()
    if not model:
        return jsonify({"ok": False, "error": "model required"}), 400
    subprocess.Popen(["ollama", "pull", model], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return jsonify({"ok": True, "pulling": model})


@app.route("/api/mesh")
def api_mesh():
    cmd = request.args.get("cmd", "info")
    if cmd not in ("info", "nodes", "channels"):
        return jsonify({"ok": False, "error": "bad cmd"}), 400
    snapshot = bridge_control()
    if snapshot.get("error"):
        return jsonify({"ok": False, "text": snapshot["error"]}), 503
    if cmd == "nodes":
        data = snapshot.get("nodes", {})
    elif cmd == "channels":
        data = snapshot.get("channels", [])
    else:
        data = snapshot
    return jsonify({"ok": True, "data": data, "text": json.dumps(data, indent=2)})


@app.route("/api/apply", methods=["POST"])
def api_apply():
    """Avoid opening a second Meshtastic client from the dashboard.

    Configuration writes will be routed through the bridge control queue in a
    later endpoint. The old shell script is intentionally not invoked here.
    """
    return jsonify({
        "ok": False,
        "text": "Configuration apply is temporarily disabled to protect the single bridge-owned TCP session.",
    }), 409


if __name__ == "__main__":
    port = int(env_value("DASHBOARD_PORT", "8080") or "8080")
    host = env_value("DASHBOARD_HOST", "127.0.0.1") or "127.0.0.1"
    app.run(host=host, port=port, threaded=True)
