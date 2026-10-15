"""Smoke test for the MCP server over stdio.

Runs a scripted JSON-RPC session against kicad_ai.mcp_server and checks that
the advertised tools actually work against a real KiCad project.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PROJECT = sys.argv[1] if len(sys.argv) > 1 else (
    r"C:\Program Files\KiCad\10.0\share\kicad\demos\complex_hierarchy\complex_hierarchy.kicad_sch"
)

REQUESTS = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize",
     "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "smoke", "version": "1"}}},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
     "params": {"name": "get_project_info", "arguments": {"project": PROJECT}}},
    {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
     "params": {"name": "list_nets", "arguments": {"project": PROJECT, "filter": "12V"}}},
    {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
     "params": {"name": "list_symbols", "arguments": {"project": PROJECT, "filter": "U2"}}},
    {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
     "params": {"name": "trace_connection",
                "arguments": {"project": PROJECT, "from": "U201", "to": "P301"}}},
    {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
     "params": {"name": "check_llm_status", "arguments": {}}},
    {"jsonrpc": "2.0", "id": 8, "method": "tools/call",
     "params": {"name": "review_schematic",
                "arguments": {"project": PROJECT, "use_ai": False, "min_severity": "warning"}}},
    {"jsonrpc": "2.0", "id": 9, "method": "shutdown"},
]


def main() -> int:
    payload = "\n".join(json.dumps(r) for r in REQUESTS) + "\n"
    proc = subprocess.run(
        [sys.executable, "-m", "kicad_ai.mcp_server", "--project", PROJECT],
        input=payload,
        capture_output=True,
        text=True,
        cwd=ROOT,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )

    responses = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "id" in data:
            responses[data["id"]] = data

    failures = []
    for request in REQUESTS:
        rid = request["id"]
        name = request.get("params", {}).get("name", request["method"])
        if rid not in responses:
            failures.append(f"id={rid} ({name}): keine Antwort")
            continue
        resp = responses[rid]
        if "error" in resp:
            failures.append(f"id={rid} ({name}): {resp['error']}")
            continue
        result = resp.get("result", {})
        if result.get("isError"):
            text = result.get("content", [{}])[0].get("text", "")
            failures.append(f"id={rid} ({name}): {text}")

    tools = responses.get(2, {}).get("result", {}).get("tools", [])
    print(f"Projekt      : {PROJECT}")
    print(f"Werkzeuge   : {len(tools)}")
    print(f"Antworten    : {len(responses)}/{len(REQUESTS)}")

    review = responses.get(8, {}).get("result", {})
    if not review.get("isError"):
        payload_txt = review.get("content", [{}])[0].get("text", "{}")
        try:
            data = json.loads(payload_txt)
            counts = data.get("counts", {})
            print(f"Befunde     : {counts}")
            print(f"Statistik   : {data.get('statistics')}")
        except json.JSONDecodeError:
            pass

    if failures:
        print("\nFEHLER:")
        for failure in failures:
            print("  -", failure)
        if proc.stderr.strip():
            print("\nstderr:")
            for line in proc.stderr.strip().splitlines()[-15:]:
                print("  ", line)
        return 1

    print("\nAlle MCP-Aufrufe erfolgreich.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())