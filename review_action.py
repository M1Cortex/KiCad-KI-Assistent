"""IPC action entrypoint: opens the review dialog for the current project.

KiCad 10 launches IPC actions in a separate process and passes the board
document via the API. In KiCad 11 the same action is also offered in the
schematic editor; when no board is open we fall back to scanning the project
directory.

Entry point referenced from ``plugin.json``.
"""

from __future__ import annotations

import os
import sys

# Make the bundled package importable when launched by KiCad.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _extend_path_with_plugin_environment() -> None:
    """Make the plugin's virtualenv importable.

    KiCad launches actions with the interpreter named in
    ``api.interpreter_path`` - normally KiCad's own ``bin/pythonw.exe`` - and
    that interpreter has neither ``kipy`` nor anything else from
    requirements.txt. The per-plugin virtualenv KiCad builds for dependencies
    lives elsewhere, so its ``site-packages`` has to be added explicitly.
    Without this the action cannot talk to the IPC API and never finds the
    open project.
    """
    if sys.prefix != sys.base_prefix:
        return  # already running inside a virtualenv

    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
    candidates = [
        os.path.join(base, "KiCad", "10.0", "python-environments"),
    ]
    env_override = os.environ.get("KICAD_PLUGIN_ENVIRONMENTS")
    if env_override:
        candidates.insert(0, env_override)

    identifier = os.path.basename(_HERE)
    for root in candidates:
        venv = os.path.join(root, identifier)
        for site_dir in _site_packages(venv):
            if os.path.isdir(site_dir) and site_dir not in sys.path:
                sys.path.insert(0, site_dir)


def _site_packages(venv: str) -> list[str]:
    """Return the site-packages directories of a virtualenv (any layout)."""
    found: list[str] = []
    lib = os.path.join(venv, "Lib", "site-packages")
    if os.path.isdir(lib):
        found.append(lib)
    lib64 = os.path.join(venv, "lib")
    if os.path.isdir(lib64):
        for entry in os.listdir(lib64):
            candidate = os.path.join(lib64, entry, "site-packages")
            if os.path.isdir(candidate):
                found.append(candidate)
    return found


_extend_path_with_plugin_environment()

from kicad_ai.ui import run  # noqa: E402


# Document types KiCad can report on. The enum is a protobuf wrapper whose
# keys are prefixed, e.g. "DOCTYPE_PCB" - plain "PCB" does not exist and a
# getattr() on it silently yields None, which is what made project detection
# fail before.
_PCB_DOC_TYPE = "DOCTYPE_PCB"
_SCHEMATIC_DOC_TYPE = "DOCTYPE_SCHEMATIC"

# KiCad answers only for document types with a registered handler; the others
# raise ApiError. Two seconds is also tight while an editor is busy.
_IPC_TIMEOUT_MS = 15000


def _project_from_ipc() -> str | None:
    """Ask the running KiCad instance for the open project path.

    In KiCad 10 only the PCB editor reports a usable ``project.path``. The
    schematic editor returns a document with an empty project, so its filename
    is matched against recently used files to recover the directory.
    """
    try:
        from kipy import KiCad
        from kipy.proto.common.types import DocumentType
    except ImportError:
        print("[kicad-ai] kipy nicht installiert - IPC nicht verfügbar", file=sys.stderr)
        return None

    try:
        kicad = KiCad(timeout_ms=_IPC_TIMEOUT_MS)
    except Exception as exc:  # noqa: BLE001
        print(f"[kicad-ai] IPC-Verbindung fehlgeschlagen: {exc}", file=sys.stderr)
        return None

    schematic_filename = ""

    for name in (_PCB_DOC_TYPE, _SCHEMATIC_DOC_TYPE):
        doc_type = getattr(DocumentType, name, None)
        if doc_type is None:
            continue
        try:
            docs = kicad.get_open_documents(doc_type)
        except Exception as exc:  # noqa: BLE001 - no handler for this type
            print(f"[kicad-ai] {name}: {exc}", file=sys.stderr)
            continue

        for doc in docs:
            project = getattr(doc, "project", None)
            path = getattr(project, "path", "") if project is not None else ""
            if path:
                return path
            filename = str(getattr(doc, "board_filename", "") or "")
            if filename and not schematic_filename:
                schematic_filename = filename

    if schematic_filename:
        recovered = _find_project_dir(schematic_filename)
        if recovered:
            print(f"[kicad-ai] Projektordner aus Dateiname ermittelt: {recovered}", file=sys.stderr)
            return recovered

    return None


