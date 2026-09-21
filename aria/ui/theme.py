"""Visual theme: palette, stylesheet and metrics.

The workspace is a dark neutral grey because a radiograph is judged by its grey
levels, and a bright interface around a dark image shifts how those greys are
perceived. Nothing in the chrome is saturated; colour is reserved for the
annotations themselves and for the small number of status signals that need it.

Two accessibility rules are built in here rather than left to each widget
(NFR 010):

* every status colour is paired with a glyph and a text label, so the interface
  is readable without colour,
* contrast between text and its background stays above the level needed for
  small text, and a high contrast variant raises it further.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    """Named colours. Referenced by the stylesheet and by painted widgets."""

    # Surfaces, from the deepest to the most raised.
    canvas: str = "#0b0d0f"          # behind the image
    window: str = "#2b2f33"          # application background
    panel: str = "#34393e"           # docks and panels
    panel_alt: str = "#3b4147"       # alternating rows, group bodies
    raised: str = "#454c53"          # buttons, inputs
    raised_hover: str = "#515960"
    raised_pressed: str = "#2f353a"
    header: str = "#272b2f"          # section headers, toolbars

    # Lines.
    border: str = "#1e2226"
    border_light: str = "#565e66"
    divider: str = "#232629"

    # Text.
    text: str = "#e6e9ec"
    text_dim: str = "#a8b0b8"
    text_disabled: str = "#6b7278"
    text_inverse: str = "#12161a"

    # Accent and status. Each is paired with a glyph wherever it is used.
    accent: str = "#6cb2e5"
    accent_dim: str = "#3d7ea8"
    success: str = "#6bb886"
    warning: str = "#d9a441"
    danger: str = "#e88b8b"
    info: str = "#7fb8d4"

    # View frame header bars. Distinct hues so each pane is identifiable at a
    # glance, matching the pane name printed in the same bar.
    view_main: str = "#c0504d"
    view_right: str = "#c9a227"
    view_left: str = "#4f9153"
    view_compare: str = "#5a7fb8"


PALETTE = Palette()

HIGH_CONTRAST = Palette(
    canvas="#000000",
    window="#15181b",
    panel="#1d2125",
    panel_alt="#252a2f",
    raised="#343b42",
    raised_hover="#454d55",
    raised_pressed="#12161a",
    header="#101316",
    border="#000000",
    border_light="#7e878f",
    text="#ffffff",
    text_dim="#d2d8dd",
    text_disabled="#8a9198",
    accent="#63b3f0",
    success="#6fc98c",
    warning="#f0b955",
    danger="#f07171",
)


@dataclass(frozen=True)
class Metrics:
    """Spacing and sizing, in device independent pixels."""

    unit: int = 4
    gutter: int = 8
    panel_width: int = 376
    panel_min_width: int = 320
    toolbar_icon: int = 20
    tool_button: int = 28
    row_height: int = 24
    header_height: int = 26
    view_header_height: int = 22
    radius: int = 3
    handle_size: int = 8


METRICS = Metrics()


def stylesheet(palette: Palette = PALETTE, font_size: int = 9, scale: float = 1.0) -> str:
    """Build the application stylesheet for a palette and text size."""
    p = palette
    m = METRICS
    s = lambda v: max(1, int(round(v * scale)))  # noqa: E731

    return f"""
/* ---------------------------------------------------------------- base */
* {{
    outline: none;
}}

QWidget {{
    background-color: {p.window};
    color: {p.text};
    font-size: {font_size}pt;
    selection-background-color: {p.accent_dim};
    selection-color: {p.text};
}}

QMainWindow, QDialog {{
    background-color: {p.window};
}}

QWidget:disabled {{
    color: {p.text_disabled};
}}

QToolTip {{
    background-color: {p.header};
    color: {p.text};
    border: 1px solid {p.border_light};
    padding: {s(5)}px {s(7)}px;
    border-radius: {m.radius}px;
}}

/* --------------------------------------------------------------- menus */
QMenuBar {{
    background-color: {p.header};
    border-bottom: 1px solid {p.border};
    padding: {s(2)}px {s(4)}px;
}}
QMenuBar::item {{
    background: transparent;
    padding: {s(5)}px {s(10)}px;
    border-radius: {m.radius}px;
}}
QMenuBar::item:selected {{
    background-color: {p.raised};
}}
QMenuBar::item:pressed {{
    background-color: {p.accent_dim};
}}

