"""Simulate KiCad's PCM extraction to verify the package layout.

KiCad extracts the *contents* of every recognised top-level directory into
``<3rdparty>/<dir>/<sanitised_package_id>/``. This script replicates that step
and then checks that the IPC plugin manifest ends up exactly where KiCad looks
for it, so a wrong nesting level is caught before a user ever sees it.

    python tools/check_install_layout.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from schema_validator import load_schema, validate as schema_check  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
SANDBOX = os.path.join(DIST, "layout-check")

# Directories KiCad extracts from a package archive.
PCM_PACKAGE_DIRECTORIES = [
    "plugins", "footprints", "3dmodels", "symbols", "resources",
    "colors", "templates", "scripts",
]


def sanitise(identifier: str) -> str:
    """Mirror KiCad's directory naming for a package identifier."""
    return re.sub(r"[.\-]", "_", identifier)


def simulate(zip_path: str, identifier: str) -> dict[str, str]:
    """Extract like KiCad and report where each top-level file landed."""
    if os.path.isdir(SANDBOX):
        shutil.rmtree(SANDBOX)
    os.makedirs(SANDBOX)

    package_dir = sanitise(identifier)
    landed: dict[str, str] = {}

    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            parts = info.filename.split("/")
            if len(parts) < 2:
                continue
            top = parts[0]
            if top not in PCM_PACKAGE_DIRECTORIES:
                continue
            # Contents of <dir>/ go to <3rdparty>/<dir>/<package_id>/
            relative = "/".join(parts[1:])
            target = os.path.join(SANDBOX, top, package_dir, *parts[1:])
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            landed[relative] = os.path.join(SANDBOX, top, package_dir, relative)

    return landed


def main() -> int:
    zips = [f for f in os.listdir(DIST) if f.endswith(".zip")] if os.path.isdir(DIST) else []
    if not zips:
        print("FEHLER: kein ZIP in dist/ - erst build_package.py ausfuehren")
        return 1
    zip_path = os.path.join(DIST, sorted(zips)[-1])

    with zipfile.ZipFile(zip_path) as zf:
        meta = json.loads(zf.read("metadata.json"))
    identifier = meta["identifier"]

    print(f"Archiv       : {os.path.basename(zip_path)}")
    print(f"Identifier   : {identifier}")
    print(f"Install-Verz.: {sanitise(identifier)}")
    print()

    landed = simulate(zip_path, identifier)
    errors: list[str] = []

    # 1. The IPC manifest must sit directly in the plugin install directory.
    manifest_rel = "plugin.json"
    if manifest_rel in landed:
        print(f"  [OK]   plugin.json landet in {os.path.dirname(landed[manifest_rel])}")
    else:
        errors.append(
            "plugin.json landet NICHT direkt im Installationsverzeichnis - "
            "das Plugin wird nicht geladen. Vermutlich eine Ebene zu tief verschachtelt."
        )
        print("  [FEHL] plugin.json nicht direkt im Installationsverzeichnis")

    # 2. The metadata inside the zip must satisfy KiCad's own schema.
    for err in schema_check(meta, load_schema(os.path.join(ROOT, "tools", "schemas", "pcm.v2.schema.json"))):
        errors.append(f"metadata.json {err}")
        print(f"  [FEHL] {err}")
    else:
        if not errors:
            print("  [OK]   metadata.json erfuellt PCM-Schema v2")

    # 3. No stray nesting: nothing should sit under a plugin-named subfolder.
    for relative in landed:
        if relative.startswith(("org.", "com.", "net.")):
            errors.append(f"unerwartete Verschachtelung: plugins/{relative}")
            print(f"  [FEHL] plugins/{relative} - zu tief verschachtelt")

    # 4. resources must land in the package resources folder.
    icons = [
        rel for rel, full in landed.items()
        if os.path.basename(full).endswith(".png")
        and os.sep + "resources" + os.sep in full
    ]
    print(f"  [OK]   {len(icons)} Ressourcen-Icon(s) in resources/" if icons
          else "  [INFO] keine PNG-Ressource (optional)")

    # 5. Report where the Python package ended up.
    pkg = os.path.join(SANDBOX, "plugins", sanitise(identifier), "kicad_ai")
    print(f"  [{'OK' if os.path.isdir(pkg) else 'FEHL'}]   kicad_ai/ liegt unter "
          f"{os.path.relpath(pkg, SANDBOX) if os.path.isdir(pkg) else pkg}")
    if not os.path.isdir(pkg):
        errors.append("kicad_ai-Paket nicht korrekt installiert")

    shutil.rmtree(SANDBOX, ignore_errors=True)

    print()
    if errors:
        print("Layout-Pruefung fehlgeschlagen:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("Layout ist korrekt - KiCad findet plugin.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())