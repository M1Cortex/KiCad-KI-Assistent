"""Check that all text files are valid UTF-8 and free of mojibake.

Run from the plugin root:

    python tools/check_encoding.py
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", "dist", "__pycache__", ".venv", "venv"}

# Sequences that appear when UTF-8 text was decoded as CP1252 and re-encoded.
# Built from code points so this file does not flag itself.
MOJIBAKE_MARKERS = tuple(
    chr(cp)
    for cp in (
        0x00C3,  # A-tilde, start of every double-encoded sequence
        0x00C2,  # A-circumflex
        0x00E2,  # a-circumflex
        0xFFFD,  # replacement character
    )
)
SELF = os.path.abspath(__file__)


def check(path: str) -> tuple[bool, str]:
    if os.path.abspath(path) == SELF:
        return True, "ok"
    with open(path, "rb") as handle:
        raw = handle.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return False, f"kein UTF-8 ({exc})"
    for marker in MOJIBAKE_MARKERS:
        if marker in text:
            line = next(
                (i for i, l in enumerate(text.splitlines(), 1) if marker in l),
                0,
            )
            return False, f"Mojibake U+{ord(marker):04X} in Zeile {line}"
    return True, "ok"


def main() -> int:
    failures = 0
    checked = 0
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in sorted(filenames):
            if os.path.splitext(name)[1] not in (".py", ".md", ".json", ".txt", ".ps1"):
                continue
            path = os.path.join(dirpath, name)
            relative = os.path.relpath(path, ROOT)
            ok, detail = check(path)
            checked += 1
            if not ok:
                print(f"  [FEHL] {relative}: {detail}")
                failures += 1

    print(f"Geprueft: {checked} Dateien, {failures} mit Problemen")
    if failures:
        print("Dateien mit Encoding-Fehlern oben aufgefuehrt.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())