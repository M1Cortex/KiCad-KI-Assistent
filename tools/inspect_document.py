"""Inspect the full contents of an open DocumentSpecifier.

Used to find which fields carry a usable project path when only the schematic
editor is open (KiCad 10 leaves the project path empty there).

    python tools/inspect_document.py
"""

from __future__ import annotations

from kipy import KiCad
from kipy.proto.common.types import DocumentType

kicad = KiCad(timeout_ms=10000)

for name in ("DOCTYPE_PCB", "DOCTYPE_SCHEMATIC"):
    doc_type = getattr(DocumentType, name, None)
    if doc_type is None:
        continue
    try:
        docs = kicad.get_open_documents(doc_type)
    except Exception as exc:  # noqa: BLE001
        print(f"{name}: FEHLER {exc}")
        continue

    print(f"=== {name}: {len(docs)} Dokument(e) ===")
    for index, doc in enumerate(docs):
        print(f"  [{index}] Message-Text:")
        print("      " + str(doc).replace("\n", "\n      "))

        print("  Felder:")
        for field, value in doc.ListFields():
            print(f"      {field} = {value!r}")

        project = getattr(doc, "project", None)
        if project is not None:
            print("  project:")
            for field, value in project.ListFields():
                print(f"      {field} = {value!r}")
            print("      project-Text:")
            print("          " + str(project).replace("\n", "\n          "))
        print()