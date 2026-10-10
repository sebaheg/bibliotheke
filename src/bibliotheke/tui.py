"""The look shared by the full-screen views (``bib view``, ``bib globe``,
``bib browse --tui``): plotui's own design, so the Textual chrome around a
plot and the plot itself are one surface. The tokens are the ones
plotui.xyz is built from: a near-black blue-grey canvas, grey ink, and the
pink / cyan / orange trio for anything that needs a colour. ``dracula`` is
the same chrome in the Dracula theme's colours (draculatheme.com)."""

from __future__ import annotations

from textual.app import App
from textual.theme import Theme

CANVAS = "#0b0e11"  # the terminal surface behind a plot
SURFACE = "#101418"  # header, footer
PANEL = "#14191e"  # sidebars, the time bar
GRID = "#1a2026"  # hairlines: legend border, grid
FRAME = "#2b3237"
INK = "#676f76"  # secondary text
BODY = "#c9ced3"  # text
PINK = "#ec4c86"
CYAN = "#45c8d1"
ORANGE = "#f0a13c"
GREEN = "#199e70"

THEME = Theme(
    "plotui",
    primary=CYAN,
    secondary=PINK,
    accent=CYAN,
    warning=ORANGE,
    error=PINK,
    success=GREEN,
    foreground=BODY,
    background=CANVAS,
    surface=SURFACE,
    panel=PANEL,
    dark=True,
    variables={
        "footer-background": SURFACE,
        "footer-key-foreground": CYAN,
        "footer-description-foreground": INK,
        "footer-item-background": SURFACE,
        "block-cursor-background": CYAN,
        "block-cursor-foreground": CANVAS,
        "border": FRAME,
        "border-blurred": GRID,
        "scrollbar": GRID,
        "scrollbar-hover": FRAME,
        "scrollbar-active": INK,
        "scrollbar-background": CANVAS,
    },
)


# Dracula's specification: draculatheme.com/contribute.
class DRACULA_COLORS:
    BACKGROUND = "#282a36"
    CURRENT_LINE = "#44475a"
    # Dracula's backgrounds (#282a36 and its darker #21222c, #191a21) read as
    # grey behind a graph, so bib's are plotui-dark, tinted Dracula's purple.
    CANVAS = "#0c0c11"  # plotui #0b0e11
    SURFACE = "#111118"  # plotui #101418: header, footer
    PANEL = "#15161e"  # plotui #14191e: sidebars, legend
    GRID = "#1c1d27"  # plotui #1a2026: hairlines, dimmed edges
    FRAME = "#2c2e3b"  # plotui #2b3237
    FOREGROUND = "#f8f8f2"
    COMMENT = "#6272a4"
    CYAN = "#8be9fd"
    GREEN = "#50fa7b"
    ORANGE = "#ffb86c"
    PINK = "#ff79c6"
    PURPLE = "#bd93f9"
    RED = "#ff5555"
    YELLOW = "#f1fa8c"


_D = DRACULA_COLORS
DRACULA = Theme(
    "bib-dracula",
    primary=_D.PURPLE,
    secondary=_D.PINK,
    accent=_D.CYAN,
    warning=_D.ORANGE,
    error=_D.RED,
    success=_D.GREEN,
    foreground=_D.FOREGROUND,
    background=_D.CANVAS,
    surface=_D.SURFACE,
    panel=_D.PANEL,
    dark=True,
    variables={
        "footer-background": _D.SURFACE,
        "footer-key-foreground": _D.PINK,
        "footer-description-foreground": _D.COMMENT,
        "footer-item-background": _D.SURFACE,
        "block-cursor-background": _D.PURPLE,
        "block-cursor-foreground": _D.CANVAS,
        "border": _D.FRAME,
        "border-blurred": _D.GRID,
        "scrollbar": _D.GRID,
        "scrollbar-hover": _D.FRAME,
        "scrollbar-active": _D.COMMENT,
        "scrollbar-background": _D.CANVAS,
    },
)
THEMES = {"plotui": THEME, "dracula": DRACULA}  # chrome by bib's theme name


def style(app: App, name: str = "plotui") -> None:
    """Give a Textual app a look (``plotui`` or ``dracula``). Call from
    ``__init__``; call again to switch."""
    for theme in THEMES.values():
        if theme.name not in app.available_themes:
            app.register_theme(theme)
    app.theme = THEMES.get(name, THEME).name
