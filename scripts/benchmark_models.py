#!/usr/bin/env python3
"""Benchmark small local models for the Meshtastic AI bridge.

The harness sends purpose-aligned prompts through the same reply paths the bridge
uses (short factual, procedural safety, retrieved-context Q&A, weather and news
summaries, help) and records two things per model:

* **Speed and footprint** - cold-load time, time to first token, wall-clock
  latency, generated tokens, tokens/second, and peak host memory, so a model can be
  matched to a host's RAM budget.
* **Instruction compliance** - whether the reply was actually usable on the mesh:
  non-empty, inside the word and bullet budget from `agent_prompt.txt`, plain text,
  finished rather than truncated, grounded in the supplied retrieval context, and
  citing its source. A model that fails these is not recommended however fast it
  decodes, because an oversized answer costs airtime and an empty one looks like an
  outage.

Pass `--system-file` to judge models against the real `agent_prompt.txt` rather
than the short built-in prompt.

Only the Python standard library is used, so it runs on the node without the
bridge virtualenv.

Examples:
    python3 benchmark_models.py --models qwen2.5:0.5b qwen3.5:0.8b
    python3 benchmark_models.py --models-file /tmp/models.txt --repeats 2 \
        --system-file agent_prompt.txt --json /tmp/bench.json \
        --markdown /tmp/bench.md
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_BASE_URL = "http://127.0.0.1:11434"
SYSTEM_PROMPT = "You answer over low-bandwidth mesh radio. Be accurate and concise."

WEATHER_CONTEXT = (
    "Retrieved context (source: wttr.in):\n"
    "Location: Boulder, CO\n"
    "Condition: Partly cloudy, 18 C (feels like 17 C)\n"
    "Humidity: 38%, Wind: W 9 km/h, Observed: 2026-09-19 14:00 local\n"
)

WIKI_CONTEXT = (
    "Retrieved context (source: local offline library - WikiMed):\n"
    "Heat exhaustion is a heat-related illness caused by loss of water and salt "
    "through heavy sweating. Warning signs include heavy sweating, weakness, "
    "cold, pale and clammy skin, a fast weak pulse, nausea or vomiting, muscle "
    "cramps, dizziness, headache, and fainting. Move the person to a cooler "
    "place, loosen clothing, apply cool wet cloths, sip water slowly, and seek "
    "medical help if symptoms last longer than one hour, get worse, or the "
    "person vomits. Heat exhaustion can progress to heat stroke, a medical "
    "emergency marked by hot dry skin, confusion, slurred speech, and loss of "
    "consciousness."
)

NEWS_CONTEXT = (
    "Retrieved context (source: Google News RSS):\n"
    "- County crews reopen the Cedar Creek bridge after flood damage\n"
    "- Emergency managers extend the boil-water notice through Friday\n"
    "- Red Cross opens a shelter at the fairgrounds\n"
)

# Limits the bridge and agent_prompt.txt actually enforce. Replies are split at
# REPLY_MAX_CHARS (the Meshtastic chunk size), and the agent prompt requires three
# bullets or fewer, under 90 words, plain text, a finished sentence, and use of the
# supplied context with its source named. `max_words`, `grounding` and
# `require_source` below encode the per-prompt version of those rules, so a model
# can be failed for ignoring instructions instead of only timed for speed.
REPLY_MAX_CHARS = 180
MAX_BULLETS = 3
MAX_WORDS = 90

PROMPTS = (
    {
        "name": "short_factual",
        "user": "Why is the sky blue? Answer in two short sentences.",
        "max_words": 45,
    },
    {
        "name": "safety_steps",
        "user": (
            "Give the steps to purify drinking water in an emergency. "
            "Keep it under 90 words."
        ),
        "max_words": 90,
        "style": "steps",
    },
    {
        "name": "wiki_context",
        "user": (
            WIKI_CONTEXT
            + "\nUsing only the context above, what are the warning signs of heat "
            "exhaustion and what should a helper do first? Keep it under 90 words."
        ),
        "max_words": 90,
        "grounding": [
            "sweat",
            "clammy",
            "nausea",
            "vomit",
            "dizz",
            "faint",
            "cramp",
            "weak pulse",
            "cooler",
            "loosen",
        ],
        "grounding_min": 2,
    },
    {
        "name": "weather_context",
        "user": (
            WEATHER_CONTEXT
            + "\nSummarize this for a mesh radio message under 60 words. Include "
            "location, conditions, temperature, feels-like, humidity, wind, and "
            "the source."
        ),
        "max_words": 60,
        "grounding": ["boulder", "18", "38", "cloudy"],
        "grounding_min": 2,
        "require_source": "wttr",
    },
    {
        "name": "news_context",
        "user": (
            NEWS_CONTEXT
            + "\nSummarize the headlines above for a mesh radio message under 60 "
            "words and say where they came from."
        ),
        "max_words": 60,
        "grounding": ["bridge", "boil", "water", "shelter", "fairground"],
        "grounding_min": 2,
        "require_source": "news",
    },
    {
        "name": "help_reply",
        "user": (
            "What can you do? Reply with three short bullet-style lines, "
            "under 60 words."
        ),
        "max_words": 60,
        "style": "bullets",
    },
)


class ResourceSampler(threading.Thread):
    """Sample host memory and Ollama RSS while a request is in flight."""

    def __init__(self, interval: float = 0.2) -> None:
        super().__init__(daemon=True)
        self.interval = interval
        self.total_kb = 0
        self.min_available_kb = None
        self.max_swap_used_kb = 0
        self.max_ollama_rss_kb = 0
        self._stop_event = threading.Event()

    @staticmethod
    def _meminfo():
        info = {}
        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as handle:
                for line in handle:
                    key, _, rest = line.partition(":")
                    info[key] = int(rest.strip().split()[0])
        except (OSError, ValueError, IndexError):
            pass
        return info

    @staticmethod
    def _ollama_rss_kb():
        rss = 0
        try:
            entries = list(Path("/proc").iterdir())
        except OSError:
            return 0
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                comm = (entry / "comm").read_text(encoding="utf-8").strip()
                # Ollama runs inference in a separate "llama-server" child.
                if not (comm.startswith("ollama") or comm.startswith("llama")):
                    continue
                for line in (entry / "status").read_text(
                    encoding="utf-8"
                ).splitlines():
                    if line.startswith("VmRSS:"):
                        rss += int(line.split()[1])
            except (OSError, ValueError, IndexError):
                continue
        return rss

    def _sample(self):
        info = self._meminfo()
        if info:
            self.total_kb = info.get("MemTotal", self.total_kb)
            available = info.get("MemAvailable")
            if available is not None:
                self.min_available_kb = (
                    available
                    if self.min_available_kb is None
                    else min(self.min_available_kb, available)
                )
            swap_used = info.get("SwapTotal", 0) - info.get("SwapFree", 0)
            self.max_swap_used_kb = max(self.max_swap_used_kb, swap_used)
        self.max_ollama_rss_kb = max(self.max_ollama_rss_kb, self._ollama_rss_kb())

    def run(self):
        while not self._stop_event.is_set():
            self._sample()
            self._stop_event.wait(self.interval)

    def stop(self):
        self._sample()
        self._stop_event.set()
        self.join(timeout=2)
        return {
            "mem_total_kb": self.total_kb,
            "mem_available_min_kb": self.min_available_kb or 0,
            "swap_used_max_kb": self.max_swap_used_kb,
            "ollama_rss_max_kb": self.max_ollama_rss_kb,
        }


def _request(url, payload=None, timeout=30.0):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {} if payload is None else {"Content-Type": "application/json"}
    request = urllib.request.Request(url, data=data, headers=headers)
    return urllib.request.urlopen(request, timeout=timeout)


def list_models(base_url, timeout=20.0):
    """Return ``{model_name: size_bytes}`` for models already installed."""
    try:
        with _request(f"{base_url}/api/tags", None, timeout) as response:
            data = json.load(response)
    except (urllib.error.URLError, OSError, ValueError):
        return {}
    models = {}
    for entry in data.get("models", []):
        name = entry.get("name") or entry.get("model") or ""
        if name:
            models[name] = int(entry.get("size") or 0)
    for name, size in list(models.items()):
        if ":" not in name:
            models.setdefault(name + ":latest", size)
    return models


def unload_model(base_url, model, timeout=120.0):
    """Drop a model so the next run reports a cold load time."""
    try:
        with _request(
            f"{base_url}/api/generate",
            {"model": model, "keep_alive": 0},
            timeout,
        ) as response:
            response.read()
    except (urllib.error.URLError, OSError):
        pass


def unload_all_models(base_url, installed):
    """Unload every installed model so memory reflects a single model only."""
    for name in installed:
        unload_model(base_url, name)
    time.sleep(1.0)


TERMINAL_PUNCT = re.compile(r"[.!?\u2026\"')\]]$")
# A reply cut off by the token cap usually ends on a comma, dash or connector.
# Trailing punctuation matches wherever it sits, but a connector word must not be
# preceded by a word character, "." or "/" - otherwise the "in" of a source such
# as "wttr.in" would look like a dangling connector and every weather reply would
# be reported as truncated.
DANGLING_TAIL = re.compile(
    r"(?:[,;:\-\u2013\u2014]\s*$|(?<![\w./])(?:and|or|but|the|a|an|of|to|in|on|"
    r"with|for|that|is|are|was|were|be|as|at|by|from|if|then|than|so|because|"
    r"while|when|which|your|their|its)\s*$)",
    re.IGNORECASE,
)


def score_response(prompt, text, thinking_chars=0, hit_token_cap=False):
    """Score one reply against the limits the bridge and agent prompt enforce.

    Coding a model as usable needs more than throughput: the reply has to arrive
    non-empty, inside the word and bullet budget, as plain text, finished, and
    grounded in whatever retrieval context the bridge supplied. Returns the
    pass/fail decision plus the individual checks so the report can show why a
    model was rejected rather than only how fast it decoded.

    `hit_token_cap` is what separates the two kinds of "did not end with a full
    stop". A reply that ran into the token cap is genuinely truncated and vetoes
    the model. A reply that stopped on its own and merely omitted the final
    period is complete, so it is recorded as the cosmetic `minor` note
    `no_final_stop` instead of a failure.
    """
    text = (text or "").strip()
    lowered = text.lower()
    words = len(text.split())
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    bullets = sum(
        len(re.findall(r"(?:^|\s)[-*\u2022]\s+[A-Za-z]", line))
        + len(re.findall(r"(?:^|\s)\d+[.)]\s+[A-Za-z]", line))
        for line in lines
    )
    has_table = bool(re.search(r"^\s*\|.*\|\s*$", text, re.M))
    has_fence = "```" in text
    dangling = bool(DANGLING_TAIL.search(text))
    terminal = bool(TERMINAL_PUNCT.search(text))
    # Truncation means a dangling tail, or a cap-limited stop with no full stop.
    truncated = bool(text) and (dangling or (hit_token_cap and not terminal))
    hits = None

    failures = {
        "empty": not text,
        "over_word_limit": words > prompt.get("max_words", MAX_WORDS),
        "too_many_bullets": bullets > MAX_BULLETS,
        "markdown": has_table or has_fence,
        "unfinished": truncated,
    }
    grounding = prompt.get("grounding")
    if grounding:
        hits = sum(1 for token in grounding if token.lower() in lowered)
        failures["not_grounded"] = hits < prompt.get("grounding_min", 2)
    source = prompt.get("require_source")
    if source:
        failures["source_not_cited"] = source.lower() not in lowered

    # Cosmetic notes: do not veto the model, but worth reporting.
    minor = []
    if text and not terminal and not truncated:
        minor.append("no_final_stop")
    if bullets > 0 and prompt.get("style") != "bullets" and bullets <= MAX_BULLETS:
        minor.append("used_bullets")

    reasons = sorted(name for name, bad in failures.items() if bad)
    return {
        "comply": not reasons,
        "reasons": reasons,
        "minor": sorted(minor),
        "words": words,
        "bullets": bullets,
        "chunks": (-(-len(text) // REPLY_MAX_CHARS)) if text else 0,
        "grounding_hits": hits,
        "reasoning_leak": thinking_chars > 0,
    }


def run_prompt(base_url, model, prompt, options, timeout, stream=True, think=None,
               system=SYSTEM_PROMPT):
    """Run one chat completion and return timing, token, and memory metrics."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt["user"]},
        ],
        "stream": stream,
        "options": options,
    }
    if think is not None:
        payload["think"] = think
    sampler = ResourceSampler()
    sampler.start()
    started = time.perf_counter()
    ttft = None
    pieces = []
    thinking_pieces = []
    final = {}
    error = None
    try:
        if stream:
            with _request(f"{base_url}/api/chat", payload, timeout) as response:
                for raw in response:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        chunk = json.loads(raw)
                    except ValueError:
                        continue
                    message = chunk.get("message") or {}
                    content = message.get("content") or ""
                    reasoning = message.get("thinking") or ""
                    if reasoning:
                        thinking_pieces.append(reasoning)
                    if content:
                        if ttft is None:
                            ttft = time.perf_counter() - started
                        pieces.append(content)
                    if chunk.get("done"):
                        final = chunk
        else:
            with _request(f"{base_url}/api/chat", payload, timeout) as response:
                chunk = json.load(response)
            final = chunk
            pieces.append((chunk.get("message") or {}).get("content") or "")
            ttft = time.perf_counter() - started
    except (urllib.error.URLError, OSError) as exc:
        error = f"{type(exc).__name__}: {exc}"
    wall = time.perf_counter() - started
    memory = sampler.stop()

    ns = 1_000_000_000
    eval_count = int(final.get("eval_count") or 0)
    eval_duration = int(final.get("eval_duration") or 0)
    prompt_count = int(final.get("prompt_eval_count") or 0)
    prompt_duration = int(final.get("prompt_eval_duration") or 0)
    text = "".join(pieces).strip()
    thinking_chars = len("".join(thinking_pieces))
    result = {
        "prompt": prompt["name"],
        "ok": error is None and bool(text),
        "error": error,
        "ttft_s": round(ttft, 3) if ttft else None,
        "wall_s": round(wall, 3),
        "load_s": round(int(final.get("load_duration") or 0) / ns, 3),
        "total_s": round(int(final.get("total_duration") or 0) / ns, 3),
        "prompt_tokens": prompt_count,
        "output_tokens": eval_count,
        "prompt_tok_s": (
            round(prompt_count / (prompt_duration / ns), 1) if prompt_duration else None
        ),
        "output_tok_s": (
            round(eval_count / (eval_duration / ns), 2) if eval_duration else None
        ),
        "chars": len(text),
        "thinking_chars": thinking_chars,
        "response": text,
        **memory,
    }
    max_tokens = None
    try:
        max_tokens = int((options or {}).get("num_predict"))
    except (TypeError, ValueError):
        max_tokens = None
    cap_hit = bool(max_tokens) and eval_count >= max_tokens
    result.update(score_response(prompt, text, thinking_chars, hit_token_cap=cap_hit))
    result["hit_token_cap"] = cap_hit
    return result


