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

from kicad_ai.ui import run  # noqa: E402


def _project_from_ipc() -> str | None:
    """Ask the running KiCad instance for the open project path."""
    try:
        from kipy import KiCad
        from kipy.proto.common.types import DocumentType
    except ImportError:
        return None

    try:
        kicad = KiCad()
    except Exception:
        return None

    for doc_type in (
        getattr(DocumentType, "PCB", None),
        getattr(DocumentType, "SCHEMATIC", None),
    ):
        if doc_type is None:
            continue
        try:
            docs = kicad.get_open_documents(doc_type)
        except Exception:
            continue
        for doc in docs:
            path = getattr(doc, "path", "") or ""
            if not path:
                project = getattr(doc, "project", None)
                path = getattr(project, "path", "") if project is not None else ""
            if path:
                return path

    # Fallback: the project object itself carries the .kicad_pro path
    try:
        for doc in kicad.get_open_documents(getattr(DocumentType, "PCB", 0)):
            project = kicad.get_project(doc)
            path = getattr(getattr(project, "document", None), "path", "")
            if path:
                return path
    except Exception:
        pass
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

    app = wx.GetApp()
    owns_app = False
    if app is None:
        app = wx.App(False)
        owns_app = True
    try:
        wx.MessageBox(
            f"{message}\n\n{detail}" if detail else message,
            "KiCad KI-Assistent",
            wx.OK | wx.ICON_INFORMATION,
        )
    finally:
        if owns_app:
            app.Destroy()


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
    run(project)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())