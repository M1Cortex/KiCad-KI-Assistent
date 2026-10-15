"""Probe which DocumentType values actually report the open project.

    python tools/probe_documents.py
"""

from __future__ import annotations

from kipy import KiCad
from kipy.proto.common.types import DocumentType

DOC_TYPES = (
    "DOCTYPE_PCB",
    "DOCTYPE_SCHEMATIC",
    "DOCTYPE_PROJECT",
    "DOCTYPE_FOOTPRINT",
    "DOCTYPE_SYMBOL",
    "DOCTYPE_DRAWING_SHEET",
)

print("EnumTypeWrapper-Attribute:", hasattr(DocumentType, "DOCTYPE_PCB"))
print("altes Attribut 'PCB'   :", hasattr(DocumentType, "PCB"))
print()

kicad = KiCad()
for name in DOC_TYPES:
    doc_type = getattr(DocumentType, name, None)
    if doc_type is None:
        print(f"{name:<24} FEHLT")
        continue
    try:
        docs = kicad.get_open_documents(doc_type)
    except Exception as exc:  # noqa: BLE001
        print(f"{name:<24} ({doc_type}) FEHLER: {type(exc).__name__}: {exc}")
        continue

    print(f"{name:<24} ({doc_type}) -> {len(docs)} Dokument(e)")
    for doc in docs:
        print(f"    doc.type = {getattr(doc, 'type', '?')}")
        print(f"    doc.path = {getattr(doc, 'path', None)!r}")
        project = getattr(doc, "project", None)
        if project is not None:
            print(f"    doc.project.path = {getattr(project, 'path', None)!r}")
            try:
                real = kicad.get_project(doc)
                print(f"    get_project(...).document.path = {getattr(getattr(real, 'document', None), 'path', None)!r}")
            except Exception as exc:  # noqa: BLE001
                print(f"    get_project FEHLER: {type(exc).__name__}: {exc}")