QMenu {{
    background-color: {p.panel};
    border: 1px solid {p.border};
    padding: {s(4)}px;
}}
QMenu::item {{
    padding: {s(6)}px {s(28)}px {s(6)}px {s(28)}px;
    border-radius: {m.radius}px;
}}
QMenu::item:selected {{
    background-color: {p.accent_dim};
}}
QMenu::item:disabled {{
    color: {p.text_disabled};
}}
QMenu::separator {{
    height: 1px;
    background: {p.border_light};
    margin: {s(4)}px {s(8)}px;
}}
QMenu::indicator {{
    width: {s(14)}px;
    height: {s(14)}px;
    left: {s(7)}px;
}}

/* ------------------------------------------------------------ toolbars */
QToolBar {{
    background-color: {p.header};
    border: none;
    border-bottom: 1px solid {p.border};
    spacing: {s(2)}px;
    padding: {s(3)}px {s(5)}px;
}}
QToolBar::separator {{
    background: {p.border_light};
    width: 1px;
    margin: {s(5)}px {s(5)}px;
}}

QToolButton {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: {m.radius}px;
    padding: {s(4)}px;
    min-width: {s(m.tool_button)}px;
    min-height: {s(m.tool_button)}px;
}}
QToolButton:hover {{
    background-color: {p.raised};
    border-color: {p.border_light};
}}
QToolButton:pressed, QToolButton:checked {{
    background-color: {p.accent_dim};
    border-color: {p.accent};
}}
QToolButton:disabled {{
    color: {p.text_disabled};
}}
QToolButton::menu-indicator {{
    subcontrol-position: right bottom;
    width: {s(8)}px;
}}

/* ------------------------------------------------------------- buttons */
QPushButton {{
    background-color: {p.raised};
    border: 1px solid {p.border};
    border-radius: {m.radius}px;
    padding: {s(5)}px {s(12)}px;
    min-height: {s(20)}px;
}}
QPushButton:hover {{
    background-color: {p.raised_hover};
    border-color: {p.border_light};
}}
QPushButton:pressed {{
    background-color: {p.raised_pressed};
}}
QPushButton:default {{
    border: 1px solid {p.accent};
}}
QPushButton:disabled {{
    background-color: {p.panel_alt};
    color: {p.text_disabled};
    border-color: {p.border};
}}
QPushButton[accent="true"] {{
    background-color: {p.accent_dim};
    border-color: {p.accent};
}}
QPushButton[accent="true"]:hover {{
    background-color: {p.accent};
    color: {p.text_inverse};
}}
QPushButton[danger="true"] {{
    border-color: {p.danger};
}}
QPushButton[danger="true"]:hover {{
    background-color: {p.danger};
    color: {p.text_inverse};
}}

/* -------------------------------------------------------------- inputs */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background-color: {p.raised_pressed};
    border: 1px solid {p.border};
    border-radius: {m.radius}px;
    padding: {s(4)}px {s(6)}px;
    min-height: {s(18)}px;
    color: {p.text};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: {p.accent};
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    background-color: {p.panel_alt};
    color: {p.text_disabled};
}}
QLineEdit[invalid="true"] {{
    border-color: {p.danger};
}}

QComboBox::drop-down {{
    border: none;
    width: {s(18)}px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: {s(4)}px solid transparent;
    border-right: {s(4)}px solid transparent;
    border-top: {s(5)}px solid {p.text_dim};
    width: 0; height: 0;
    margin-right: {s(6)}px;
}}
QComboBox QAbstractItemView {{
    background-color: {p.panel};
    border: 1px solid {p.border};
    selection-background-color: {p.accent_dim};
    padding: {s(2)}px;
}}

QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    background-color: {p.raised};
    border: 1px solid {p.border};
    width: {s(14)}px;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background-color: {p.raised_hover};
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    border-left: {s(3)}px solid transparent;
    border-right: {s(3)}px solid transparent;
    border-bottom: {s(4)}px solid {p.text_dim};
    width: 0; height: 0;
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    border-left: {s(3)}px solid transparent;
    border-right: {s(3)}px solid transparent;
    border-top: {s(4)}px solid {p.text_dim};
    width: 0; height: 0;
}}

