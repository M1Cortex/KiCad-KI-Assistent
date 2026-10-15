"""Pack the Plan-B archive (legacy plugin) into a clean, restorable ZIP.

The archive contains the plugin folder named after its identifier, so restoring
is a straight copy into KiCad's plugin directory.

    python tools/pack_plan_b.py
"""

from __future__ import annotations

import os
import shutil
import sys
import zipfile

PLUGIN_ID = "com.github.stani.kicad-ai-assistant"
SOURCE = r"C:\Users\stani\AppData\Local\Temp\opencode\com.github.stani.kicad-ai-assistant-backup"
OUT_DIR = r"C:\Users\stani\Documents\KiCad\Plan-B"
OUT_ZIP = os.path.join(OUT_DIR, f"{PLUGIN_ID}-v0.1.zip")


def main() -> int:
    if not os.path.isdir(SOURCE):
        print(f"FEHLER: Quelle nicht gefunden: {SOURCE}")
        return 1
    if not os.path.isfile(os.path.join(SOURCE, "plugin.json")):
        print("FEHLER: plugin.json fehlt in der Quelle - unvollstaendiges Backup.")
        return 1

    os.makedirs(OUT_DIR, exist_ok=True)
    if os.path.isfile(OUT_ZIP):
        os.remove(OUT_ZIP)

    # Build the archive with the plugin folder as the single root entry so it
    # can be extracted straight into KiCad's plugins directory.
    count = 0
    with zipfile.ZipFile(OUT_ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for dirpath, dirnames, filenames in os.walk(SOURCE):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for name in sorted(filenames):
                if name.endswith((".pyc", ".pyo")):
                    continue
                full = os.path.join(dirpath, name)
                relative = os.path.relpath(full, SOURCE)
                zf.write(full, f"{PLUGIN_ID}/{relative}")
                count += 1

    print(f"Archiv   : {OUT_ZIP}")
    print(f"Dateien  : {count}")
    print(f"Groesse  : {os.path.getsize(OUT_ZIP):,} Bytes")

    # Verify by reading the archive back.
    with zipfile.ZipFile(OUT_ZIP) as zf:
        names = zf.namelist()
        roots = sorted({n.split("/")[0] for n in names})
        bad = zf.testzip()
        has_manifest = f"{PLUGIN_ID}/plugin.json" in names

    print(f"Wurzel   : {roots}")
    print(f"Manifest : {'ok' if has_manifest else 'FEHLT'}")
    print(f"Test     : {'ok' if bad is None else 'FEHLER ' + bad}")
    return 0 if bad is None and has_manifest and len(roots) == 1 else 1


if __name__ == "__main__":
    sys.exit(main())