def host_info():
    """Collect a compact host description for the report header."""
    info = {
        "machine": platform.machine(),
        "python": platform.python_version(),
    }
    try:
        with open("/proc/device-tree/model", "rb") as handle:
            info["board"] = handle.read().decode("utf-8", "ignore").strip("\x00 ").strip()
    except OSError:
        pass
    try:
        info["kernel"] = platform.uname().release
    except OSError:
        pass
    meminfo = ResourceSampler._meminfo()
    if meminfo:
        info["mem_total_mb"] = round(meminfo.get("MemTotal", 0) / 1024)
        info["swap_total_mb"] = round(meminfo.get("SwapTotal", 0) / 1024)
    try:
        info["ollama_version"] = (
            subprocess.run(
                ["ollama", "--version"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            ).stdout.strip()
            or "unknown"
        )
    except (OSError, subprocess.SubprocessError):
        info["ollama_version"] = "unknown"
    return info


def _mean(values):
    numbers = [value for value in values if value is not None]
    if not numbers:
        return None
    return round(sum(numbers) / len(numbers), 2)


def verdict(comply_ok, runs):
    """Recommendation gate for one model.

    Obeying the reply contract matters more than throughput: an oversized or
    invented answer costs airtime on a shared channel, and an empty one looks like
    an outage. A model therefore has to comply on every prompt to be recommended.
    """
    if not runs:
        return "unknown"
    rate = comply_ok / runs
    if rate == 1:
        return "recommended"
    if rate >= 0.75:
        return "flaky"
    return "not recommended"


def repeat_seed(base_seed, repeat):
    """Seed to use for one repeat of the prompt set.

    Repeats only mean something if they are independent samples. Passing the same
    seed to every repeat makes the sampler reproduce the same reply byte for byte,
    so N repeats measure a single generation N times over rather than N different
    attempts - which flatters a model that happens to be lucky on the first draw.
    Each repeat therefore shifts the seed, and the seed actually used is recorded
    with every run.
    """
    return base_seed + repeat


def summarise(model, size_bytes, runs, repeats):
    """Collapse all runs for one model into a comparable row."""
    ok_runs = [run for run in runs if run["ok"]]
    errors = sorted({run["error"] for run in runs if run["error"]})
    total_kb = max((run["mem_total_kb"] for run in runs), default=0)
    free_min_kb = min(
        (run["mem_available_min_kb"] for run in runs if run["mem_available_min_kb"]),
        default=0,
    )
    return {
        "model": model,
        "size_bytes": size_bytes,
        "size_gb": round(size_bytes / 1e9, 2) if size_bytes else None,
        "runs": len(runs),
        "ok": len(ok_runs),
        "failed": len(runs) - len(ok_runs),
        "repeats": repeats,
        "load_s": _mean([run["load_s"] for run in ok_runs]),
        "ttft_s": _mean([run["ttft_s"] for run in ok_runs]),
        "wall_s": _mean([run["wall_s"] for run in ok_runs]),
        "output_tokens": _mean([run["output_tokens"] for run in ok_runs]),
        "output_tok_s": _mean([run["output_tok_s"] for run in ok_runs]),
        "prompt_tok_s": _mean([run["prompt_tok_s"] for run in ok_runs]),
        "thinking_chars": _mean([run["thinking_chars"] for run in runs]),
        "ollama_rss_max_mb": round(
            max((run["ollama_rss_max_kb"] for run in runs), default=0) / 1024
        ),
        "mem_available_min_mb": round(free_min_kb / 1024),
        "peak_used_mb": round((total_kb - free_min_kb) / 1024) if total_kb else 0,
        "swap_used_max_mb": round(
            max((run["swap_used_max_kb"] for run in runs), default=0) / 1024
        ),
        "complying": len([run for run in runs if run.get("comply")]),
        "comply_rate": (
            round(len([run for run in runs if run.get("comply")]) / len(runs), 2)
            if runs
            else 0.0
        ),
        "words_avg": _mean([run.get("words") for run in ok_runs]),
        "chunks_avg": _mean([run.get("chunks") for run in ok_runs]),
        "reasons": sorted(
            {reason for run in runs for reason in run.get("reasons", [])}
        ),
        "minor": sorted(
            {note for run in runs for note in run.get("minor", [])}
        ),
        "minor_counts": {
            note: len([run for run in runs if note in run.get("minor", [])])
            for note in sorted({n for run in runs for n in run.get("minor", [])})
        },
        "reasoning_leaks": len([run for run in runs if run.get("reasoning_leak")]),
        "verdict": verdict(len([run for run in runs if run.get("comply")]), len(runs)),
        "errors": errors,
        "details": runs,
    }


def markdown_report(host, summaries, options):
    board = host.get("board") or host.get("machine", "unknown")
    lines = [
        f"**Host:** {board} - "
        f"RAM {host.get('mem_total_mb', '?')} MB, swap "
        f"{host.get('swap_total_mb', '?')} MB, {host.get('ollama_version')}",
        "**Prompt options:** num_predict={num_predict}, temperature={temperature}, "
        "seed={seed}, num_ctx={num_ctx}, think={think}".format(
            num_predict=options.get("num_predict"),
            temperature=options.get("temperature"),
            seed=options.get("seed"),
            num_ctx=options.get("num_ctx", "default"),
            think=options.get("think", "default"),
        ),
        f"**System prompt:** {options.get('system', 'built-in')}",
        "",
        "| Model | Size GB | Load s | TTFT s | Wall s | Out tok | Tok/s | "
        "Think chars | Ollama RSS MB | Free RAM min MB | Swap MB | OK/Total | Fit |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in sorted(summaries, key=lambda item: item["size_bytes"] or 0):
        lines.append(
            "| `{model}` | {size} | {load} | {ttft} | {wall} | {tokens} | "
            "{rate} | {think} | {rss} | {free} | {swap} | {ok}/{runs} | {fit} |".format(
                model=row["model"],
                size=row["size_gb"] if row["size_gb"] is not None else "-",
                load=row["load_s"] if row["load_s"] is not None else "-",
                ttft=row["ttft_s"] if row["ttft_s"] is not None else "-",
                wall=row["wall_s"] if row["wall_s"] is not None else "-",
                tokens=row["output_tokens"] if row["output_tokens"] is not None else "-",
                rate=row["output_tok_s"] if row["output_tok_s"] is not None else "-",
                think=(
                    row["thinking_chars"] if row["thinking_chars"] is not None else "-"
                ),
                rss=row["ollama_rss_max_mb"],
                free=row["mem_available_min_mb"],
                swap=row["swap_used_max_mb"],
                ok=row["ok"],
                runs=row["runs"],
                fit=f"{row['complying']}/{row['runs']}",
            )
        )
    lines.append("")
    lines.append(
        "**Instruction compliance** - the reply contract from `agent_prompt.txt` "
        "(3 bullets or fewer, under 90 words, plain text, finished sentence), plus "
        "grounding in the supplied retrieval context and citation of its source. "
        f"`Pkts` is how many {REPLY_MAX_CHARS}-character Meshtastic chunks the reply "
        "would need. Verdict: `recommended` = complied on every prompt, `flaky` = "
        "75-99%, `not recommended` = below 75%."
    )
    lines.append("")
    lines.append("| Model | Comply | Avg words | Pkts | Verdict | Failures | Minor |")
    lines.append("|---|---:|---:|---:|---|---|---|")
    for row in sorted(summaries, key=lambda item: item["size_bytes"] or 0):
        counts = row.get("minor_counts") or {}
        minor = ", ".join(f"{note} x{counts[note]}" for note in sorted(counts))
        lines.append(
            "| `{model}` | {ok}/{runs} | {words} | {pkts} | {verdict} | {reasons} | {minor} |"
            .format(
                model=row["model"],
                ok=row["complying"],
                runs=row["runs"],
                words=row["words_avg"] if row["words_avg"] is not None else "-",
                pkts=row["chunks_avg"] if row["chunks_avg"] is not None else "-",
                verdict=row["verdict"],
                reasons=", ".join(row["reasons"]) or "-",
                minor=minor or "-",
            )
        )
    for row in summaries:
        if row["errors"]:
            lines.append("")
            lines.append(f"- `{row['model']}` errors: {'; '.join(row['errors'])}")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--models", nargs="+", default=None)
    parser.add_argument("--models-file", default=None)
    parser.add_argument("--prompts", nargs="+", default=None)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--max-tokens", type=int, default=220)
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--num-ctx", type=int, default=None)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--think",
        choices=("default", "true", "false"),
        default="default",
        help="Ollama think flag; 'default' leaves it unset",
    )
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument(
        "--system-file",
        default=None,
        help="Read the system prompt from this file, e.g. agent_prompt.txt",
    )
    parser.add_argument("--json", default=None)
    parser.add_argument("--markdown", default=None)
    parser.add_argument("--no-unload", action="store_true")
    parser.add_argument(
        "--unload-all",
        action="store_true",
        help="Unload every installed model before each model's runs",
    )
    parser.add_argument("--pull-missing", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    models = list(args.models or [])
    if args.models_file:
        with open(args.models_file, "r", encoding="utf-8") as handle:
            models += [
                line.strip()
                for line in handle
                if line.strip() and not line.lstrip().startswith("#")
            ]
    if not models:
        parser.error("provide --models or --models-file")

    prompts = PROMPTS
    if args.prompts:
        wanted = set(args.prompts)
        prompts = tuple(item for item in PROMPTS if item["name"] in wanted)
        if not prompts:
            parser.error("no matching prompt names")

    options = {
        "num_predict": args.max_tokens,
        "temperature": args.temperature,
        "seed": args.seed,
    }
    if args.num_ctx:
        options["num_ctx"] = args.num_ctx
    think = None if args.think == "default" else args.think == "true"
    system = SYSTEM_PROMPT
    system_label = "built-in short mesh prompt"
    if args.system_file:
        system = Path(args.system_file).read_text(encoding="utf-8").strip()
        system_label = f"custom prompt ({len(system)} chars)"
    if args.repeats > 1:
        # Repeats shift the seed, so report the range rather than one number.
        report_seed = f"{args.seed}..{repeat_seed(args.seed, args.repeats - 1)}"
    else:
        report_seed = str(args.seed)
    report_options = dict(
        options, think=args.think, system=system_label, seed=report_seed
    )

    installed = list_models(args.base_url)
    if not installed:
        print(f"ERROR: no Ollama server at {args.base_url}", file=sys.stderr)
        return 2

    host = host_info()
    print(
        f"Host: {host.get('board', host.get('machine'))} - "
        f"RAM {host.get('mem_total_mb')} MB, {host.get('ollama_version')}",
        file=sys.stderr,
    )

    selected = []
    for model in models:
        if model in installed:
            selected.append(model)
        elif args.pull_missing:
            print(f"PULL {model} (not installed)", file=sys.stderr)
            if subprocess.run(["ollama", "pull", model], check=False).returncode == 0:
                installed = list_models(args.base_url)
                if model in installed:
                    selected.append(model)
        else:
            print(f"SKIP {model}: not installed", file=sys.stderr)

    if not selected:
        print("ERROR: no models to benchmark", file=sys.stderr)
        return 2
    if args.dry_run:
        print(f"Would benchmark: {', '.join(selected)}")
        return 0

    summaries = []
    for model in selected:
        runs = []
        for repeat in range(args.repeats):
            run_options = dict(options, seed=repeat_seed(args.seed, repeat))
            if args.unload_all:
                unload_all_models(args.base_url, installed)
            if not args.no_unload:
                unload_model(args.base_url, model)
            for prompt in prompts:
                result = run_prompt(
                    args.base_url,
                    model,
                    prompt,
                    run_options,
                    args.timeout,
                    think=think,
                    system=system,
                )
                result["seed"] = run_options["seed"]
                runs.append(result)
                status = "ok" if result["ok"] else f"FAIL ({result['error']})"
                fit = "comply" if result["comply"] else "BREACH:" + ",".join(result["reasons"])
                print(
                    f"{model} r{repeat + 1} {prompt['name']}: {status} "
                    f"wall={result['wall_s']}s ttft={result['ttft_s']}s "
                    f"tok={result['output_tokens']} "
                    f"tok/s={result['output_tok_s']} "
                    f"words={result['words']} pkts={result['chunks']} {fit}",
                    file=sys.stderr,
                )
        summaries.append(summarise(model, installed.get(model, 0), runs, args.repeats))

    report = markdown_report(host, summaries, report_options)
    print(report)

    payload = {
        "host": host,
        "options": options,
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "summaries": summaries,
    }
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        print(f"JSON written to {args.json}", file=sys.stderr)
    if args.markdown:
        with open(args.markdown, "w", encoding="utf-8") as handle:
            handle.write(report + "\n")
        print(f"Markdown written to {args.markdown}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