/* ------------------------------------------------------------- sliders */
QSlider::groove:horizontal {{
    height: {s(4)}px;
    background: {p.raised_pressed};
    border: 1px solid {p.border};
    border-radius: {s(2)}px;
}}
QSlider::sub-page:horizontal {{
    background: {p.accent_dim};
    border: 1px solid {p.border};
    border-radius: {s(2)}px;
}}
QSlider::handle:horizontal {{
    background: {p.text_dim};
    border: 1px solid {p.border};
    width: {s(11)}px;
    margin: -{s(5)}px 0;
    border-radius: {s(2)}px;
}}
QSlider::handle:horizontal:hover {{
    background: {p.text};
}}
QSlider::groove:vertical {{
    width: {s(4)}px;
    background: {p.raised_pressed};
    border: 1px solid {p.border};
    border-radius: {s(2)}px;
}}
QSlider::handle:vertical {{
    background: {p.text_dim};
    border: 1px solid {p.border};
    height: {s(11)}px;
    margin: 0 -{s(5)}px;
    border-radius: {s(2)}px;
}}

/* --------------------------------------------------------- check boxes */
QCheckBox, QRadioButton {{
    spacing: {s(7)}px;
    padding: {s(2)}px 0;
    background: transparent;
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: {s(14)}px;
    height: {s(14)}px;
    border: 1px solid {p.border_light};
    background: {p.raised_pressed};
}}
QCheckBox::indicator {{
    border-radius: {s(2)}px;
}}
QRadioButton::indicator {{
    border-radius: {s(7)}px;
}}
QCheckBox::indicator:checked {{
    background: {p.accent};
    border-color: {p.accent};
}}
QRadioButton::indicator:checked {{
    background: {p.accent};
    border: {s(4)}px solid {p.raised_pressed};
}}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
    border-color: {p.text_disabled};
    background: {p.panel_alt};
}}

/* --------------------------------------------------------------- docks */
QDockWidget {{
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
    color: {p.text};
    font-weight: 600;
}}
QDockWidget::title {{
    background: {p.header};
    padding: {s(6)}px {s(9)}px;
    border-bottom: 1px solid {p.border};
}}
QDockWidget::close-button, QDockWidget::float-button {{
    background: transparent;
    border: none;
    padding: {s(2)}px;
}}
QDockWidget::close-button:hover, QDockWidget::float-button:hover {{
    background: {p.raised};
}}

/* ------------------------------------------------------------ splitter */
QSplitter::handle {{
    background: {p.border};
}}
QSplitter::handle:horizontal {{
    width: {s(3)}px;
}}
QSplitter::handle:vertical {{
    height: {s(3)}px;
}}
QSplitter::handle:hover {{
    background: {p.accent_dim};
}}

/* ---------------------------------------------------------------- tabs */
QTabWidget::pane {{
    border: 1px solid {p.border};
    background: {p.panel};
    top: -1px;
}}
QTabBar::tab {{
    background: {p.header};
    border: 1px solid {p.border};
    border-bottom: none;
    padding: {s(6)}px {s(13)}px;
    margin-right: {s(2)}px;
    color: {p.text_dim};
}}
QTabBar::tab:selected {{
    background: {p.panel};
    color: {p.text};
    border-top: {s(2)}px solid {p.accent};
}}
QTabBar::tab:hover:!selected {{
    background: {p.raised};
}}

/* -------------------------------------------------------------- tables */
QTableWidget, QTableView, QTreeWidget, QTreeView, QListWidget, QListView {{
    background-color: {p.panel_alt};
    alternate-background-color: {p.panel};
    border: 1px solid {p.border};
    gridline-color: {p.border};
    selection-background-color: {p.accent_dim};
    selection-color: {p.text};
}}
QTableWidget::item, QTreeWidget::item, QListWidget::item {{
    padding: {s(3)}px {s(5)}px;
    border: none;
}}
QTableWidget::item:selected, QTreeWidget::item:selected, QListWidget::item:selected {{
    background: {p.accent_dim};
}}
QHeaderView::section {{
    background-color: {p.header};
    color: {p.text_dim};
    padding: {s(5)}px {s(7)}px;
    border: none;
    border-right: 1px solid {p.border};
    border-bottom: 1px solid {p.border};
    font-weight: 600;
}}
QHeaderView::section:hover {{
    background-color: {p.raised};
}}
QTreeView::branch {{
    background: transparent;
}}

