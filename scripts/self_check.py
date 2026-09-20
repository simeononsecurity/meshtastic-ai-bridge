#!/usr/bin/env python3
"""Run offline repository checks without hardware or secrets."""

import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command):
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        print(f"FAIL: {' '.join(command)}\n{result.stdout}{result.stderr}")
        return False
    print(f"OK: {' '.join(command)}")
    return True


def slug(heading):
    """Convert a Markdown heading into the anchor GitHub generates."""
    text = re.sub(r"[^a-z0-9 \-_]", "", heading.strip().lower())
    return text.replace(" ", "-")


def python_sources():
    """Every project Python file, discovered instead of listed by hand."""
    skip = {".git", "venv", "__pycache__", "node_modules", ".pio-mcp-workspace"}
    return sorted(
        path for path in ROOT.glob("**/*.py") if not skip.intersection(path.parts)
    )


def check_markdown():
    """Validate in-repo links and tables so the docs cannot rot silently."""
    ok = True
    for path in sorted(ROOT.glob("**/*.md")):
        if ".git" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(ROOT)
        headings = {slug(match.group(1)) for match in re.finditer(r"^#{1,6}\s+(.*)$", text, re.M)}
        anchors = set(re.findall(r"\]\(#([a-z0-9\-_]+)\)", text))
        missing = sorted(anchor for anchor in anchors if anchor not in headings)

        broken = []
        for link in set(re.findall(r"\]\((?!https?:|mailto:|#)([^)\s]+)\)", text)):
            target = link.split("#", 1)[0]
            if not target:
                continue
            if not (ROOT / target).exists() and not (path.parent / target).exists():
                broken.append(link)

        uneven = 0
        for block in re.findall(r"(?:^\|.*\n)+", text, re.M):
            rows = block.strip().split("\n")
            if len({len(row.split("|")) - 2 for row in rows}) != 1:
                uneven += 1

        if missing or broken or uneven:
            ok = False
            print(f"FAIL: markdown {relative}: dead anchors={missing} dead links={broken} ragged tables={uneven}")
        else:
            print(f"OK: markdown {relative} ({len(anchors)} anchors, {len(set(re.findall(r'\]\((?!https?:|mailto:)([^)\s]+)\)', text)))} links)")
    return ok


def main():
    ok = True
    for path in python_sources():
        try:
            ast.parse(path.read_text())
            print(f"OK: AST {path.relative_to(ROOT)}")
        except Exception as exc:
            print(f"FAIL: AST {path}: {exc}")
            ok = False

    for path in ROOT.glob("**/*.sh"):
        if ".git" not in path.parts:
            ok = run(["bash", "-n", str(path)]) and ok

    ok = check_markdown() and ok

    example = (ROOT / "bridge" / ".env.example").read_text()
    keys = re.findall(r"^([A-Z][A-Z0-9_]*)=", example, re.MULTILINE)
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        print(f"FAIL: duplicate .env.example keys: {duplicates}")
        ok = False
    else:
        print("OK: .env.example has no duplicate keys")

    if (ROOT / "bridge" / ".env").exists():
        print("OK: private bridge/.env exists and is not part of the check input")
    if "MCP_ENABLED=false" not in example:
        print("FAIL: MCP is not disabled by default")
        ok = False
    else:
        print("OK: MCP disabled by default")

    if shutil_available("docker") and (ROOT / "docker-compose.yml").exists():
        ok = run(["docker", "compose", "-f", "docker-compose.yml", "config", "--quiet"]) and ok
    return 0 if ok else 1


def shutil_available(command):
    return subprocess.run(["sh", "-c", f"command -v {command}"], capture_output=True).returncode == 0


if __name__ == "__main__":
    raise SystemExit(main())