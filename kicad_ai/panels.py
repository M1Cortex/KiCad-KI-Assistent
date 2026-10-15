"""Small self-drawn panels: statistic tiles, the summary card and a badge row."""

from __future__ import annotations

import wx

from .theme import Theme

__all__ = ["StatTile", "StatRow", "SummaryCard", "Card"]


def _round_rect(dc: wx.DC, colour: wx.Colour, rect: wx.Rect, radius: int) -> None:
    dc.SetBrush(wx.Brush(colour))
    dc.SetPen(wx.Pen(colour))
    dc.DrawRoundedRectangle(rect, radius)


class Card(wx.Panel):
    """A rounded surface used as a container."""

    def __init__(self, parent: wx.Window, theme: Theme, fill=None, border=True) -> None:
        super().__init__(parent, style=wx.NO_FULL_REPAINT_ON_RESIZE)
        self.theme = theme
        self._fill = fill or theme.surface
        self._border = border
        self.SetBackgroundColour(theme.bg)
        # Required by wxAutoBufferedPaintDC; without it wx asserts and the
        # panel stays blank.
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        # Bind explicitly: relying on wxPython's implicit OnPaint lookup does
        # not fire reliably for panels that are re-created in a sizer.
        self.Bind(wx.EVT_PAINT, self._on_paint)

    def paint(self, dc: wx.DC) -> None:
        rect = wx.Rect(0, 0, self.GetSize().width, self.GetSize().height)
        _round_rect(dc, self._fill, rect, self.theme.radius)
        if self._border:
            dc.SetPen(wx.Pen(self.theme.border, 1))
            dc.SetBrush(wx.TRANSPARENT_BRUSH)
            dc.DrawRoundedRectangle(rect, self.theme.radius)

    def _on_paint(self, event: wx.PaintEvent) -> None:
        # AutoBufferedPaintDC does the double buffering; a plain PaintDC on a
        # background-coloured panel leaves the surface blank on Windows.
        dc = wx.AutoBufferedPaintDC(self)
        dc.SetBackground(wx.Brush(self.theme.bg))
        dc.Clear()
        self.paint(dc)


class StatTile(Card):
    """One number with a label, optionally tinted by severity."""

    def __init__(
        self,
        parent: wx.Window,
        theme: Theme,
        value: str,
        label: str,
        accent: wx.Colour | None = None,
    ) -> None:
        super().__init__(parent, theme)
        self.value = value
        self.label = label
        self.accent = accent
        self.SetMinSize(wx.Size(96, 62))

    def paint(self, dc: wx.DC) -> None:
        super().paint(dc)
        width, height = self.GetSize().width, self.GetSize().height
        theme = self.theme

        if self.accent is not None:
            dc.SetBrush(wx.Brush(self.accent))
            dc.SetPen(wx.Pen(self.accent))
            dc.DrawRoundedRectangle(wx.Rect(0, 8, 3, height - 16), 2)

        dc.SetFont(theme.font(size=17, bold=True))
        dc.SetTextForeground(theme.text if self.accent is None else self.accent)
        dc.DrawText(self.value, 14, 10)

        dc.SetFont(theme.font(size=8))
        dc.SetTextForeground(theme.text_faint)
        dc.DrawText(self.label.upper(), 14, 38)


class StatRow(wx.Panel):
    """A horizontal strip of :class:`StatTile`."""

    def __init__(self, parent: wx.Window, theme: Theme) -> None:
        super().__init__(parent)
        self.theme = theme
        self._sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.SetSizer(self._sizer)

    def set_stats(self, stats: list[tuple[str, str, wx.Colour | None]]) -> None:
        self._sizer.Clear(delete_windows=True)
        for value, label, accent in stats:
            self._sizer.Add(
                StatTile(self, self.theme, value, label, accent),
                0,
                wx.EXPAND | wx.RIGHT,
                8,
            )
        self.Layout()


class SummaryCard(Card):
    """Shows the review summary with a coloured heading rule."""

    def __init__(self, parent: wx.Window, theme: Theme) -> None:
        super().__init__(parent, theme)
        self.text = "Noch kein Review ausgeführt."
        self.accent = theme.accent
        self.SetMinSize(wx.Size(-1, 74))

    def set_text(self, text: str, accent: wx.Colour | None = None) -> None:
        self.text = text or "Keine Zusammenfassung."
        if accent is not None:
            self.accent = accent
        self.Refresh()

    def paint(self, dc: wx.DC) -> None:
        super().paint(dc)
        theme = self.theme
        width, height = self.GetSize().width, self.GetSize().height

        dc.SetPen(wx.Pen(self.accent, 3))
        dc.SetBrush(wx.TRANSPARENT_BRUSH)
        dc.DrawRoundedRectangle(wx.Rect(0, 0, width, height), theme.radius)

        dc.SetFont(theme.font(size=8, bold=True))
        dc.SetTextForeground(theme.text_faint)
        dc.DrawText("ZUSAMMENFASSUNG", 18, 12)

        dc.SetFont(theme.font(size=10))
        dc.SetTextForeground(theme.text)
        words = self.text.split()
        line, y = "", 34
        max_width = width - 36
        for word in words:
            candidate = f"{line} {word}".strip()
            if dc.GetTextExtent(candidate)[0] > max_width:
                dc.DrawText(line, 18, y)
                y += 16
                line = word
                if y > height - 20:
                    break
            else:
                line = candidate
        if line and y <= height - 20:
            dc.DrawText(line, 18, y)