/* ---------------------------------------------------------- scrollbars */
QScrollBar:vertical {{
    background: {p.window};
    width: {s(11)}px;
    margin: 0;
    border: none;
}}
QScrollBar::handle:vertical {{
    background: {p.raised};
    min-height: {s(28)}px;
    border-radius: {s(5)}px;
    margin: {s(2)}px;
}}
QScrollBar::handle:vertical:hover {{
    background: {p.raised_hover};
}}
QScrollBar:horizontal {{
    background: {p.window};
    height: {s(11)}px;
    margin: 0;
    border: none;
}}
QScrollBar::handle:horizontal {{
    background: {p.raised};
    min-width: {s(28)}px;
    border-radius: {s(5)}px;
    margin: {s(2)}px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {p.raised_hover};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0; width: 0; border: none; background: none;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
}}

/* ------------------------------------------------------------ group box */
QGroupBox {{
    background-color: {p.panel};
    border: 1px solid {p.border};
    border-radius: {m.radius}px;
    margin-top: {s(11)}px;
    padding-top: {s(9)}px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: {s(9)}px;
    padding: 0 {s(5)}px;
    color: {p.text_dim};
}}

/* ------------------------------------------------------------ progress */
QProgressBar {{
    background-color: {p.raised_pressed};
    border: 1px solid {p.border};
    border-radius: {m.radius}px;
    text-align: center;
    min-height: {s(16)}px;
    color: {p.text};
}}
QProgressBar::chunk {{
    background-color: {p.accent_dim};
    border-radius: {s(2)}px;
}}

/* ---------------------------------------------------------- status bar */
QStatusBar {{
    background: {p.header};
    border-top: 1px solid {p.border};
    color: {p.text_dim};
}}
QStatusBar::item {{
    border: none;
}}
QStatusBar QLabel {{
    padding: 0 {s(7)}px;
    background: transparent;
}}

/* ------------------------------------------------------------ labels */
QLabel {{
    background: transparent;
}}
QLabel[heading="true"] {{
    font-size: {font_size + 4}pt;
    font-weight: 600;
    color: {p.text};
}}
QLabel[subheading="true"] {{
    font-size: {font_size + 1}pt;
    font-weight: 600;
    color: {p.text};
}}
QLabel[dim="true"] {{
    color: {p.text_dim};
}}
QLabel[mono="true"] {{
    font-family: "Consolas", "SF Mono", "DejaVu Sans Mono", monospace;
}}
QLabel[status="ok"]      {{ color: {p.success}; }}
QLabel[status="warn"]    {{ color: {p.warning}; }}
QLabel[status="danger"]  {{ color: {p.danger}; }}
QLabel[status="info"]    {{ color: {p.info}; }}

/* --------------------------------------------------------- collapsible */
QToolButton[section="true"] {{
    background-color: {p.header};
    border: 1px solid {p.border};
    border-radius: {m.radius}px;
    padding: {s(6)}px {s(8)}px;
    text-align: left;
    font-weight: 600;
    color: {p.text};
}}
QToolButton[section="true"]:hover {{
    background-color: {p.raised};
}}
QToolButton[section="true"]:checked {{
    border-bottom-left-radius: 0;
    border-bottom-right-radius: 0;
}}

QFrame[sectionBody="true"] {{
    background-color: {p.panel};
    border: 1px solid {p.border};
    border-top: none;
    border-bottom-left-radius: {m.radius}px;
    border-bottom-right-radius: {m.radius}px;
}}

QFrame[card="true"] {{
    background-color: {p.panel_alt};
    border: 1px solid {p.border};
    border-radius: {m.radius}px;
}}

QFrame[hline="true"] {{
    background: {p.border_light};
    max-height: 1px;
    border: none;
}}
"""


def annotation_pen_colour(base_hex: str, high_contrast: bool) -> str:
    """Lighten an annotation colour when high contrast mode is on."""
    if not high_contrast:
        return base_hex
    c = base_hex.lstrip("#")
    r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    r = min(255, int(r + (255 - r) * 0.35))
    g = min(255, int(g + (255 - g) * 0.35))
    b = min(255, int(b + (255 - b) * 0.35))
    return f"#{r:02X}{g:02X}{b:02X}"


def contrast_ratio(a: str, b: str) -> float:
    """Contrast ratio between two colours, used by the theme self check."""

    def luminance(value: str) -> float:
        value = value.lstrip("#")
        channels = []
        for i in (0, 2, 4):
            v = int(value[i : i + 2], 16) / 255.0
            channels.append(v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4)
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

    la, lb = luminance(a), luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)
