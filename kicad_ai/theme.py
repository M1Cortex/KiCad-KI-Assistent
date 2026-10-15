"""Visual design tokens for the review dialog.

wx draws in the native Windows look by default, which reads as unfinished next
to KiCad's own UI. These tokens define a flat, spacing-based design that adapts
to the system colour scheme, so the dialog looks deliberate in both light and
dark mode.
"""

from __future__ import annotations

import wx

from .rules import Severity

__all__ = [
    "Theme",
    "SEVERITY_TOKENS",
    "STATUS_TOKENS",
    "detect_dark",
    "severity_colour",
    "severity_label",
    "status_colour",
    "status_label",
]


def detect_dark() -> bool:
    """Return True when the system uses a dark colour scheme."""
    try:
        window = wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOW)
        return window.GetLuminance() < 0.5
    except Exception:  # noqa: BLE001 - never block the UI on a theme question
        return False


class Theme:
    """Colour and metric tokens for one colour scheme."""

    def __init__(self, dark: bool = False) -> None:
        self.dark = dark

        if dark:
            self.bg = wx.Colour(31, 31, 34)
            self.surface = wx.Colour(39, 39, 43)
            self.surface_alt = wx.Colour(45, 45, 50)
            self.border = wx.Colour(58, 58, 64)
            self.text = wx.Colour(233, 233, 235)
            self.text_dim = wx.Colour(158, 158, 165)
            self.text_faint = wx.Colour(115, 115, 125)
            self.accent = wx.Colour(76, 141, 255)
            self.accent_dim = wx.Colour(48, 92, 168)
            self.header_bg = wx.Colour(35, 35, 39)
            self.hover = wx.Colour(52, 52, 58)
            self.selected = wx.Colour(40, 62, 96)
        else:
            self.bg = wx.Colour(245, 246, 248)
            self.surface = wx.Colour(255, 255, 255)
            self.surface_alt = wx.Colour(250, 250, 251)
            self.border = wx.Colour(224, 226, 230)
            self.text = wx.Colour(28, 30, 34)
            self.text_dim = wx.Colour(96, 100, 108)
            self.text_faint = wx.Colour(140, 144, 152)
            self.accent = wx.Colour(40, 100, 220)
            self.accent_dim = wx.Colour(28, 72, 160)
            self.header_bg = wx.Colour(255, 255, 255)
            self.hover = wx.Colour(243, 245, 248)
            self.selected = wx.Colour(222, 235, 255)

        self.radius = 6
        self.pad = 12
        self.gap = 8

    # -- fonts ----------------------------------------------------------- #

    def font(self, size: int = 10, bold: bool = False, mono: bool = False) -> wx.Font:
        family = wx.FONTFAMILY_TELETYPE if mono else wx.FONTFAMILY_DEFAULT
        weight = wx.FONTWEIGHT_BOLD if bold else wx.FONTWEIGHT_NORMAL
        font = wx.Font(size, family, wx.FONTSTYLE_NORMAL, weight)
        if not self.dark and not mono:
            try:
                font.SetFaceName("Segoe UI")
            except Exception:  # noqa: BLE001
                pass
        return font

    # -- helpers --------------------------------------------------------- #

    def mix(self, colour: wx.Colour, other: wx.Colour, ratio: float) -> wx.Colour:
        return wx.Colour(
            int(colour.Red() * (1 - ratio) + other.Red() * ratio),
            int(colour.Green() * (1 - ratio) + other.Green() * ratio),
            int(colour.Blue() * (1 - ratio) + other.Blue() * ratio),
        )


#: Severity colours. The ERROR tone is deliberately desaturated a little on dark
#: backgrounds so it stays readable instead of glaring.
SEVERITY_TOKENS = {
    Severity.ERROR: (lambda t: (wx.Colour(211, 47, 47) if not t.dark else wx.Colour(255, 106, 105)), "Fehler"),
    Severity.WARNING: (lambda t: (wx.Colour(230, 138, 0) if not t.dark else wx.Colour(255, 183, 77)), "Warnung"),
    Severity.INFO: (lambda t: (wx.Colour(25, 118, 210) if not t.dark else wx.Colour(116, 176, 255)), "Info"),
    Severity.HINT: (lambda t: (wx.Colour(120, 124, 132) if not t.dark else wx.Colour(150, 154, 162)), "Hinweis"),
}

STATUS_TOKENS = {
    "erfuellt": (lambda t: wx.Colour(56, 142, 60), "Erfüllt"),
    "teilweise": (lambda t: wx.Colour(230, 138, 0), "Teilweise"),
    "fehlt": (lambda t: wx.Colour(211, 47, 47), "Fehlt"),
    "unklar": (lambda t: wx.Colour(130, 134, 142), "Unklar"),
}


def severity_colour(theme: Theme, severity: Severity) -> wx.Colour:
    factory, _ = SEVERITY_TOKENS[severity]
    return factory(theme)


def severity_label(severity: Severity) -> str:
    _, label = SEVERITY_TOKENS[severity]
    return label


def status_colour(theme: Theme, status: str) -> wx.Colour:
    entry = STATUS_TOKENS.get(status)
    if entry is None:
        return theme.text_dim
    return entry[0](theme)


def status_label(status: str) -> str:
    return STATUS_TOKENS.get(status, (None, status))[1]