"""Custom-drawn list with its own scrolling.

wx.ListCtrl in virtual report mode is fragile here: it asserts inside
``OnGetItemText`` and, without at least one column, never issues draw events at
all. Rather than fight the control, this panel paints its rows directly and
manages its own scrollbar, which makes the behaviour fully predictable.
"""

from __future__ import annotations

import wx

from .rules import Severity
from .theme import (
    Theme,
    severity_colour,
    severity_label,
    status_colour,
    status_label,
)

__all__ = ["ScrollingList", "Row"]

SCROLLBAR_WIDTH = 14


class Row:
    """One displayed entry."""

    __slots__ = ("primary", "secondary", "tertiary", "badge", "colour", "payload", "location")

    def __init__(self, primary, secondary="", tertiary="", badge="",
                 colour=None, payload=None, location=""):
        self.primary = primary
        self.secondary = secondary
        self.tertiary = tertiary
        self.badge = badge
        self.colour = colour
        self.payload = payload
        self.location = location


def _ellipsize(dc: wx.DC, text: str, width: int) -> str:
    if width <= 0:
        return ""
    if dc.GetTextExtent(text)[0] <= width:
        return text
    ellipsis = "…"
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if dc.GetTextExtent(text[:mid] + ellipsis)[0] <= width:
            low = mid
        else:
            high = mid - 1
    return text[:low] + ellipsis