def _find_project_dir(filename: str) -> str | None:
    """Locate the directory of an open schematic via KiCad's recent-files list.

    The KiCad 10 schematic editor reports the file name only, so the directory
    is recovered from the project manager's session history.
    """
    import json

    config = os.path.join(os.environ.get("APPDATA", ""), "kicad", "10.0", "kicad.json")
    if not os.path.isfile(config):
        return None
    try:
        with open(config, "r", encoding="utf-8", errors="replace") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None

    candidates: list[str] = []
    session = data.get("session", {})
    if isinstance(session, dict):
        for key, value in session.items():
            if isinstance(value, list):
                candidates.extend(str(item) for item in value)

    def expand(paths: list[str]) -> str | None:
        for raw in paths:
            path = raw.replace("\\", "/")
            if path.endswith("/" + filename):
                return raw[: -len(filename)].rstrip("/\\")
        return None

    found = expand(candidates)
    if found:
        return found

    # Fall back to a shallow scan of the KiCad projects folder.
    projects = os.path.join(os.environ.get("USERPROFILE", ""), "Documents", "KiCad", "Projekte")
    if os.path.isdir(projects):
        for entry in sorted(os.listdir(projects)):
            candidate = os.path.join(projects, entry, filename)
            if os.path.isfile(candidate):
                return os.path.join(projects, entry)
    return None


def _discover_project() -> str | None:
    """Fall back to the most recently modified project in common locations."""
    candidates: list[str] = []

    env_dir = os.environ.get("KICAD_AI_PROJECT")
    if env_dir:
        candidates.append(env_dir)

    cwd = os.getcwd()
    try:
        candidates.append(cwd)
        parent = os.path.dirname(cwd)
        if os.path.basename(cwd).lower() in ("pcbnew", "eeschema", "kicad"):
            candidates.append(parent)
    except OSError:
        pass

    for directory in dict.fromkeys(candidates):
        if not os.path.isdir(directory):
            continue
        projects = []
        for entry in os.listdir(directory):
            if entry.endswith(".kicad_pro"):
                path = os.path.join(directory, entry)
                projects.append((os.path.getmtime(path), path))
        if projects:
            projects.sort(reverse=True)
            return projects[0][1]

    return None


def _resolve() -> str | None:
    """Find the project to review, preferring what the user currently has open."""
    try:
        path = _project_from_ipc()
        if path:
            return path
    except Exception as exc:  # noqa: BLE001
        print(f"[kicad-ai] IPC-Abfrage fehlgeschlagen: {exc}", file=sys.stderr)
    return _discover_project()


def _report(message: str, detail: str = "") -> None:
    """Show an explanatory dialog instead of failing silently.

    KiCad only surfaces the exit code to the user, so a bare ``return 1`` looks
    like an unexplained crash. The dialog names the cause and the way out.
    """
    print(f"[kicad-ai] {message}", file=sys.stderr)
    if detail:
        print(f"[kicad-ai] {detail}", file=sys.stderr)

    try:
        import wx
    except ImportError:
        return

    if wx.GetApp() is None:
        # Keep a reference: a temporary would be garbage collected and every
        # subsequent window creation would fail with PyNoAppError.
        app = wx.App(False)
    wx.MessageBox(
        f"{message}\n\n{detail}" if detail else message,
        "KiCad KI-Assistent",
        wx.OK | wx.ICON_INFORMATION,
    )


def main() -> int:
    project = _resolve()
    if not project:
        _report(
            "Es wurde kein KiCad-Projekt gefunden.",
            "Öffne zuerst ein Projekt (.kicad_pro) im Projektmanager oder im "
            "PCB-Editor und klicke dann erneut auf die Aktion.\n\n"
            "Alternativ kannst du ein Projekt auswählen:\n"
            "Plugin → Aktion rechtsklicken → Argumente",
        )
        return 1

    print(f"[kicad-ai] Projekt: {project}", file=sys.stderr)
    try:
        run(project)
    except Exception:
        # KiCad routes plugin stdout/stderr to the warning area of the status
        # bar. Without the traceback a failure is indistinguishable from a no-op.
        import traceback

        traceback.print_exc()
        _report(
            "Das Review-Fenster konnte nicht geöffnet werden.",
            "Die technische Meldung steht in der Statusleiste unten rechts "
            "im Editor. Häufigste Ursache: fehlende Python-Umgebung.\n\n"
            "Abhilfe: Plugin-Einstellungen → Aktion rechtsklicken → "
            "'Plugin-Umgebung neu erstellen'",
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())