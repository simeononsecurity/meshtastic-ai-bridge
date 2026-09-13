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


def main():
    ok = True
    python_files = [
        ROOT / "bridge" / "bridge.py",
        ROOT / "bridge" / "retrieval.py",
        ROOT / "bridge" / "local_wiki.py",
        ROOT / "bridge" / "local_kiwix.py",
        ROOT / "dashboard" / "dashboard.py",
        ROOT / "scripts" / "build_local_wiki_index.py",
    ]
    for path in python_files:
        try:
            ast.parse(path.read_text())
            print(f"OK: AST {path.relative_to(ROOT)}")
        except Exception as exc:
            print(f"FAIL: AST {path}: {exc}")
            ok = False

    for path in ROOT.glob("**/*.sh"):
        if ".git" not in path.parts:
            ok = run(["bash", "-n", str(path)]) and ok

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