class ScrollingList(wx.Panel):
    """A flat, self-drawn list with hover, selection and keyboard navigation."""

    ROW_HEIGHT = 62

    def __init__(
        self,
        parent: wx.Window,
        theme: Theme,
        on_activate=None,
        style: str = "finding",
    ) -> None:
        super().__init__(parent, style=wx.FULL_REPAINT_ON_RESIZE)
        self.theme = theme
        self.style = style
        self.rows: list[Row] = []
        self._offset = 0
        self._hover = -1
        self._selected = -1
        self._on_activate = on_activate

        self.SetBackgroundColour(theme.surface)
        self.SetDoubleBuffered(True)

        self.scrollbar = wx.ScrollBar(
            self, style=wx.SB_VERTICAL, size=wx.Size(SCROLLBAR_WIDTH, -1)
        )
        self.scrollbar.Bind(wx.EVT_SCROLL, self.on_scroll)

        self.Bind(wx.EVT_PAINT, self.OnPaint)
        self.Bind(wx.EVT_SIZE, self.on_resize)
        self.Bind(wx.EVT_MOTION, self.on_motion)
        self.Bind(wx.EVT_LEAVE_WINDOW, self.on_leave)
        self.Bind(wx.EVT_LEFT_DOWN, self.on_click)
        self.Bind(wx.EVT_LEFT_DCLICK, self.on_double_click)
        self.Bind(wx.EVT_KEY_DOWN, self.on_key)
        self.Bind(wx.EVT_SET_FOCUS, lambda e: self.Refresh())

    # -- data ------------------------------------------------------------ #

    def set_rows(self, rows: list[Row]) -> None:
        self.rows = rows
        self._selected = -1
        self._hover = -1
        self._update_scrollbar()
        self.Refresh()

    def clear(self) -> None:
        self.set_rows([])

    def GetItemCount(self) -> int:  # noqa: N802 - mirrors the wx API it replaces
        return len(self.rows)

    def row_payload(self, index: int):
        if 0 <= index < len(self.rows):
            return self.rows[index].payload
        return None

    def sort_by_severity(self) -> None:
        """Order rows by descending severity.

        The severity is read from the payload when it carries one (``Finding``)
        and otherwise from a payload that *is* a severity, so the method works
        for requirement rows too.
        """
        def key(row: Row) -> int:
            payload = row.payload
            severity = getattr(payload, "severity", payload)
            try:
                return -int(severity)
            except (TypeError, ValueError):
                return 0

        self.rows.sort(key=key)
        self.Refresh()

    # -- geometry --------------------------------------------------------- #

    def _content_size(self) -> tuple[int, int]:
        width = max(1, self.GetClientSize().width - SCROLLBAR_WIDTH)
        height = len(self.rows) * self.ROW_HEIGHT
        return width, height

    def _visible_rows(self) -> int:
        height = self.GetClientSize().height
        return max(1, height // self.ROW_HEIGHT)

    def _update_scrollbar(self) -> None:
        _, content_height = self._content_size()
        size = self.GetClientSize()
        viewport = max(1, size.height)

        if content_height <= viewport:
            self.scrollbar.Hide()
            self._offset = 0
        else:
            self.scrollbar.Show()
            # The scrollbar is a plain child, not in a sizer, so it has to be
            # placed along the right edge explicitly.
            self.scrollbar.SetSize(wx.Size(SCROLLBAR_WIDTH, viewport))
            self.scrollbar.SetPosition(wx.Point(size.width - SCROLLBAR_WIDTH, 0))
            self.scrollbar.SetScrollbar(
                self._offset,
                min(viewport, content_height),
                0,
                content_height,
            )

    def on_resize(self, event: wx.SizeEvent) -> None:
        # Defer: during a resize the client size is not final yet.
        self._update_scrollbar()
        wx.CallAfter(self._update_scrollbar)
        event.Skip()

    def on_scroll(self, event: wx.ScrollEvent) -> None:
        self._offset = int(event.GetPosition())
        self.Refresh()
        event.Skip()

    def ensure_visible(self, index: int) -> None:
        if not (0 <= index < len(self.rows)):
            return
        _, content_height = self._content_size()
        viewport = self.GetClientSize().height
        top = index * self.ROW_HEIGHT
        bottom = top + self.ROW_HEIGHT
        if top < self._offset:
            self._offset = top
        elif bottom > self._offset + viewport:
            self._offset = max(0, bottom - viewport)
        self._offset = max(0, min(self._offset, max(0, content_height - viewport)))
        self._update_scrollbar()
        self.Refresh()

    def _index_at(self, event: wx.MouseEvent) -> int:
        y = event.GetY() + self._offset
        if y < 0:
            return -1
        index = y // self.ROW_HEIGHT
        return index if 0 <= index < len(self.rows) else -1

    # -- events ----------------------------------------------------------- #

    def on_motion(self, event: wx.MouseEvent) -> None:
        index = self._index_at(event)
        if index != self._hover:
            self._hover = index
            self.Refresh()

    def on_leave(self, event: wx.MouseEvent) -> None:
        if self._hover != -1:
            self._hover = -1
            self.Refresh()

    def on_click(self, event: wx.MouseEvent) -> None:
        index = self._index_at(event)
        if index != self._selected:
            self._selected = index
            self.SetFocus()
            self.Refresh()

    def on_double_click(self, event: wx.MouseEvent) -> None:
        index = self._index_at(event)
        if index >= 0 and self._on_activate is not None:
            self._on_activate(index)
        event.Skip()

    def on_key(self, event: wx.KeyEvent) -> None:
        if not self.rows:
            return
        if event.GetKeyCode() == wx.WXK_DOWN:
            self._selected = min(len(self.rows) - 1, self._selected + 1)
        elif event.GetKeyCode() == wx.WXK_UP:
            self._selected = max(0, self._selected - 1)
        elif event.GetKeyCode() == wx.WXK_HOME:
            self._selected = 0
        elif event.GetKeyCode() == wx.WXK_END:
            self._selected = len(self.rows) - 1
        elif event.GetKeyCode() == wx.WXK_RETURN and self._selected >= 0:
            if self._on_activate is not None:
                self._on_activate(self._selected)
            return
        else:
            event.Skip()
            return
        self.ensure_visible(self._selected)
        event.Skip()

    # -- drawing ---------------------------------------------------------- #

    def OnPaint(self, event: wx.PaintEvent) -> None:  # noqa: N802 - wx naming
        dc = wx.PaintDC(self)
        dc.SetBackground(wx.Brush(self.theme.surface))
        dc.Clear()
        self._paint_rows(dc)

    def _paint_rows(self, dc: wx.DC) -> None:
        theme = self.theme
        width, _ = self._content_size()
        height = self.GetClientSize().height
        visible = self._visible_rows()

        first = max(0, self._offset // self.ROW_HEIGHT)
        last = min(len(self.rows), first + visible + 1)

        for index in range(first, last):
            top = index * self.ROW_HEIGHT - self._offset
            row = self.rows[index]

            if index == self._selected:
                dc.SetBrush(wx.Brush(theme.selected))
            elif index == self._hover:
                dc.SetBrush(wx.Brush(theme.hover))
            else:
                dc.SetBrush(wx.Brush(theme.surface))
            dc.SetPen(wx.Pen(theme.border))
            dc.DrawRectangle(0, top, width, self.ROW_HEIGHT)

            if self.style == "requirement":
                self._draw_requirement(dc, row, top, width)
            else:
                self._draw_finding(dc, row, top, width)

    def _draw_finding(self, dc: wx.DC, row: Row, top: int, width: int) -> None:
        theme = self.theme
        colour = row.colour or theme.text_dim
        margin = 16

        # Severity bar on the left edge, inset so it reads as a marker
        dc.SetBrush(wx.Brush(colour))
        dc.SetPen(wx.Pen(colour))
        dc.DrawRoundedRectangle(0, top + 10, 5, self.ROW_HEIGHT - 20, 2)

        # Category pill, right aligned
        pill = row.badge or ""
        pill_x = width - margin
        if pill:
            dc.SetFont(theme.font(size=8, bold=True))
            text_w = dc.GetTextExtent(pill)[0]
            pill_w = text_w + 20
            pill_x = width - pill_w - margin
            dc.SetBrush(wx.Brush(colour))
            dc.SetPen(wx.Pen(colour))
            dc.DrawRoundedRectangle(pill_x, top + 13, pill_w, 20, 10)
            dc.SetTextForeground(wx.Colour(255, 255, 255))
            dc.DrawText(pill, pill_x + 10, top + 19)

        # Title
        dc.SetFont(theme.font(size=10, bold=True))
        dc.SetTextForeground(theme.text)
        title_width = max(80, pill_x - margin - 14) if pill else width - margin * 2
        dc.DrawText(_ellipsize(dc, row.primary, title_width), margin, top + 12)

        # Rule and location
        dc.SetFont(theme.font(size=8, mono=True))
        dc.SetTextForeground(theme.text_faint)
        meta = row.secondary
        if row.location and row.location != "-":
            meta = f"{meta}   ·   {row.location}"
        dc.DrawText(_ellipsize(dc, meta, width - margin * 2), margin, top + 30)

        # Detail
        dc.SetFont(theme.font(size=9))
        dc.SetTextForeground(theme.text_dim)
        dc.DrawText(_ellipsize(dc, row.tertiary, width - margin * 2), margin, top + 44)

        # Separator
        dc.SetPen(wx.Pen(theme.border, 1))
        dc.DrawLine(margin, top + self.ROW_HEIGHT - 1, width - margin, top + self.ROW_HEIGHT - 1)

    def _draw_requirement(self, dc: wx.DC, row: Row, top: int, width: int) -> None:
        theme = self.theme
        colour = row.colour or theme.text_dim
        margin = 16

        dc.SetBrush(wx.Brush(colour))
        dc.SetPen(wx.Pen(colour))
        dc.DrawRoundedRectangle(0, top + 10, 5, self.ROW_HEIGHT - 20, 2)

        dc.SetFont(theme.font(size=8, bold=True, mono=True))
        dc.SetTextForeground(theme.text_faint)
        dc.DrawText(row.secondary, margin, top + 14)

        pill = row.badge or ""
        if pill:
            pill_x = margin + 86
            dc.SetFont(theme.font(size=8, bold=True))
            text_w = dc.GetTextExtent(pill)[0]
            pill_w = text_w + 20
            dc.SetBrush(wx.Brush(colour))
            dc.SetPen(wx.Pen(colour))
            dc.DrawRoundedRectangle(pill_x, top + 11, pill_w, 20, 10)
            dc.SetTextForeground(wx.Colour(255, 255, 255))
            dc.DrawText(pill, pill_x + 10, top + 17)

            dc.SetFont(theme.font(size=10, bold=True))
            dc.SetTextForeground(theme.text)
            text_x = pill_x + pill_w + 12
            dc.DrawText(
                _ellipsize(dc, row.primary, max(80, width - text_x - margin)),
                text_x,
                top + 12,
            )

        detail = row.tertiary
        dc.SetFont(theme.font(size=9))
        dc.SetTextForeground(theme.text_dim if detail else theme.text_faint)
        dc.DrawText(_ellipsize(dc, detail, width - margin * 2), margin, top + 38)

        dc.SetPen(wx.Pen(theme.border, 1))
        dc.DrawLine(margin, top + self.ROW_HEIGHT - 1, width - margin, top + self.ROW_HEIGHT - 1)