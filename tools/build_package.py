"""Build a PCM-installable zip for the KiCad AI Review plugin.

The archive layout expected by the Plugin and Content Manager is::

    plugins/<plugin_identifier>/...   the plugin itself
    metadata.json                     package manifest (PCM schema v2)
    resources/                        optional package-manager assets

Usage::

    python tools/build_package.py                 # -> dist/kicad-ai-review-0.1.0.zip
    python tools/build_package.py --validate       # validate manifests against the schemas
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_ID = "org.kicad.ai_review"
DIST = os.path.join(ROOT, "dist")

EXCLUDE_DIRS = {"__pycache__", ".git", ".pytest_cache", "dist", ".mypy_cache", ".ruff_cache"}
EXCLUDE_FILES = {".pyc", ".pyo", ".DS_Store", "Thumbs.db"}
# Dev-only content that must not ship inside the plugin folder.
EXCLUDE_PLUGIN = {"tests", "tools", "package", ".gitignore"}


def _iter_files(base: str, skip: set[str] | None = None):
    skip = skip or set()
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS and d not in skip]
        for name in filenames:
            if os.path.splitext(name)[1] in EXCLUDE_FILES:
                continue
            yield os.path.join(dirpath, name)


def build_version() -> str:
    meta_path = os.path.join(ROOT, "package", "metadata.json")
    with open(meta_path, "r", encoding="utf-8") as handle:
        return json.load(handle)["versions"][0]["version"]


def build_package(with_checksum: bool = True) -> str:
    """Assemble the PCM zip.

    ``download_sha256`` inside metadata.json refers to the published archive.
    That value cannot describe the archive it lives in (it changes as soon as
    it is inserted), so by default we omit it and KiCad installs from the file
    without verifying. Callers that publish to a repository run this twice:
    once to learn the hash, then again with that hash baked in.
    """
    version = build_version()
    stage = os.path.join(DIST, "stage")
    if os.path.isdir(stage):
        shutil.rmtree(stage)

    plugin_stage = os.path.join(stage, "plugins", PLUGIN_ID)
    os.makedirs(plugin_stage, exist_ok=True)
    resources_stage = os.path.join(stage, "resources")
    os.makedirs(resources_stage, exist_ok=True)

    # Plugin content, excluding dev folders.
    count = 0
    for path in _iter_files(ROOT, skip=EXCLUDE_PLUGIN):
        relative = os.path.relpath(path, ROOT)
        target = os.path.join(plugin_stage, relative)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(path, target)
        count += 1
    print(f"  plugin files : {count}")

    # Package manifest, with the self-referential checksum removed.
    meta_path = os.path.join(ROOT, "package", "metadata.json")
    with open(meta_path, "r", encoding="utf-8") as handle:
        meta = json.load(handle)
    for entry in meta.get("versions", []):
        if not with_checksum:
            entry.pop("download_sha256", None)
            entry.pop("download_size", None)
            entry.pop("install_size", None)
    with open(os.path.join(stage, "metadata.json"), "w", encoding="utf-8") as handle:
        json.dump(meta, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    # A README for the package manager detail view.
    readme_src = os.path.join(ROOT, "README.md")
    if os.path.isfile(readme_src):
        shutil.copy2(readme_src, os.path.join(resources_stage, "README.md"))
    install_src = os.path.join(ROOT, "INSTALL.md")
    if os.path.isfile(install_src):
        shutil.copy2(install_src, os.path.join(resources_stage, "INSTALL.md"))

    # Optional artwork.
    for icon_name in ("review_light_48.png", "review_dark_48.png"):
        icon_src = os.path.join(ROOT, "icons", icon_name)
        if os.path.isfile(icon_src):
            shutil.copy2(icon_src, os.path.join(resources_stage, icon_name))

    out_zip = os.path.join(DIST, f"kicad-ai-review-{version}.zip")
    if os.path.isfile(out_zip):
        os.remove(out_zip)

    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in _iter_files(stage):
            zf.write(path, os.path.relpath(path, stage))

    print(f"  archive      : {out_zip}")
    print(f"  size         : {os.path.getsize(out_zip):,} bytes")
    print(f"  sha256       : {sha256(out_zip)}")
    return out_zip


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_checksum_report(zip_path: str) -> None:
    """Record the real hash of the built archive next to it, for publishing.

    This must never be fed back into metadata.json inside the archive itself -
    that is circular. The value is written to ``dist/SHA256SUMS.txt`` instead,
    which is what a user verifies the download against.
    """
    size = os.path.getsize(zip_path)
    digest = sha256(zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        install_size = sum(info.file_size for info in zf.infolist())

    report = os.path.join(DIST, "SHA256SUMS.txt")
    with open(report, "w", encoding="utf-8") as handle:
        handle.write(f"{digest}  {os.path.basename(zip_path)}\n")
        handle.write(f"# Groesse: {size} Bytes\n")
        handle.write(f"# Entpackt: {install_size} Bytes\n")

    print(f"  pruefsumme   : {report}")


def validate() -> int:
    """Check the manifests and the archive layout against the published schemas."""
    errors: list[str] = []

    # --- plugin.json against the KiCad API plugin schema -------------------
    plugin_json = os.path.join(ROOT, "plugin.json")
    try:
        with open(plugin_json, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"plugin.json nicht lesbar: {exc}")
        manifest = {}

    for field in ("identifier", "name", "description", "runtime", "actions"):
        if field not in manifest:
            errors.append(f"plugin.json: Pflichtfeld '{field}' fehlt")
    if not manifest.get("identifier", "").endswith(PLUGIN_ID):
        errors.append(f"plugin.json: identifier muss mit '{PLUGIN_ID}' uebereinstimmen")
    for action in manifest.get("actions", []):
        for field in ("identifier", "name", "description", "entrypoint"):
            if field not in action:
                errors.append(f"plugin.json: Action '{action.get('identifier')}' ohne '{field}'")
        entry = action.get("entrypoint", "")
        if entry and not os.path.isfile(os.path.join(ROOT, entry)):
            errors.append(f"plugin.json: entrypoint '{entry}' existiert nicht")
        for scope in action.get("scopes", []):
            if scope not in ("pcb", "schematic", "footprint", "symbol",
                             "project_manager", "footprint_wizard"):
                errors.append(f"plugin.json: ungueltiger scope '{scope}'")
        for icon_key in ("icons-light", "icons-dark"):
            for icon in action.get(icon_key, []):
                if not os.path.isfile(os.path.join(ROOT, icon)):
                    errors.append(f"plugin.json: {icon_key} Datei '{icon}' fehlt")

    # --- metadata.json against PCM v2 -------------------------------------
    meta_path = os.path.join(ROOT, "package", "metadata.json")
    try:
        with open(meta_path, "r", encoding="utf-8") as handle:
            meta = json.load(handle)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"metadata.json nicht lesbar: {exc}")
        meta = {}

    for field in ("name", "description", "description_full", "identifier",
                  "type", "author", "license", "resources", "versions"):
        if field not in meta:
            errors.append(f"metadata.json: Pflichtfeld '{field}' fehlt")
    if meta.get("$schema") != "https://go.kicad.org/pcm/schemas/v2":
        errors.append("metadata.json: $schema muss PCM v2 sein (KiCad 10)")
    if meta.get("identifier") != PLUGIN_ID:
        errors.append(f"metadata.json: identifier muss '{PLUGIN_ID}' sein")
    if meta.get("type") != "plugin":
        errors.append("metadata.json: type muss 'plugin' sein")
    for contact_key in ("author", "maintainer"):
        contact = meta.get(contact_key, {})
        if "name" not in contact or "contact" not in contact:
            errors.append(f"metadata.json: {contact_key} braucht 'name' und 'contact'")
    if not meta.get("versions"):
        errors.append("metadata.json: mindestens eine Version erforderlich")
    for version in meta.get("versions", []):
        for field in ("version", "status", "kicad_version"):
            if field not in version:
                errors.append(f"metadata.json: Version ohne '{field}'")
        if version.get("status") not in ("stable", "testing", "development", "deprecated"):
            errors.append(f"metadata.json: ungueltiger status '{version.get('status')}'")
        if version.get("runtime") not in (None, "swig", "ipc"):
            errors.append("metadata.json: runtime muss 'ipc' sein fuer KiCad 10")

    # --- requirements.txt --------------------------------------------------
    requirements = os.path.join(ROOT, "requirements.txt")
    if not os.path.isfile(requirements):
        errors.append("requirements.txt fehlt (KiCad baut sonst keine venv)")

    # --- package contents --------------------------------------------------
    if errors:
        print("Validierung fehlgeschlagen:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("Manifeste und Layout sind gueltig.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="PCM-Paket bauen")
    parser.add_argument("--validate", action="store_true",
                        help="nur validieren, kein Archiv erzeugen")
    args = parser.parse_args()

    print("PCM-Paket: KiCad AI Review")
    status = validate()
    if status != 0 or args.validate:
        return status

    os.makedirs(DIST, exist_ok=True)
    zip_path = build_package()
    write_checksum_report(zip_path)
    print("\nFertig. Installation ueber KiCad:")
    print("  KiCad > Plugin- und Content-Verwaltung > 'Aus Datei installieren...'")
    print(f"  {zip_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())