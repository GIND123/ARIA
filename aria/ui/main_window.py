"""The main window: menus, toolbars, module panel, workspace and status bar.

Layout
------
A module panel on the left holds the task specific controls. The workspace in
the middle holds one or more view panes. A data probe runs along the bottom and
a status bar below that carries session and save state.

Modules are switched from a selector in the toolbar, with back and forward
history, so returning to where you were is one click rather than a hunt through
menus.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QStackedWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..core.schema import Presence, Side, get_class
from ..io.image import AVAILABLE_FILTERS
from ..security.auth import Permission
from ..version import APP_LONG_NAME, APP_NAME, APP_VERSION
from .controller import Controller
from .icons import application_icon, icon as make_icon
from .panels.annotate_panel import AnnotatePanel
from .panels.case_browser import CaseBrowser
from .panels.data_probe import DataProbe
from .panels.measure_panel import MeasurePanel
from .theme import METRICS, PALETTE
from .viewer.canvas import Tool
from .viewer.view_frame import LAYOUTS, ViewWorkspace
from .widgets.common import StatusChip, make_tool_button

#: Modules in the order they appear in the selector.
MODULES = [
    ("cases", "Cases", "folder", "Browse and open cases in the project."),
    ("annotate", "Annotate", "point", "Draw and edit annotations on the open case."),
    ("measure", "Measure", "measure", "Calibration, derived measurements and texture features."),
    ("review", "Review", "review", "Review submissions, compare revisions and view agreement."),
    ("export", "Export", "export", "Export annotations, measurements and training bundles."),
    ("administration", "Administration", "settings", "Projects, users, schema and policy."),
    ("audit", "Audit", "audit", "Read the immutable history of every change."),
]

#: Which permission each module needs to be reachable.
MODULE_PERMISSIONS = {
    "cases": Permission.VIEW_CASES,
    "annotate": Permission.VIEW_IMAGE,
    "measure": Permission.VIEW_IMAGE,
    "review": Permission.REVIEW_CASES,
    "export": Permission.EXPORT_DATA,
    "administration": Permission.MANAGE_PROJECTS,
    "audit": Permission.VIEW_AUDIT,
}

#: Tool buttons on the annotation toolbar.
TOOL_BUTTONS = [
    (Tool.SELECT, "cursor", "Select and edit", "S"),
    (Tool.POINT, "point", "Place a point", "1"),
    (Tool.LINE, "line", "Draw a two endpoint line", "2"),
    (Tool.POLYLINE, "polyline", "Trace a contour", "3"),
    (Tool.POLYGON, "polygon", "Outline a region", "4"),
    (Tool.BOX, "box", "Draw a box", "5"),
    (Tool.ROI, "roi", "Place a fixed size analysis region", "6"),
    (Tool.BRUSH, "brush", "Paint a mask", "7"),
    (Tool.ERASER, "eraser", "Erase from a mask", "8"),
    (Tool.RULER, "calibrate", "Measure a known length for calibration", "9"),
]


class MainWindow(QMainWindow):
    """The application shell."""

    closing = Signal()

    def __init__(self, repository, paths, config, session, parent=None):
        super().__init__(parent)
        self.repo = repository
        self.paths = paths
        self.config = config
        self.settings = config.settings
        self.session = session

        self.controller = Controller(repository, paths, config, session, self)
        self._module_history: list = []
        self._history_index = -1
        self._suppress_history = False
        #: Held while the calibration dialog is open, because a ruler line
        #: drawn during that time belongs to the dialog rather than to the
        #: status bar.
        self._calibration_dialog = None
        #: Which side the view was last moved to, so that switching sides can
        #: carry the position within the side across rather than jumping.
        self._last_focused_side = None
        self._suppress_side_focus = False

        self.setWindowTitle(f"{APP_NAME}   {APP_LONG_NAME}")
        self.setWindowIcon(application_icon())
        self.setMinimumSize(QSize(1180, 720))
        self.setDockOptions(
            QMainWindow.AnimatedDocks | QMainWindow.AllowTabbedDocks
        )

        self._build_workspace()
        self._build_module_panel()
        self._build_display_dock()
        self._build_actions()
        self._build_menus()
        self._build_toolbars()
        self._build_status_bar()
        self._connect()

        self._apply_permissions()
        self.set_module("cases")
        self._update_case_actions()

        self._idle_timer = QTimer(self)
        self._idle_timer.setInterval(20_000)
        self._idle_timer.timeout.connect(self._check_idle)
        if self.settings.lock_on_idle:
            self._idle_timer.start()

    # -- construction --------------------------------------------------------

    def _build_workspace(self) -> None:
        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.workspace = ViewWorkspace(central)
        self.probe = DataProbe(self.controller, central)

        layout.addWidget(self.workspace, 1)
        layout.addWidget(self.probe, 0)
        self.setCentralWidget(central)

        self.canvas = self.workspace.main_canvas

    def _build_module_panel(self) -> None:
        # Pinned to the left. A panel that can be dragged off becomes a window
        # of its own, and on Linux, Wayland in particular, it often cannot be
        # dragged back in again.
        self.module_dock = QDockWidget("Modules", self)
        self.module_dock.setObjectName("moduleDock")
        self.module_dock.setAllowedAreas(Qt.LeftDockWidgetArea)
        self.module_dock.setFeatures(QDockWidget.NoDockWidgetFeatures)

        container = QWidget(self.module_dock)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.module_title = QLabel("", container)
        self.module_title.setProperty("subheading", True)
        self.module_title.setContentsMargins(10, 8, 10, 2)
        layout.addWidget(self.module_title)

        self.module_hint = QLabel("", container)
        self.module_hint.setProperty("dim", True)
        self.module_hint.setWordWrap(True)
        self.module_hint.setContentsMargins(10, 0, 10, 6)
        layout.addWidget(self.module_hint)

        self.module_stack = QStackedWidget(container)
        layout.addWidget(self.module_stack, 1)

        self.panels: dict = {}
        self.case_browser = CaseBrowser(self.controller, self)
        self.annotate_panel = AnnotatePanel(self.controller, self)
        self.measure_panel = MeasurePanel(self.controller, self)

        from .panels.review_panel import ReviewPanel
        from .panels.export_panel import ExportPanel
        from .panels.admin_panel import AdminPanel
        from .panels.audit_panel import AuditPanel

        self.review_panel = ReviewPanel(self.controller, self)
        self.export_panel = ExportPanel(self.controller, self)
        self.admin_panel = AdminPanel(self.controller, self)
        self.audit_panel = AuditPanel(self.controller, self)

        for key, panel in (
            ("cases", self.case_browser),
            ("annotate", self.annotate_panel),
            ("measure", self.measure_panel),
            ("review", self.review_panel),
            ("export", self.export_panel),
            ("administration", self.admin_panel),
            ("audit", self.audit_panel),
        ):
            self.panels[key] = panel
            self.module_stack.addWidget(panel)

        self.module_dock.setWidget(container)
        self.module_dock.setMinimumWidth(METRICS.panel_min_width)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.module_dock)
        self.resizeDocks([self.module_dock], [METRICS.panel_width], Qt.Horizontal)

    def _build_display_dock(self) -> None:
        """Brightness, contrast, magnification and sharpness, always to hand.

        These belong beside the image rather than inside a module, because they
        are adjusted while annotating rather than instead of it. Switching to a
        Display module to raise the contrast and back again to place the next
        point would make a reading aid feel like a detour.
        """
        from .panels.display_panel import DisplayPanel

        self.display_dock = QDockWidget("Image display", self)
        self.display_dock.setObjectName("displayDock")
        # Pinned to the right, like the module panel to the left. It can be
        # closed and brought back from the View menu, but not dragged away.
        self.display_dock.setAllowedAreas(Qt.RightDockWidgetArea)
        self.display_dock.setFeatures(QDockWidget.DockWidgetClosable)
        self.display_panel = DisplayPanel(self.controller, self)
        self.display_dock.setWidget(self.display_panel)
        self.display_dock.setMinimumWidth(METRICS.panel_min_width)
        self.addDockWidget(Qt.RightDockWidgetArea, self.display_dock)
        self.resizeDocks([self.display_dock], [METRICS.panel_width], Qt.Horizontal)

        self.display_panel.control_changed.connect(self._on_display_control)
        self.display_panel.reset_requested.connect(self.reset_view)

    def _build_actions(self) -> None:
        def action(text, slot=None, shortcut=None, icon_name="", tip="", checkable=False):
            a = QAction(text, self)
            if icon_name:
                a.setIcon(make_icon(icon_name, 18))
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            if tip:
                a.setToolTip(tip)
                a.setStatusTip(tip)
            a.setCheckable(checkable)
            if slot is not None:
                a.triggered.connect(slot)
            return a

        # File
        self.action_import = action(
            "Import images", self.import_images, "Ctrl+I", "import",
            "Import DICOM or PNG panoramic images into the current project.",
        )
        self.action_import_folder = action(
            "Import folder", self.import_folder, "Ctrl+Shift+I", "folder",
            "Import every supported image in a folder.",
        )
        self.action_close_case = action(
            "Close case", self.close_case, "Ctrl+W", tip="Close the open case."
        )
        self.action_export = action(
            "Export", lambda: self.set_module("export"), "Ctrl+E", "export",
            "Open the export module.",
        )
        self.action_bundle = action(
            "Create training bundle", lambda: self.export_panel.create_bundle(),
            "Ctrl+Shift+B", "bundle",
            "Write a single archive with raw images, labels and metadata.",
        )
        self.action_backup = action(
            "Back up database", self.backup_database, tip="Write a consistent copy of the database."
        )
        self.action_sign_out = action("Sign out", self.sign_out, tip="End this session.")
        self.action_quit = action("Exit", self.close, "Ctrl+Q")

        # Edit
        self.action_undo = action("Undo", self.controller.undo, QKeySequence.Undo, "undo")
        self.action_redo = action("Redo", self.controller.redo, QKeySequence.Redo, "redo")
        self.action_delete = action(
            "Delete selected", self.delete_selected, "Del", "delete",
            "Delete the selected annotation objects.",
        )
        self.action_select_all = action("Select all objects", self.select_all, "Ctrl+A")
        self.action_deselect = action("Deselect", self.deselect_all, "Ctrl+Shift+A")
        self.action_preferences = action(
            "Preferences", self.open_preferences, "Ctrl+,", "settings"
        )

        # View
        self.action_zoom_in = action("Zoom in", lambda: self.canvas.zoom_in(), "Ctrl++", "zoom")
        self.action_zoom_out = action("Zoom out", lambda: self.canvas.zoom_out(), "Ctrl+-", "zoom")
        self.action_fit = action(
            "Fit to window", lambda: self.workspace.apply_to_all(lambda c: c.fit_to_window()),
            "Ctrl+0", "fit",
        )
        self.action_actual_size = action(
            "Actual size", lambda: self.canvas.zoom_to_actual(), "Ctrl+1"
        )
        self.action_reset_view = action(
            "Reset view", self.reset_view, "Ctrl+R", "reset",
            "Return zoom, pan and display settings to their defaults.",
        )
        self.action_invert = action(
            "Invert", self.toggle_invert, "I", "invert",
            "Invert the displayed greyscale. Stored values do not change.",
            checkable=True,
        )
        self.action_original_pixels = action(
            "Show original pixels", self.toggle_original, "O", "image",
            "Show the image with no windowing or filtering, exactly as stored.",
            checkable=True,
        )
        self.action_patient_factors = action(
            "Patient factors", self.edit_patient_factors, "Ctrl+Shift+P", "grade",
            "Age, sex and the other factors an index has to be read against.",
        )
        self.action_calibrate = action(
            "Calibrate", self.start_manual_calibration, "Ctrl+Shift+C", "calibrate",
            "Set the scale from an object of known size, then check it.",
        )
        self.action_reset_layout = action(
            "Reset panel layout", self.reset_panel_layout, tip=
            "Put the side panels back where they started, docked and visible.",
        )
        self.action_labels = action(
            "Show labels", self.toggle_labels, "L", "grade",
            "Show the short code beside each object.", checkable=True,
        )
        self.action_labels.setChecked(True)
        self.action_crosshair = action(
            "Crosshair", self.toggle_crosshair, "C", "point",
            "Show a crosshair at the cursor.", checkable=True,
        )
        self.action_crosshair.setChecked(True)
        self.action_magnifier = action(
            "Magnifier", self.toggle_magnifier, "M", "zoom",
            "Show a magnified inset at the cursor.", checkable=True,
        )
        self.action_snap = action(
            "Snap to contours", self.toggle_snap, "N", "snap", checkable=True,
            tip=(
                "Snap new points onto a traced contour, so index line endpoints "
                "sit on the borders they are measured between."
            ),
        )
        self.action_snap.setChecked(True)
        self.action_probe = action("Data probe", self.toggle_probe, checkable=True)
        self.action_probe.setChecked(True)

        self.layout_group = QActionGroup(self)
        self.layout_actions = {}
        for key, definition in LAYOUTS.items():
            a = action(
                definition["name"],
                lambda _checked=False, k=key: self.set_layout(k),
                icon_name=definition["icon"], tip=definition["description"],
                checkable=True,
            )
            self.layout_group.addAction(a)
            self.layout_actions[key] = a
        self.layout_actions["one_up"].setChecked(True)

        # Annotate
        self.action_construct = action(
            "Construct index line", self.construct_index_line, "Ctrl+G", "construct",
            "Compute the selected index line from the traced contours.",
        )
        self.action_mark_absent = action(
            "Mark selected label absent", self.mark_absent, "Ctrl+M",
            tip="Record the selected label as not visible or not assessable.",
        )
        self.action_next_side = action(
            "Switch side", self.switch_side, "Tab",
            tip="Switch between the right and left side.",
        )
        self.action_submit = action(
            "Submit for review", lambda: self.annotate_panel._on_submit(), "Ctrl+Return",
            "submit",
        )

        # Tools
        self.action_system_check = action(
            "Compatibility check", self.run_system_check, icon_name="diagnostics",
            tip="Check that this workstation meets the requirements.",
        )
        self.action_diagnostics = action(
            "Run diagnostics", self.run_diagnostics, icon_name="diagnostics",
            tip="Run the built in self tests and report the result.",
        )
        self.action_verify_audit = action(
            "Verify audit history", self.verify_audit,
            tip="Recompute the audit chain and report whether it is intact.",
        )
        self.action_integrity = action(
            "Check database integrity", self.check_integrity,
        )

        # Help
        self.action_tour = action(
            "Guided tour", self.start_tour, "F1", "tour",
            "Walk through the workspace step by step.",
        )
        self.action_shortcuts = action("Keyboard shortcuts", self.show_shortcuts, "Ctrl+/")
        self.action_about = action("About ARIA", self.show_about, icon_name="info")
        self.action_user_guide = action("User guide", self.show_user_guide, icon_name="help")

    def _build_menus(self) -> None:
        bar = self.menuBar()

        file_menu = bar.addMenu("&File")
        file_menu.addAction(self.action_import)
        file_menu.addAction(self.action_import_folder)
        file_menu.addSeparator()
        file_menu.addAction(self.action_close_case)
        file_menu.addSeparator()
        file_menu.addAction(self.action_export)
        file_menu.addAction(self.action_bundle)
        file_menu.addSeparator()
        file_menu.addAction(self.action_backup)
        file_menu.addSeparator()
        file_menu.addAction(self.action_sign_out)
        file_menu.addAction(self.action_quit)

        edit_menu = bar.addMenu("&Edit")
        edit_menu.addAction(self.action_undo)
        edit_menu.addAction(self.action_redo)
        edit_menu.addSeparator()
        edit_menu.addAction(self.action_delete)
        edit_menu.addAction(self.action_select_all)
        edit_menu.addAction(self.action_deselect)
        edit_menu.addSeparator()
        edit_menu.addAction(self.action_preferences)

        view_menu = bar.addMenu("&View")
        view_menu.addAction(self.action_zoom_in)
        view_menu.addAction(self.action_zoom_out)
        view_menu.addAction(self.action_fit)
        view_menu.addAction(self.action_actual_size)
        view_menu.addAction(self.action_reset_view)
        view_menu.addSeparator()
        layout_menu = view_menu.addMenu("Layout")
        for a in self.layout_actions.values():
            layout_menu.addAction(a)
        view_menu.addSeparator()
        view_menu.addAction(self.action_invert)
        view_menu.addAction(self.action_original_pixels)
        filter_menu = view_menu.addMenu("Enhancement filter")
        self.filter_group = QActionGroup(self)
        self.filter_actions = {}
        for key, label in AVAILABLE_FILTERS:
            a = QAction(label, self)
            a.setCheckable(True)
            a.triggered.connect(lambda _c=False, k=key: self.set_filter(k))
            self.filter_group.addAction(a)
            filter_menu.addAction(a)
            self.filter_actions[key] = a
        self.filter_actions["none"].setChecked(True)
        view_menu.addSeparator()
        view_menu.addAction(self.action_labels)
        view_menu.addAction(self.action_crosshair)
        view_menu.addAction(self.action_magnifier)
        view_menu.addAction(self.action_probe)
        view_menu.addSeparator()
        view_menu.addAction(self.module_dock.toggleViewAction())
        view_menu.addAction(self.display_dock.toggleViewAction())
        view_menu.addAction(self.action_reset_layout)

        annotate_menu = bar.addMenu("&Annotate")
        self.tool_group = QActionGroup(self)
        self.tool_actions = {}
        for tool, icon_name, label, shortcut in TOOL_BUTTONS:
            a = QAction(label, self)
            a.setCheckable(True)
            a.setIcon(make_icon(icon_name, 18))
            a.setShortcut(QKeySequence(shortcut))
            a.setToolTip(f"{label}   ({shortcut})")
            a.triggered.connect(lambda _c=False, t=tool: self.set_tool(t))
            self.tool_group.addAction(a)
            annotate_menu.addAction(a)
            self.tool_actions[tool] = a
        self.tool_actions[Tool.SELECT].setChecked(True)
        annotate_menu.addSeparator()
        annotate_menu.addAction(self.action_snap)
        annotate_menu.addAction(self.action_construct)
        annotate_menu.addAction(self.action_mark_absent)
        annotate_menu.addAction(self.action_next_side)
        annotate_menu.addSeparator()
        annotate_menu.addSeparator()
        annotate_menu.addAction(self.action_calibrate)
        annotate_menu.addAction(self.action_patient_factors)
        annotate_menu.addSeparator()
        annotate_menu.addAction(self.action_submit)

        review_menu = bar.addMenu("&Review")
        review_menu.addAction(
            QAction("Open review module", self, triggered=lambda: self.set_module("review"))
        )
        review_menu.addSeparator()
        self.action_accept = QAction("Accept this submission", self)
        self.action_accept.triggered.connect(lambda: self.review_panel.accept_case())
        self.action_return = QAction("Return to annotator", self)
        self.action_return.triggered.connect(lambda: self.review_panel.return_case())
        self.action_adjudicate = QAction("Record adjudication", self)
        self.action_adjudicate.triggered.connect(lambda: self.review_panel.adjudicate_case())
        review_menu.addAction(self.action_accept)
        review_menu.addAction(self.action_return)
        review_menu.addAction(self.action_adjudicate)
        review_menu.addSeparator()
        review_menu.addAction(
            QAction("Agreement report", self, triggered=lambda: self.review_panel.build_agreement())
        )

        tools_menu = bar.addMenu("&Tools")
        tools_menu.addAction(self.action_system_check)
        tools_menu.addAction(self.action_diagnostics)
        tools_menu.addSeparator()
        tools_menu.addAction(self.action_verify_audit)
        tools_menu.addAction(self.action_integrity)
        tools_menu.addSeparator()
        tools_menu.addAction(
            QAction("Open data folder", self, triggered=self.open_data_folder)
        )

        help_menu = bar.addMenu("&Help")
        help_menu.addAction(self.action_tour)
        help_menu.addAction(self.action_user_guide)
        help_menu.addAction(self.action_shortcuts)
        help_menu.addSeparator()
        help_menu.addAction(self.action_about)

    def _build_toolbars(self) -> None:
        # Navigation and module selection.
        nav = QToolBar("Navigation", self)
        nav.setObjectName("navToolbar")
        nav.setIconSize(QSize(METRICS.toolbar_icon, METRICS.toolbar_icon))
        nav.setMovable(False)

        self.back_button = make_tool_button("back", "Go back to the previous module", parent=self)
        self.forward_button = make_tool_button("forward", "Go forward", parent=self)
        nav.addWidget(self.back_button)
        nav.addWidget(self.forward_button)
        nav.addSeparator()

        label = QLabel("  Module  ", self)
        label.setProperty("dim", True)
        nav.addWidget(label)

        self.module_combo = QComboBox(self)
        self.module_combo.setMinimumWidth(190)
        for key, name, icon_name, tip in MODULES:
            self.module_combo.addItem(make_icon(icon_name, 16), name, key)
            self.module_combo.setItemData(
                self.module_combo.count() - 1, tip, Qt.ToolTipRole
            )
        nav.addWidget(self.module_combo)
        nav.addSeparator()
        nav.addAction(self.action_import)
        nav.addAction(self.action_export)
        nav.addSeparator()
        nav.addAction(self.action_undo)
        nav.addAction(self.action_redo)

        spacer = QWidget(self)
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        nav.addWidget(spacer)

        self.case_label = QLabel("No case open", self)
        self.case_label.setProperty("dim", True)
        nav.addWidget(self.case_label)
        nav.addSeparator()
        nav.addAction(self.action_tour)
        self.addToolBar(Qt.TopToolBarArea, nav)
        self.nav_toolbar = nav

        # Tools and display.
        tools = QToolBar("Tools", self)
        tools.setObjectName("toolsToolbar")
        tools.setIconSize(QSize(METRICS.toolbar_icon, METRICS.toolbar_icon))
        tools.setMovable(False)
        for tool, _icon, _label, _shortcut in TOOL_BUTTONS:
            tools.addAction(self.tool_actions[tool])
        tools.addSeparator()
        tools.addAction(self.action_snap)
        tools.addAction(self.action_construct)
        tools.addSeparator()
        for a in (
            self.action_fit, self.action_reset_view, self.action_invert,
            self.action_magnifier,
        ):
            tools.addAction(a)
        tools.addSeparator()
        for key in ("one_up", "side_by_side", "two_by_two"):
            tools.addAction(self.layout_actions[key])
        self.addToolBar(Qt.TopToolBarArea, tools)
        self.tools_toolbar = tools

        # The side and label selectors go on their own row. Crowding them onto
        # the tool row pushes it into an overflow menu at ordinary window
        # widths, which hides the tools an annotator reaches for constantly.
        self.addToolBarBreak(Qt.TopToolBarArea)
        context = QToolBar("Current label", self)
        context.setObjectName("contextToolbar")
        context.setIconSize(QSize(METRICS.toolbar_icon, METRICS.toolbar_icon))
        context.setMovable(False)

        side_label = QLabel("  Side  ", self)
        side_label.setProperty("dim", True)
        context.addWidget(side_label)
        self.side_combo = QComboBox(self)
        self.side_combo.setMinimumWidth(110)
        for side in (Side.RIGHT, Side.LEFT, Side.MIDLINE):
            self.side_combo.addItem(side.display, side.value)
        self.side_combo.setToolTip(
            "The side new annotations are placed on. Right is drawn with a solid "
            "stroke and left with a dashed stroke."
        )
        context.addWidget(self.side_combo)

        class_label = QLabel("    Label  ", self)
        class_label.setProperty("dim", True)
        context.addWidget(class_label)
        self.class_combo = QComboBox(self)
        self.class_combo.setMinimumWidth(260)
        context.addWidget(self.class_combo)

        context.addSeparator()
        context.addAction(self.action_construct)
        context.addAction(self.action_mark_absent)
        context.addSeparator()
        context.addAction(self.action_submit)

        context_spacer = QWidget(self)
        context_spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        context.addWidget(context_spacer)

        self.validation_label = QLabel("", self)
        self.validation_label.setProperty("dim", True)
        context.addWidget(self.validation_label)

        self.addToolBar(Qt.TopToolBarArea, context)
        self.context_toolbar = context

    def _build_status_bar(self) -> None:
        bar = self.statusBar()
        bar.setSizeGripEnabled(True)

        self.status_state = StatusChip("", "neutral", self)
        self.status_save = StatusChip("Saved", "ok", self)
        self.status_lock = StatusChip("", "neutral", self)
        self.status_user = QLabel("", self)
        self.status_user.setProperty("dim", True)

        bar.addPermanentWidget(self.status_lock)
        bar.addPermanentWidget(self.status_state)
        bar.addPermanentWidget(self.status_save)
        bar.addPermanentWidget(self.status_user)

        user = self.session.user if self.session else None
        if user is not None:
            self.status_user.setText(
                f"{user.display_name} ({user.role_display}) as {user.pseudonym}"
            )
        bar.showMessage("Ready", 3000)

    # -- wiring --------------------------------------------------------------

    def _connect(self) -> None:
        c = self.controller

        c.case_opened.connect(self._on_case_opened)
        c.case_closed.connect(self._on_case_closed)
        c.image_loaded.connect(self._on_image_loaded)
        c.annotation_added.connect(self._on_annotation_added)
        c.annotation_updated.connect(self._on_annotation_updated)
        c.annotation_removed.connect(self._on_annotation_removed)
        c.annotations_changed.connect(self._refresh_canvas_items)
        c.status_message.connect(lambda m, t: self.statusBar().showMessage(m, t))
        c.error_raised.connect(self._show_error)
        c.dirty_changed.connect(self._on_dirty)
        c.saved.connect(self._on_saved)
        c.undo_state_changed.connect(self._on_undo_state)
        c.read_only_changed.connect(self._on_read_only)
        c.measurements_changed.connect(lambda _m: None)
        c.validation_changed.connect(self._on_validation)

        self.module_combo.currentIndexChanged.connect(
            lambda _i: self.set_module(self.module_combo.currentData())
        )
        self.back_button.clicked.connect(self.go_back)
        self.forward_button.clicked.connect(self.go_forward)

        self.case_browser.case_open_requested.connect(self.open_case)
        self.case_browser.import_requested.connect(self.import_images)

        self.annotate_panel.class_activated.connect(self._on_class_activated)
        self.annotate_panel.focus_requested.connect(self.focus_annotation)

        self.measure_panel.calibrate_requested.connect(self.start_manual_calibration)
        self.measure_panel.texture_requested.connect(self.compute_texture)

        self.side_combo.currentIndexChanged.connect(self._on_side_combo)
        self.class_combo.currentIndexChanged.connect(self._on_class_combo)

        for pane in self.workspace.panes.values():
            canvas = pane.canvas
            canvas.cursor_moved.connect(self.probe.update_cursor)
            canvas.cursor_left.connect(self.probe.clear)
            canvas.zoom_changed.connect(self.probe.update_zoom)
            canvas.display_settings_changed.connect(self._on_display_changed)
            canvas.status_message.connect(lambda m: self.statusBar().showMessage(m, 4000))
            canvas.annotation_created.connect(self._on_canvas_created)
            canvas.annotation_edited.connect(self._on_canvas_edited)
            canvas.annotation_edit_finished.connect(self._on_canvas_edit_finished)
            canvas.selection_changed.connect(self._on_canvas_selection)
            canvas.ruler_measured.connect(self._on_ruler)

        # Only the main canvas drives the magnification slider. Letting the
        # side panes drive it as well would have the three of them overwriting
        # each other every time the layout changed.
        self.canvas.zoom_changed.connect(self._on_zoom_changed)

        # Every pane shows the value a hand drawn measurement carries.
        for pane in self.workspace.panes.values():
            pane.canvas.set_value_provider(self._measurement_label)

        self.controller.calibration_changed.connect(self._on_calibration_for_labels)

    # -- modules -------------------------------------------------------------

    def set_module(self, key: str) -> None:
        if key not in self.panels:
            return
        panel = self.panels[key]
        self.module_stack.setCurrentWidget(panel)

        for module_key, name, _icon, tip in MODULES:
            if module_key == key:
                self.module_title.setText(name)
                self.module_hint.setText(tip)
                break

        index = self.module_combo.findData(key)
        if index >= 0 and self.module_combo.currentIndex() != index:
            self.module_combo.blockSignals(True)
            self.module_combo.setCurrentIndex(index)
            self.module_combo.blockSignals(False)

        if not self._suppress_history:
            self._module_history = self._module_history[: self._history_index + 1]
            if not self._module_history or self._module_history[-1] != key:
                self._module_history.append(key)
                self._history_index = len(self._module_history) - 1
        self._update_history_buttons()

        if key == "cases":
            self.case_browser.reload_projects()
        elif key == "review":
            self.review_panel.refresh()
        elif key == "audit":
            self.audit_panel.refresh()
        elif key == "administration":
            self.admin_panel.refresh()
        elif key == "export":
            self.export_panel.refresh()

    def go_back(self) -> None:
        if self._history_index > 0:
            self._history_index -= 1
            self._suppress_history = True
            self.set_module(self._module_history[self._history_index])
            self._suppress_history = False
            self._update_history_buttons()

    def go_forward(self) -> None:
        if self._history_index < len(self._module_history) - 1:
            self._history_index += 1
            self._suppress_history = True
            self.set_module(self._module_history[self._history_index])
            self._suppress_history = False
            self._update_history_buttons()

    def _update_history_buttons(self) -> None:
        self.back_button.setEnabled(self._history_index > 0)
        self.forward_button.setEnabled(
            self._history_index < len(self._module_history) - 1
        )

    def _apply_permissions(self) -> None:
        """Hide modules this account cannot use, rather than showing failures."""
        user = self.session.user if self.session else None
        for index in reversed(range(self.module_combo.count())):
            key = self.module_combo.itemData(index)
            permission = MODULE_PERMISSIONS.get(key)
            if permission is not None and not self.controller.can(permission):
                self.module_combo.removeItem(index)

        for a in (
            self.action_construct, self.action_submit,
            self.action_mark_absent, self.action_delete,
        ):
            a.setEnabled(self.controller.can(Permission.EDIT_ANNOTATIONS))
        self.action_import.setEnabled(self.controller.can(Permission.IMPORT_CASES))
        self.action_import_folder.setEnabled(self.controller.can(Permission.IMPORT_CASES))
        self.action_export.setEnabled(self.controller.can(Permission.EXPORT_DATA))
        self.action_bundle.setEnabled(self.controller.can(Permission.CREATE_BUNDLE))
        self.action_verify_audit.setEnabled(self.controller.can(Permission.VERIFY_AUDIT))
        self.tools_toolbar.setVisible(self.controller.can(Permission.VIEW_IMAGE))

    # -- case lifecycle ------------------------------------------------------

    def open_case(self, case_id: str, read_only: bool = False) -> None:
        if self.controller.case_data is not None:
            self.controller.close_case()
        if self.controller.open_case(case_id, force_read_only=read_only):
            self.set_module("annotate")

    def close_case(self) -> None:
        self.controller.close_case()
        self.set_module("cases")

    def _on_case_opened(self, data) -> None:
        self.case_label.setText(
            f"{data.case.pseudonym}   {data.case.state_enum.display}"
        )
        self.status_state.set_state(
            data.case.state_enum.display,
            {"accepted": "ok", "adjudicated": "ok", "submitted": "info",
             "returned": "warn"}.get(data.case.state, "neutral"),
        )
        self._populate_class_combo()
        self._update_case_actions()
        self.setWindowTitle(
            f"{APP_NAME}   {data.case.pseudonym}   {APP_LONG_NAME}"
        )

    def _on_case_closed(self) -> None:
        self.display_panel.set_enabled_for_case(False)
        self.canvas.set_image(None)
        for pane in self.workspace.panes.values():
            pane.canvas.set_image(None)
        self.case_label.setText("No case open")
        self.status_state.set_state("", "neutral")
        self.status_lock.set_state("", "neutral")
        self.probe.clear()
        self._update_case_actions()
        self.setWindowTitle(f"{APP_NAME}   {APP_LONG_NAME}")

    def _on_image_loaded(self, image) -> None:
        settings = self.controller.display_settings
        self.canvas.set_image(image, settings)
        self.canvas.load_annotations(self.controller.case_data.live_annotations())
        self.canvas.roi_size = self.controller.schema.roi_size_px
        self.probe.update_display(settings)
        self.probe.update_zoom(self.canvas.zoom_factor())
        self.action_invert.setChecked(settings.invert)
        self.display_panel.set_enabled_for_case(True)
        self.display_panel.load_from(settings, zoom=self.canvas.zoom_factor())

        for key in ("right", "left", "overview", "compare"):
            pane = self.workspace.panes.get(key)
            if pane is None or not pane.isVisible():
                continue
            pane.canvas.set_image(image, settings)
            pane.canvas.load_annotations(self.controller.case_data.live_annotations())
            pane.canvas.read_only = True
            if key == "right":
                pane.canvas.zoom_to_rect(_region_rect(image, 0.10, 0.45))
            elif key == "left":
                pane.canvas.zoom_to_rect(_region_rect(image, 0.55, 0.90))

    def _update_case_actions(self) -> None:
        has_case = self.controller.case_data is not None
        editable = has_case and not self.controller.read_only
        for a in (
            self.action_close_case, self.action_zoom_in, self.action_zoom_out,
            self.action_fit, self.action_actual_size, self.action_reset_view,
            self.action_invert, self.action_original_pixels, self.action_labels,
            self.action_magnifier, self.action_crosshair,
        ):
            a.setEnabled(has_case)
        for a in (
            self.action_delete, self.action_construct, self.action_mark_absent,
            self.action_submit, self.action_calibrate, self.action_patient_factors,
        ):
            a.setEnabled(editable)
        self.tools_toolbar.setEnabled(has_case)

    def _on_read_only(self, read_only: bool, reason: str) -> None:
        for canvas in (p.canvas for p in self.workspace.panes.values()):
            canvas.read_only = read_only
        self.status_lock.set_state(
            "Read only" if read_only else "Editing",
            "warn" if read_only else "ok",
            reason or "This case is open for editing.",
        )
        self._update_case_actions()

    # -- canvas bridge -------------------------------------------------------

    def _on_canvas_created(self, annotation) -> None:
        self.controller.add_annotation(annotation)

    def _on_canvas_edited(self, annotation_id: str, points) -> None:
        self.controller.update_annotation_points(annotation_id, points, commit=False)

    def _on_canvas_edit_finished(self, annotation_id: str) -> None:
        item = self.canvas.item_for(annotation_id)
        if item is not None:
            self.controller.update_annotation_points(annotation_id, item.points(), commit=True)

    def _on_canvas_selection(self, ids) -> None:
        if not ids:
            return
        annotation = self.controller._find(ids[0])
        if annotation is None:
            return
        try:
            cls = get_class(annotation.class_key)
            self.statusBar().showMessage(
                f"{cls.display_name} ({Side(annotation.side).display}), "
                f"{len(annotation.points())} points, revision {annotation.revision}",
                6000,
            )
        except KeyError:
            pass

    def _on_annotation_added(self, annotation) -> None:
        self.canvas.add_annotation_item(annotation)
        self.canvas.cancel_pending()

    def _on_annotation_updated(self, annotation) -> None:
        self.canvas.update_annotation_item(annotation)

    def _on_annotation_removed(self, annotation_id: str) -> None:
        self.canvas.remove_annotation_item(annotation_id)

    def _refresh_canvas_items(self) -> None:
        data = self.controller.case_data
        if data is None:
            return
        live = {a.id for a in data.live_annotations()}
        for annotation_id in list(self.canvas._items.keys()):
            if annotation_id not in live:
                self.canvas.remove_annotation_item(annotation_id)
        for annotation in data.live_annotations():
            if annotation.id in self.canvas._items:
                self.canvas.update_annotation_item(annotation)
            else:
                self.canvas.add_annotation_item(annotation)

        for key in ("right", "left", "overview", "compare"):
            pane = self.workspace.panes.get(key)
            if pane is not None and pane.isVisible():
                pane.canvas.load_annotations(data.live_annotations())

    def focus_annotation(self, annotation_id: str) -> None:
        item = self.canvas.item_for(annotation_id)
        if item is None:
            return
        self.canvas.select_annotation(annotation_id)
        rect = item.boundingRect()
        if rect.width() < 60 or rect.height() < 60:
            centre = rect.center()
            self.canvas.centre_on_scene(centre.x(), centre.y())
        else:
            self.canvas.zoom_to_rect(rect, margin=2.0)

    # -- tools and display ---------------------------------------------------

    def set_tool(self, tool: str) -> None:
        self.canvas.set_tool(tool)
        if tool in self.tool_actions:
            self.tool_actions[tool].setChecked(True)
        hints = {
            Tool.SELECT: "Click to select. Drag a handle to move a point. Shift drag to move the whole object. Alt click a handle to delete it.",
            Tool.POINT: "Click to place the landmark.",
            Tool.LINE: "Click the start, then the end.",
            Tool.POLYLINE: "Click along the contour. Press Enter or double click to finish, Escape to cancel.",
            Tool.POLYGON: "Click around the region. Press Enter or double click to close it.",
            Tool.BOX: "Drag to draw the box.",
            Tool.ROI: f"Click to place a {self.controller.schema.roi_size_px} by {self.controller.schema.roi_size_px} pixel region.",
            Tool.BRUSH: "Drag to paint. Control and the wheel change the brush size.",
            Tool.ERASER: "Drag to erase. Control and the wheel change the size.",
            Tool.RULER: "Click both ends of a known length to calibrate.",
        }
        if tool in hints:
            self.statusBar().showMessage(hints[tool], 8000)

    def _on_class_activated(self, class_key: str, side) -> None:
        self.canvas.set_active_class(class_key, side)
        self.focus_side(side)
        try:
            cls = get_class(class_key)
        except KeyError:
            return
        index = self.class_combo.findData(class_key)
        if index >= 0:
            self.class_combo.blockSignals(True)
            self.class_combo.setCurrentIndex(index)
            self.class_combo.blockSignals(False)
        index = self.side_combo.findData(side.value)
        if index >= 0:
            self.side_combo.blockSignals(True)
            self.side_combo.setCurrentIndex(index)
            self.side_combo.blockSignals(False)

        tool = {
            "point": Tool.POINT, "line": Tool.LINE, "polyline": Tool.POLYLINE,
            "polygon": Tool.POLYGON, "box": Tool.BOX, "roi_rect": Tool.ROI,
            "mask": Tool.BRUSH,
        }.get(cls.geometry.value)
        if tool and self.canvas.active_tool != Tool.SELECT:
            self.set_tool(tool)

    def _populate_class_combo(self) -> None:
        self.class_combo.blockSignals(True)
        self.class_combo.clear()
        for cls in self.controller.schema.active_classes():
            self.class_combo.addItem(
                make_icon("point", 14, cls.colour), cls.display_name, cls.key
            )
            self.class_combo.setItemData(
                self.class_combo.count() - 1, cls.description, Qt.ToolTipRole
            )
        self.class_combo.blockSignals(False)

    def _on_side_combo(self, _index: int) -> None:
        side = Side(self.side_combo.currentData())
        self.annotate_panel.set_side(side)

    def _on_class_combo(self, _index: int) -> None:
        key = self.class_combo.currentData()
        if key:
            self.annotate_panel.active_class = key
            self.annotate_panel._select_class(key)

    #: Where the middle of each side sits across the image width. Patient right
    #: is on the left of a panoramic image, which is why these are not the
    #: order a reader expects.
    SIDE_CENTRES = {"R": 0.28, "L": 0.72, "M": 0.50}

    def _whole_image_visible(self) -> bool:
        """True when the image already fits, so there is nothing to move to."""
        image = self.controller.image
        if image is None:
            return True
        viewport = self.canvas.viewport().rect()
        if viewport.width() < 4 or image.columns <= 0:
            return True
        fit = viewport.width() / float(image.columns)
        return self.canvas.zoom_factor() <= fit * 1.05

    def focus_side(self, side) -> None:
        """Move the view to the side being annotated.

        Choosing a side used to change only which label the next object was
        given. On a magnified image that left somebody looking at one half of
        the mandible while drawing on the other.

        When the image is magnified, the position within the side is carried
        across, so leaving the right antegonial region arrives at the left
        antegonial region rather than at the middle of the left side. Zoomed
        out far enough to see the whole mandible there is nothing to move to,
        so nothing moves.
        """
        image = self.controller.image
        if image is None or self._suppress_side_focus:
            return

        key = getattr(side, "value", str(side))
        target = self.SIDE_CENTRES.get(key)
        if target is None:
            return

        if self._whole_image_visible():
            self._last_focused_side = side
            return

        canvas = self.canvas
        current = canvas.mapToScene(canvas.viewport().rect().center())
        previous = self._last_focused_side
        source = self.SIDE_CENTRES.get(getattr(previous, "value", str(previous)))

        if source is not None and abs(source - target) > 1e-6:
            offset = current.x() - image.columns * source
            if "M" not in (key, getattr(previous, "value", "")):
                offset = -offset        # the two sides are mirror images
            x = image.columns * target + offset
        else:
            x = image.columns * target

        x = max(0.0, min(float(image.columns), x))
        canvas.centre_on_scene(x, current.y())
        self._last_focused_side = side

        name = getattr(side, "display", key)
        self.statusBar().showMessage(f"Showing the {name.lower()} side.", 3000)

    def switch_side(self) -> None:
        current = Side(self.side_combo.currentData())
        nxt = Side.LEFT if current is Side.RIGHT else Side.RIGHT
        self.annotate_panel.set_side(nxt)

    def construct_index_line(self) -> None:
        self.controller.construct_index_line(
            self.annotate_panel.active_class, self.annotate_panel.active_side
        )

    def mark_absent(self) -> None:
        self.annotate_panel.presence_section.set_expanded(True)
        self.annotate_panel._on_presence_apply()

    def delete_selected(self) -> None:
        for annotation_id in self.canvas.selected_ids():
            self.controller.delete_annotation(annotation_id)

    def select_all(self) -> None:
        for item in self.canvas._items.values():
            item.setSelected(True)

    def deselect_all(self) -> None:
        self.canvas._scene.clearSelection()

    def set_layout(self, key: str) -> None:
        self.workspace.set_layout(key)
        self.canvas = self.workspace.main_canvas
        self.settings.layout = key
        if self.controller.image is not None:
            self._on_image_loaded(self.controller.image)

    def toggle_invert(self) -> None:
        settings = self.controller.display_settings
        settings.invert = self.action_invert.isChecked()
        self._apply_display(settings)

    def toggle_original(self) -> None:
        settings = self.controller.display_settings
        settings.show_original_pixels = self.action_original_pixels.isChecked()
        self._apply_display(settings)
        if settings.show_original_pixels:
            self.statusBar().showMessage(
                "Showing the image exactly as stored, with no windowing or filtering.",
                6000,
            )

    def set_filter(self, key: str) -> None:
        settings = self.controller.display_settings
        settings.filter_name = key
        self._apply_display(settings)

    def _apply_display(self, settings) -> None:
        self.controller.display_settings = settings
        for pane in self.workspace.visible_panes():
            pane.canvas.apply_display_settings(settings)
        self.probe.update_display(settings)
        self.display_panel.load_from(settings)
        if self.controller.case_data is not None:
            self.repo.set_display_settings(
                self.controller.case_data.case.id, settings.to_dict()
            )

    def _on_display_control(self, key: str) -> None:
        """One of the four sliders moved.

        Magnification is a property of the view and the other three are
        properties of the drawing, so they take different routes: the first
        changes the transform, the rest are written into the display settings
        and persisted with the case.
        """
        if self.controller.image is None:
            return

        if key == "magnification":
            # The main pane only, which is the one the slider reads back from.
            # In the four pane layout the side panes are framed deliberately on
            # the right and left mandible, and driving them from here would
            # flatten all three to one magnification.
            self.canvas.set_zoom(self.display_panel.zoom_factor())
            return

        settings = self.controller.display_settings
        settings.brightness = self.display_panel.brightness()
        settings.contrast = self.display_panel.contrast()

        sharpness = self.display_panel.sharpness()
        if sharpness > 0.0:
            # Sharpening is the unsharp mask that the image pipeline already
            # carries, driven from here rather than from the filter menu.
            settings.filter_name = "unsharp"
            settings.filter_strength = sharpness
        elif settings.filter_name == "unsharp":
            settings.filter_name = "none"

        self._apply_display(settings)
        if hasattr(self, "filter_actions") and settings.filter_name in self.filter_actions:
            self.filter_actions[settings.filter_name].setChecked(True)

    def _measurement_label(self, annotation) -> str:
        """The value drawn beside a measurement, and nothing beside anything
        else: an anatomical landmark is not a number to be read off."""
        from ..core.schema import MEASUREMENT_CLASSES

        if annotation.class_key not in MEASUREMENT_CLASSES:
            return ""
        return self.controller.measurement_text(annotation, short=True)

    def _on_calibration_for_labels(self, _calibration) -> None:
        """A new scale changes every measurement already on the image."""
        for pane in self.workspace.panes.values():
            pane.canvas.refresh_value_labels()

    def _on_zoom_changed(self, factor: float) -> None:
        """Zooming by wheel, keyboard or toolbar moves the slider too, so the
        panel never claims a magnification the image is not at."""
        self.display_panel.load_from(self.controller.display_settings, zoom=factor)

    def _on_display_changed(self, settings) -> None:
        self.controller.display_settings = settings
        self.probe.update_display(settings)
        self.display_panel.load_from(settings)

    def toggle_labels(self) -> None:
        visible = self.action_labels.isChecked()
        for pane in self.workspace.panes.values():
            pane.canvas.set_labels_visible(visible)

    def toggle_crosshair(self) -> None:
        for pane in self.workspace.panes.values():
            pane.canvas.crosshair_enabled = self.action_crosshair.isChecked()
            pane.canvas.viewport().update()

    def toggle_magnifier(self) -> None:
        for pane in self.workspace.panes.values():
            pane.canvas.magnifier_enabled = self.action_magnifier.isChecked()
            pane.canvas.viewport().update()

    def toggle_snap(self) -> None:
        enabled = self.action_snap.isChecked()
        for pane in self.workspace.panes.values():
            pane.canvas.snap_enabled = enabled
        self.statusBar().showMessage(
            "Snapping to contours is on." if enabled else "Snapping is off.", 4000
        )

    def toggle_probe(self) -> None:
        self.probe.setVisible(self.action_probe.isChecked())

    def reset_view(self) -> None:
        if self.controller.image is None:
            return
        settings = self.controller.image.default_display_settings()
        self._apply_display(settings)
        for pane in self.workspace.visible_panes():
            pane.canvas.fit_to_window()
        self.action_invert.setChecked(settings.invert)
        self.action_original_pixels.setChecked(False)
        self.filter_actions["none"].setChecked(True)

    # -- calibration ---------------------------------------------------------

    def start_manual_calibration(self) -> None:
        """Open the calibration dialog, which sets a scale and then checks it."""
        if self.controller.case_data is None:
            return
        from .dialogs.calibration_dialog import CalibrationDialog

        # Shown rather than exec'd. The dialog steps out of the way so the
        # object can be drawn on the image underneath it, and an exec'd dialog
        # is application modal, so the canvas would never see the drag.
        dialog = CalibrationDialog(self.controller, self)
        dialog.measure_requested.connect(
            lambda _slot: self._begin_calibration_line(dialog)
        )
        dialog.accepted.connect(lambda: self._on_calibration_accepted(dialog))
        dialog.finished.connect(lambda _result: self._end_calibration())
        self._calibration_dialog = dialog
        dialog.show()

    def _on_calibration_accepted(self, dialog) -> None:
        if dialog.calibration is not None:
            self._accept_calibration(dialog.calibration)

    def _end_calibration(self) -> None:
        self._calibration_dialog = None
        self.set_tool(Tool.SELECT)

    def edit_patient_factors(self) -> None:
        """Open the factor sheet for the open case."""
        if self.controller.case_data is None:
            self.statusBar().showMessage(
                "Open a case before recording patient factors.", 5000
            )
            return
        from .dialogs.patient_dialog import PatientFactorsDialog

        PatientFactorsDialog(
            self.controller, self.controller.case_data.case, self
        ).exec()

    def _begin_calibration_line(self, dialog) -> None:
        """Put the ruler in the person's hand for the dialog's next line."""
        self.set_tool(Tool.RULER)
        self.statusBar().showMessage(
            "Click the two ends of the object. The calibration window comes "
            "back when you have.",
            20000,
        )

    def _accept_calibration(self, calibration) -> None:
        detail = (
            f"Manual calibration, {calibration.reference_description}. "
            f"{calibration.verification_line()}"
        )
        self.controller.set_calibration(calibration, detail)
        self.set_tool(Tool.SELECT)
        if not calibration.has_verification:
            self.statusBar().showMessage(
                "The scale is set but unchecked. Its accuracy is unmeasured "
                "until a second object of known size is measured against it.",
                12000,
            )
        elif not calibration.is_verified:
            self.statusBar().showMessage(calibration.verification_line(), 15000)
        else:
            self.statusBar().showMessage(
                f"Calibrated and checked. {calibration.verification_line()}",
                10000,
            )
        self.set_module("measure")

    def _on_ruler(self, length_px: float, start, end) -> None:
        """A ruler line has been drawn.

        While the calibration dialog is open the line belongs to whichever slot
        asked for it. Otherwise it is an ordinary measurement and the status bar
        reports it.
        """
        dialog = getattr(self, "_calibration_dialog", None)
        if dialog is not None:
            if length_px < 2:
                self.statusBar().showMessage(
                    "That line is too short to calibrate from. Draw it again.",
                    6000,
                )
                dialog.cancel_measurement()
                return
            dialog.accept_measurement(end[0] - start[0], end[1] - start[1])
            return

        if self.controller.case_data is None:
            return

        # The line is kept, as an annotation like any other. It used to vanish
        # the moment the second point landed, with its value shown once in the
        # status bar and then gone, so a measurement could be taken but never
        # read back, checked or exported.
        from ..core.models import Annotation

        annotation = Annotation(
            set_id=self.controller.case_data.annotation_set.id,
            class_key="free_measurement",
            side="NA",
            geometry_type="line",
        )
        annotation.set_points([tuple(start), tuple(end)])
        if not self.controller.add_annotation(annotation):
            return

        self.statusBar().showMessage(
            f"Measured {self.controller.measurement_text(annotation)}. "
            f"It is kept with the case and listed under Objects.",
            8000,
        )

    # -- texture -------------------------------------------------------------

    def compute_texture(self) -> None:
        data = self.controller.case_data
        image = self.controller.image
        if data is None or image is None:
            return

        regions = [
            a for a in data.live_annotations()
            if a.class_key in ("trabecular_roi", "crestal_roi", "symphysis_roi")
            and a.is_assessable and len(a.points()) >= 2
        ]
        if not regions:
            QMessageBox.information(
                self, "No regions to analyse",
                "Texture features are computed from the analysis regions you draw.\n\n"
                "Place a trabecular, crestal or symphysis region first. Keep it "
                "clear of roots, the mandibular canal, the cortical border, "
                "lesions and artefacts.",
            )
            return

        from ..core.texture import compute_region_features

        analysis = image.analysis_array()
        schema = self.controller.schema
        results = []
        self.measure_panel.texture_progress.setVisible(True)
        self.measure_panel.texture_progress.setRange(0, len(regions))
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for index, region in enumerate(regions):
                points = region.points()
                x0 = min(p[0] for p in points)
                y0 = min(p[1] for p in points)
                x1 = max(p[0] for p in points)
                y1 = max(p[1] for p in points)
                result = compute_region_features(
                    analysis, int(round(x0)), int(round(y0)),
                    int(round(x1 - x0)), int(round(y1 - y0)),
                    requested=schema.texture_features,
                    region_id=region.id, region_class=region.class_key,
                    side=region.side,
                )
                results.append(result)
                self.measure_panel.texture_progress.setValue(index + 1)
                QApplication.processEvents()
        finally:
            QApplication.restoreOverrideCursor()
            self.measure_panel.texture_progress.setVisible(False)

        self.repo.store_texture(data.annotation_set.id, results)
        self.measure_panel.show_texture([r.to_dict() for r in results])
        self.statusBar().showMessage(
            f"Texture features computed for {len(results)} "
            f"{'region' if len(results) == 1 else 'regions'}.",
            6000,
        )

    # -- import --------------------------------------------------------------

    def import_images(self) -> None:
        if self.controller.project is None:
            QMessageBox.information(
                self, "No project",
                "Create or select a project before importing images.\n\n"
                "Projects are managed in the Administration module.",
            )
            return
        paths, _filter = QFileDialog.getOpenFileNames(
            self, "Import panoramic images", str(self.paths.data_dir),
            "Supported images (*.dcm *.dicom *.png);;DICOM (*.dcm *.dicom);;PNG (*.png);;All files (*)",
        )
        if paths:
            self._run_import(paths)

    def import_folder(self) -> None:
        if self.controller.project is None:
            QMessageBox.information(
                self, "No project", "Create or select a project before importing images."
            )
            return
        folder = QFileDialog.getExistingDirectory(
            self, "Import every supported image in a folder", str(self.paths.data_dir)
        )
        if not folder:
            return
        from pathlib import Path

        candidates = [
            str(p) for p in sorted(Path(folder).rglob("*"))
            if p.is_file() and p.suffix.lower() in (".dcm", ".dicom", ".png", "")
        ]
        if not candidates:
            QMessageBox.information(
                self, "Nothing to import",
                f"No DICOM or PNG files were found in {folder}.",
            )
            return
        self._run_import(candidates)

    def _run_import(self, paths) -> None:
        from .dialogs.import_dialog import ImportDialog

        dialog = ImportDialog(self.controller, paths, self)
        dialog.exec()
        self.case_browser.refresh()
        self.controller.case_list_changed.emit()

        # Land on what was just imported, so the next click opens it rather
        # than acting on nothing. Importing while a case is open used to leave
        # the person looking at the case they already had, with the new one
        # selected in a list they could not see, which made importing a second
        # case look as though it had done nothing at all. The open case is not
        # disturbed: it stays open, and the message says so.
        summary = getattr(dialog, "summary", None)
        imported = summary.imported if summary is not None else []
        if not imported:
            if self.controller.case_data is None:
                self.set_module("cases")
            return

        open_case = self.controller.case_data
        self.set_module("cases")
        self.case_browser.select_case(imported[0].case.id)
        if open_case is not None:
            message = (
                f"{summary.summary_line()}   {open_case.case.pseudonym} is "
                f"still open. Choose Open to move to an imported case."
            )
        else:
            message = (
                f"{summary.summary_line()}   Select a case and choose Open to "
                f"start annotating."
            )
        self.statusBar().showMessage(message, 8000)

    # -- tools ---------------------------------------------------------------

    def run_system_check(self) -> None:
        from .dialogs.system_check_dialog import SystemCheckDialog

        SystemCheckDialog(self.paths, self).exec()

    def run_diagnostics(self) -> None:
        from .dialogs.diagnostics_dialog import DiagnosticsDialog

        DiagnosticsDialog(self.controller, self).exec()

    def verify_audit(self) -> None:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            result = self.repo.verify_audit_chain()
        finally:
            QApplication.restoreOverrideCursor()

        if result["valid"]:
            QMessageBox.information(
                self, "Audit history verified",
                f"{result['records_checked']} records were checked.\n\n"
                f"{result['reason']}",
            )
        else:
            QMessageBox.critical(
                self, "Audit history is not intact",
                f"The chain breaks at record {result.get('broken_at_sequence')}.\n\n"
                f"{result['reason']}\n\n"
                f"Report this to the project administrator. The database may have "
                f"been modified outside the application.",
            )

    def check_integrity(self) -> None:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            result = self.repo.db.integrity_check()
        finally:
            QApplication.restoreOverrideCursor()
        lines = [f"{k}: {v}" for k, v in result["checks"].items()]
        if result["ok"]:
            QMessageBox.information(
                self, "Database is intact", "\n".join(lines)
            )
        else:
            QMessageBox.critical(
                self, "Database problem",
                "\n".join(lines)
                + "\n\nRestore from the most recent backup in the data folder.",
            )

    def backup_database(self) -> None:
        from ..core.models import utc_now

        stamp = utc_now().replace(":", "").replace("-", "")[:15]
        destination = self.paths.backups_dir / f"aria_backup_{stamp}.db"
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.repo.db.backup_to(destination)
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Backup failed", str(exc))
            return
        QApplication.restoreOverrideCursor()
        QMessageBox.information(
            self, "Backup written",
            f"A consistent copy of the database was written to:\n\n{destination}",
        )

    def open_data_folder(self) -> None:
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.paths.data_dir)))

    def open_preferences(self) -> None:
        from .dialogs.preferences_dialog import PreferencesDialog

        dialog = PreferencesDialog(self.config, self)
        if dialog.exec():
            self._apply_settings()

    def _apply_settings(self) -> None:
        settings = self.config.settings
        for pane in self.workspace.panes.values():
            canvas = pane.canvas
            canvas.set_annotation_line_width(settings.annotation_line_width)
            canvas.set_landmark_size(settings.landmark_size)
            canvas.magnifier_factor = settings.magnifier_factor
            canvas.zoom_step = settings.zoom_step
            canvas.crosshair_enabled = settings.crosshair
        self.probe.setVisible(settings.show_data_probe)
        self.action_probe.setChecked(settings.show_data_probe)
        app = QApplication.instance()
        if app is not None:
            from .theme import HIGH_CONTRAST, PALETTE as BASE, stylesheet

            palette = HIGH_CONTRAST if settings.high_contrast_annotations else BASE
            app.setStyleSheet(
                stylesheet(palette, settings.font_point_size, settings.ui_scale)
            )
        if settings.lock_on_idle:
            self._idle_timer.start()
        else:
            self._idle_timer.stop()

    # -- help ----------------------------------------------------------------

    def start_tour(self) -> None:
        from .dialogs.tour import GuidedTour

        tour = GuidedTour(self)
        tour.start()

    def show_shortcuts(self) -> None:
        from .dialogs.shortcuts_dialog import ShortcutsDialog

        ShortcutsDialog(self).exec()

    def show_about(self) -> None:
        from .dialogs.about_dialog import AboutDialog

        AboutDialog(self).exec()

    def show_user_guide(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from pathlib import Path

        guide = Path(__file__).resolve().parents[2] / "docs" / "USER_GUIDE.md"
        if guide.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(guide)))
        else:
            QMessageBox.information(
                self, "User guide",
                "The user guide is in the docs folder of the installation.",
            )

    # -- session -------------------------------------------------------------

    def _check_idle(self) -> None:
        if self.session is None or self.session.timeout_minutes <= 0:
            return
        if self.session.is_expired():
            self._idle_timer.stop()
            self.controller.close_case()
            QMessageBox.information(
                self, "Session timed out",
                f"This session was locked after "
                f"{self.session.timeout_minutes} minutes without activity.\n\n"
                f"Your work was saved as you went, so nothing was lost.",
            )
            self.sign_out(timed_out=True)

    def sign_out(self, timed_out: bool = False) -> None:
        self.controller.close_case()
        self.closing.emit()
        self.close()

    def _on_dirty(self, dirty: bool) -> None:
        if dirty:
            self.status_save.set_state("Saving", "info", "A change is being written.")

    def _on_saved(self, timestamp: str) -> None:
        self.status_save.set_state(
            "Saved", "ok",
            f"Last saved at {timestamp[11:19]} UTC. Every change is written as "
            f"it is made, so a power loss cannot lose completed work.",
        )

    def _on_validation(self, result) -> None:
        """Mirror the submission state into the toolbar."""
        if result is None:
            self.validation_label.setText("")
            return
        if result.can_submit and not result.warnings:
            text, status = "Ready to submit", "ok"
        elif result.can_submit:
            text, status = (
                f"Ready to submit, {len(result.warnings)} "
                f"{'warning' if len(result.warnings) == 1 else 'warnings'}",
                "warn",
            )
        else:
            text, status = (
                f"{len(result.blockers)} "
                f"{'item blocks' if len(result.blockers) == 1 else 'items block'} "
                f"submission",
                "danger",
            )
        self.validation_label.setText(text + "   ")
        self.validation_label.setProperty("status", status)
        self.validation_label.style().unpolish(self.validation_label)
        self.validation_label.style().polish(self.validation_label)
        lines = [i.label() for i in (result.blockers + result.warnings)[:8]]
        self.validation_label.setToolTip(
            "\n".join(lines) or "Every submission check has passed."
        )

    def _on_undo_state(self, can_undo: bool, can_redo: bool) -> None:
        self.action_undo.setEnabled(can_undo)
        self.action_redo.setEnabled(can_redo)
        undo_label = self.controller.undo_label()
        redo_label = self.controller.redo_label()
        self.action_undo.setText(f"Undo {undo_label}".strip())
        self.action_redo.setText(f"Redo {redo_label}".strip())

    def _show_error(self, title: str, message: str, remedy: str) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(title)
        box.setText(message)
        if remedy:
            box.setInformativeText(f"What to do: {remedy}")
        box.exec()

    # -- window --------------------------------------------------------------

    def event(self, event):
        if self.session is not None and event.type() in (
            event.Type.MouseButtonPress, event.Type.KeyPress, event.Type.Wheel
        ):
            self.session.touch()
        return super().event(event)

    def closeEvent(self, event) -> None:
        if self.settings.remember_window_geometry:
            self.settings.window_geometry = bytes(self.saveGeometry().toHex()).decode()
            self.settings.window_state = bytes(self.saveState().toHex()).decode()
            self.settings.layout = self.workspace.layout_key
            self.config.save()
        self.controller.close_case()
        self.repo.release_all_locks()
        super().closeEvent(event)

    def reset_panel_layout(self) -> None:
        """Put the side panels back, docked, visible and the right size.

        The way out of every arrangement that leaves a panel unreachable or
        squeezed: one hidden from a context menu, or one sized for a session
        on a differently shaped display.
        """
        for dock, area in (
            (self.module_dock, Qt.LeftDockWidgetArea),
            (self.display_dock, Qt.RightDockWidgetArea),
        ):
            dock.setFloating(False)
            self.addDockWidget(area, dock)
            dock.show()
            dock.raise_()
        self.resizeDocks(
            [self.module_dock, self.display_dock],
            [METRICS.panel_width, METRICS.panel_width],
            Qt.Horizontal,
        )
        self.statusBar().showMessage("The side panels are back in place.", 4000)

    def _pin_panels(self) -> None:
        """Put back any panel a restored layout left loose or out of place.

        The panels cannot be floated or moved any more, but a layout saved
        before that was the case is replayed without asking whether it still
        makes sense: a panel floating as a window of its own, possibly on a
        screen that is no longer attached, or docked on the other side.

        Being hidden is treated differently for the two of them. The module
        panel holds the case list and every annotation tool, so a session that
        starts without it looks exactly like the panel having disappeared, and
        it always comes back. The image display panel is a reading aid that can
        be closed for space, and somebody who closed it meant to; reopening it
        on every launch would be the application arguing. View, Image display
        brings it back.
        """
        for dock, area in (
            (self.module_dock, Qt.LeftDockWidgetArea),
            (self.display_dock, Qt.RightDockWidgetArea),
        ):
            if dock.isFloating() or self.dockWidgetArea(dock) != area:
                # Re-docking a hidden panel must not be what reveals it.
                was_hidden = dock.isHidden()
                dock.setFloating(False)
                self.addDockWidget(area, dock)
                dock.setHidden(was_hidden)

        if self.module_dock.isHidden():
            self.module_dock.show()

    def restore_geometry(self) -> None:
        from PySide6.QtCore import QByteArray

        if not self.settings.remember_window_geometry:
            return
        if self.settings.window_geometry:
            self.restoreGeometry(
                QByteArray.fromHex(self.settings.window_geometry.encode())
            )
        if self.settings.window_state:
            self.restoreState(QByteArray.fromHex(self.settings.window_state.encode()))
        self._pin_panels()
        if self.settings.layout in LAYOUTS:
            self.set_layout(self.settings.layout)
            self.layout_actions[self.settings.layout].setChecked(True)


def _region_rect(image, left_fraction: float, right_fraction: float):
    """Rectangle covering a horizontal band of the image, for the side panes."""
    from PySide6.QtCore import QRectF

    x0 = image.columns * left_fraction
    x1 = image.columns * right_fraction
    y0 = image.rows * 0.35
    y1 = image.rows * 0.95
    return QRectF(x0, y0, x1 - x0, y1 - y0)
