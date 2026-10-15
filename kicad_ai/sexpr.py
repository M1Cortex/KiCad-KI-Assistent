"""Minimal, dependency-free S-expression parser for KiCad files.

KiCad stores schematics, projects and boards as S-expressions. The IPC API does
not expose schematics in KiCad 10, so we parse the files directly.

The parser is deliberately tolerant: unknown constructs are preserved as generic
nodes rather than raising, because KiCad adds new tokens with every release.
"""

from __future__ import annotations

from typing import Any, Iterator

__all__ = ["Symbol", "SymList", "parse", "parse_file", "load", "dumps", "SexprError"]


class SexprError(ValueError):
    """Raised when a file cannot be parsed as an S-expression."""


class Symbol(str):
    """A bare atom (identifier, number, keyword).

    Subclasses ``str`` so it can be used as a dict key or compared directly,
    while remaining distinguishable from quoted strings.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Symbol({str(self)!r})"


class SymList(list):
    """A parenthesised list, i.e. ``(foo (bar 1))``.

    Behaves like a list of ``Symbol | str | SymList`` but offers convenient
    navigation helpers used throughout the analyzer.
    """

    __slots__ = ()

    def head(self) -> Symbol | None:
        """Return the leading atom of the list, e.g. ``symbol`` for ``(symbol ...)``."""
        return self[0] if self and isinstance(self[0], Symbol) else None

    def at(self, name: str) -> "SymList | None":
        """Return the first direct child list whose head equals *name*."""
        for item in self:
            if isinstance(item, SymList) and item.head() == name:
                return item
        return None

    def all(self, name: str) -> Iterator["SymList"]:
        """Yield every direct child list whose head equals *name*."""
        for item in self:
            if isinstance(item, SymList) and item.head() == name:
                yield item

    def deep_all(self, name: str) -> Iterator["SymList"]:
        """Yield every descendant list whose head equals *name*, depth first."""
        for item in self:
            if isinstance(item, SymList):
                if item.head() == name:
                    yield item
                yield from item.deep_all(name)

    def value_of(self, name: str, index: int = 1) -> str | None:
        """Return the atom at *index* of the first child list headed by *name*.

        Accepts both bare atoms and quoted strings, since KiCad uses whichever
        happens to be convenient:

        ``(at 1 2 3)``           -> ``value_of("at") == "1"``
        ``(lib_id "Device:R")``  -> ``value_of("lib_id") == "Device:R"``
        """
        child = self.at(name)
        if child is None or len(child) <= index:
            return None
        item = child[index]
        if isinstance(item, SymList):
            return None
        return str(item)

    def str_value_of(self, name: str) -> str | None:
        """Alias for :meth:`value_of`; kept for readability at call sites."""
        return self.value_of(name)

    def prop(self, name: str, default: Any = None) -> Any:
        """Return ``(name value)`` as a typed value, coercing ``yes``/``no``/numbers."""
        child = self.at(name)
        if child is None or len(child) <= 1:
            return default
        return _coerce(str(child[1]))

    def xy(self, name: str = "at") -> tuple[float, float] | None:
        """Return the ``(at x y ...)`` coordinates of a child list as floats."""
        child = self.at(name)
        if child is None or len(child) < 3:
            return None
        try:
            return (float(str(child[1])), float(str(child[2])))
        except ValueError:
            return None

    def to_dict(self) -> dict[str, Any]:
        """Convert a flat ``(name value)`` list into a dict (last wins)."""
        out: dict[str, Any] = {}
        for item in self:
            if isinstance(item, SymList) and len(item) >= 2:
                out[str(item.head())] = _coerce(str(item[1]))
        return out

    def keyval(self, key: str = "name", value: str = "value") -> tuple[str, str] | None:
        """Read a KiCad ``(key "k" "v")`` pair written as direct atoms.

        Since KiCad 7, ``(property "Reference" "R1")`` stores both the key and
        the value as plain child atoms rather than as nested ``(key ...)``
        lists. Returns ``None`` when the pair is absent.
        """
        if len(self) >= 3:
            return (str(self[1]), str(self[2]))
        child = self.at(key)
        if child is None or len(child) <= 1:
            return None
        val = self.at(value)
        return (str(self[1]), str(val[1]) if val is not None and len(val) > 1 else "")


def _coerce(text: str) -> Any:
    if text == "yes":
        return True
    if text == "no":
        return False
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


_WHITESPACE = " \t\r\n"
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"'}


def _parse_string(data: str, i: int) -> tuple[str, int]:
    assert data[i] == '"'
    i += 1
    out: list[str] = []
    n = len(data)
    while i < n:
        ch = data[i]
        if ch == "\\" and i + 1 < n:
            out.append(_ESCAPES.get(data[i + 1], data[i + 1]))
            i += 2
            continue
        if ch == '"':
            return "".join(out), i + 1
        out.append(ch)
        i += 1
    raise SexprError("unterminated string literal")


def _parse_atom(data: str, i: int) -> tuple[Symbol, int]:
    n = len(data)
    start = i
    while i < n and data[i] not in _WHITESPACE and data[i] not in "()":
        i += 1
    if i == start:
        raise SexprError(f"empty atom at offset {start}")
    return Symbol(data[start:i]), i


def _parse_list(data: str, i: int) -> tuple[SymList, int]:
    assert data[i] == "("
    i += 1
    items: list[Any] = []
    n = len(data)
    while i < n:
        ch = data[i]
        if ch in _WHITESPACE:
            i += 1
            continue
        if ch == ")":
            return SymList(items), i + 1
        if ch == "(":
            child, i = _parse_list(data, i)
            items.append(child)
            continue
        if ch == '"':
            text, i = _parse_string(data, i)
            items.append(text)
            continue
        atom, i = _parse_atom(data, i)
        items.append(atom)
    raise SexprError("unterminated list - missing closing parenthesis")


def parse(text: str) -> SymList:
    """Parse *text* and return the single top level list."""
    i = 0
    n = len(text)
    while i < n and text[i] in _WHITESPACE:
        i += 1
    if i >= n or text[i] != "(":
        raise SexprError("file does not start with a list")
    result, _ = _parse_list(text, i)
    return result


def parse_file(path: str) -> SymList:
    """Parse the KiCad file at *path*."""
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return parse(handle.read())


def load(path: str) -> SymList:
    """Alias for :func:`parse_file`."""
    return parse_file(path)


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def dumps(node: Any, indent: int = 0) -> str:
    """Serialise a parsed node back to S-expression text (used for edits)."""
    pad = "\t" * indent
    if isinstance(node, SymList):
        if not node:
            return f"{pad}()"
        head = node[0]
        flat = all(not isinstance(item, SymList) for item in node[1:])
        parts = [
            _dumps_item(item) if not isinstance(item, SymList) else dumps(item, indent + 1)
            for item in node
        ]
        if flat:
            return f"{pad}(" + " ".join(parts) + ")"
        inner = "\n".join(parts)
        return f"{pad}(\n{inner}\n{pad})"
    return f"{pad}{_dumps_item(node)}"


def _dumps_item(node: Any) -> str:
    if isinstance(node, Symbol):
        return str(node)
    return f'"{_escape(str(node))}"'