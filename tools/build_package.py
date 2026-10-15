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
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from schema_validator import load_schema  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_ID = "org.kicad.ai-review"
DIST = os.path.join(ROOT, "dist")

EXCLUDE_DIRS = {"__pycache__", ".git", ".pytest_cache", "dist", ".mypy_cache", ".ruff_cache"}
EXCLUDE_FILES = {".pyc", ".pyo", ".DS_Store", "Thumbs.db"}
# Dev-only content that must not ship inside the plugin folder.
EXCLUDE_PLUGIN = {"tests", "tools", "package"}


def _iter_files(base: str, skip: set[str] | None = None):
    """Yield every file below *base* that belongs in a release archive.

    ``skip`` applies to directories; dotfiles and the EXCLUDE_FILES extensions
    are always dropped because they are repository bookkeeping, not plugin code.
    """
    skip = skip or set()
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS and d not in skip]
        for name in filenames:
            if name.startswith("."):
                continue
            if os.path.splitext(name)[1] in EXCLUDE_FILES:
                continue
            yield os.path.join(dirpath, name)


def build_version() -> str:
    meta_path = os.path.join(ROOT, "package", "metadata.json")
    with open(meta_path, "r", encoding="utf-8") as handle:
        return json.load(handle)["versions"][0]["version"]


def _clean_stage(stage: str) -> None:
    """Remove the staging directory, tolerating files still held open.

    On Windows a leftover process (a killed preview window, an editor indexer)
    can keep a handle on the directory and make ``rmtree`` fail. Retrying after
    a short pause is enough in practice; as a last resort the directory is
    moved aside and rebuilt, which keeps the build going.
    """
    if not os.path.isdir(stage):
        return

    for attempt in range(3):
        try:
            shutil.rmtree(stage)
            return
        except (PermissionError, OSError) as exc:
            if attempt == 2:
                leftover = f"{stage}.old-{os.getpid()}"
                try:
                    os.replace(stage, leftover)
                    print(f"  hinweis   : alter Staging-Ordner umbenannt nach {os.path.basename(leftover)}")
                    print(f"             Ursache: {exc}")
                except OSError:
                    raise
                return
            time.sleep(0.6)


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
    _clean_stage(stage)

    # KiCad extracts the *contents* of each top-level directory into
    # <3rdparty>/<dir>/<sanitised_package_id>/. The plugin must therefore sit
    # directly inside plugins/ - an extra level would end up nested inside the
    # install directory and the IPC manifest would never be found.
    plugin_stage = os.path.join(stage, "plugins")
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


def _schema(name: str) -> dict:
    path = os.path.join(ROOT, "tools", "schemas", name)
    if not os.path.isfile(path):
        raise SystemExit(
            f"Schema nicht gefunden: {path}\n"
            "Die offiziellen KiCad-Schemas liegen unter tools/schemas/."
        )
    return load_schema(path)


def validate() -> int:
    """Validate both manifests against KiCad's published JSON schemas.

    Checking only for required fields is not enough: the schemas also constrain
    identifiers with regular expressions (PCM forbids underscores) and restrict
    enum values, so the full schema is applied here.
    """
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from schema_validator import validate as schema_check  # noqa: E402

    errors: list[str] = []

    # --- plugin.json against the IPC plugin schema ------------------------
    plugin_json = os.path.join(ROOT, "plugin.json")
    try:
        with open(plugin_json, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        for err in schema_check(manifest, _schema("plugin.v1.schema.json")):
            errors.append(f"plugin.json {err}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"plugin.json nicht lesbar: {exc}")
        manifest = {}

    if manifest.get("identifier") != PLUGIN_ID:
        errors.append(
            f"plugin.json: identifier '{manifest.get('identifier')}' != PLUGIN_ID '{PLUGIN_ID}'"
        )
    for action in manifest.get("actions", []):
        entry = action.get("entrypoint", "")
        if entry and not os.path.isfile(os.path.join(ROOT, entry)):
            errors.append(f"plugin.json: entrypoint '{entry}' existiert nicht")
        for icon_key in ("icons-light", "icons-dark"):
            for icon in action.get(icon_key, []):
                if not os.path.isfile(os.path.join(ROOT, icon)):
                    errors.append(f"plugin.json: {icon_key} Datei '{icon}' fehlt")

    # --- metadata.json against PCM v2 -------------------------------------
    meta_path = os.path.join(ROOT, "package", "metadata.json")
    try:
        with open(meta_path, "r", encoding="utf-8") as handle:
            meta = json.load(handle)
        for err in schema_check(meta, _schema("pcm.v2.schema.json")):
            errors.append(f"metadata.json {err}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"metadata.json nicht lesbar: {exc}")
        meta = {}

    if meta.get("identifier") != PLUGIN_ID:
        errors.append(
            f"metadata.json: identifier '{meta.get('identifier')}' != PLUGIN_ID '{PLUGIN_ID}'"
        )
    if meta.get("type") != "plugin":
        errors.append("metadata.json: type muss 'plugin' sein")
    for version in meta.get("versions", []):
        if version.get("runtime") != "ipc":
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
    print("Manifeste und Layout sind gueltig (PCM v2 + IPC-Plugin v1).")
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