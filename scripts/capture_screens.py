"""Render documentation screenshots with the platform's real fonts.

The offscreen Qt platform has no font database, so text rendered under it comes
out as empty boxes. This script uses the native platform but never calls show(),
so it produces a true to life image without putting a window on screen.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def build(window_size=(1680, 1000)):
    from PySide6.QtWidgets import QApplication

    from aria.app import apply_theme
    from aria.config import Config, Paths, set_config
    from aria.core.models import Annotation, Project, Role, new_id, utc_now
    from aria.core.schema import MCIGrade, Presence, ProjectSchema, Side
    from aria.io.importer import Importer
    from aria.security.auth import AuthService
    from aria.store.db import open_database
    from aria.store.repository import Repository
    from aria.ui.main_window import MainWindow

    tmp = Path(tempfile.mkdtemp(prefix="aria_shots_"))
    config = Config(
        Paths(
            data_dir=tmp / "data", config_dir=tmp / "cfg",
            log_dir=tmp / "logs", cache_dir=tmp / "cache",
        )
    )
    config.paths.ensure()
    config.settings.first_run_completed = True
    config.settings.tour_completed = True
    config.settings.backup_on_launch = False
    set_config(config)

    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme(app, config)

    database = open_database(config.paths.database)
    repo = Repository(database)
    service = AuthService(repo, config.settings)

    admin = service.create_account(
        "docs_admin", "R Mehta", Role.ADMIN, "DocsCapture-2026", must_change=False
    )
    reviewer = service.create_account(
        "docs_rev", "K Alvarez", Role.REVIEWER, "DocsCapture-2026", must_change=False
    )
    repo.set_actor(admin)

    project = Project(
        name="Mandibular cortical study", created_by=admin.id,
        schema_json=json.dumps(ProjectSchema().to_dict()),
    )
    repo.create_project(project)

    importer = Importer(repo, config.paths, config.settings)
    samples = sorted((ROOT / "Test Artifacts").glob("*"))
    importer.import_files([str(p) for p in samples], project.id, imported_by=admin.id)
    cases = repo.list_cases(project.id)
    dicom_cases = [c for c in cases if c.source.source_format == "dicom"]
    target = dicom_cases[0] if dicom_cases else cases[0]

    repo.set_actor(reviewer)
    session = service.start_session(reviewer)
    window = MainWindow(repo, config.paths, config, session)
    window.controller.set_project(project)
    window.resize(*window_size)
    # Lay the window out fully without putting it on the desktop. Without this
    # the viewport has no real size yet and fit to window computes a useless
    # zoom factor.
    from PySide6.QtCore import Qt as _Qt

    window.setAttribute(_Qt.WA_DontShowOnScreen, True)
    window.show()
    for _ in range(6):
        app.processEvents()

    controller = window.controller
    controller.open_case(target.id)
    controller.confirm_laterality("Confirmed against patient positioning")
    cal = controller.case_data.case.calibration
    if cal.has_spacing:
        cal.validate(reviewer.pseudonym, utc_now())
        controller.set_calibration(cal, "Validated for the capture")

    image = controller.image
    width, height = image.columns, image.rows
    set_id = controller.case_data.annotation_set.id

    def add(key, side, points, geometry):
        ann = Annotation(
            id=new_id("ann_"), set_id=set_id, class_key=key,
            side=side.value, geometry_type=geometry,
        )
        ann.set_points(points)
        controller.add_annotation(ann)

    y_peri = height * 0.775
    y_endo = y_peri - height * 0.03
    peri = [(x, y_peri + (height * 0.012) * ((x / width - 0.5) ** 2) * 14)
            for x in range(int(width * 0.17), int(width * 0.85), 36)]
    endo = [(x, y - height * 0.03) for x, y in peri]
    for side in (Side.RIGHT, Side.LEFT):
        add("periosteal_border", side, peri, "polyline")
        add("endosteal_border", side, endo, "polyline")
    add("posterior_ramus_border", Side.RIGHT,
        [(width * 0.155, y_peri - height * i * 0.055) for i in range(6)], "polyline")
    add("posterior_ramus_border", Side.LEFT,
        [(width * 0.862, y_peri - height * i * 0.055) for i in range(6)], "polyline")

    for side, fx in ((Side.RIGHT, 0.315), (Side.LEFT, 0.688)):
        cx = width * fx
        add("mental_foramen_centre", side, [(cx, y_peri - height * 0.125)], "point")
        add("mental_foramen_superior", side, [(cx, y_peri - height * 0.148)], "point")
        add("mental_foramen_inferior", side, [(cx, y_peri - height * 0.102)], "point")
        add("mci_region", side,
            [(cx - width * 0.055, y_endo - 14), (cx + width * 0.015, y_peri + 16)], "box")
        add("trabecular_roi", side,
            [(cx + width * 0.03, y_peri - height * 0.20),
             (cx + width * 0.03 + 64, y_peri - height * 0.20 + 64)], "roi_rect")

    add("antegonial_point", Side.RIGHT, [(width * 0.205, y_peri - 6)], "point")
    add("antegonial_point", Side.LEFT, [(width * 0.795, y_peri - 6)], "point")
    add("gonion", Side.RIGHT, [(width * 0.172, y_peri - 14)], "point")
    add("gonion", Side.LEFT, [(width * 0.828, y_peri - 14)], "point")
    add("menton", Side.MIDLINE, [(width * 0.5, y_peri + height * 0.022)], "point")

    for key in (
        "mcw_line", "pmi_superior_line", "pmi_inferior_line",
        "antegonial_index_line", "gonial_index_line",
    ):
        for side in (Side.RIGHT, Side.LEFT):
            controller.construct_index_line(key, side)

    controller.set_grade(Side.RIGHT, MCIGrade.C2, "Semilunar defects distal to the foramen")
    controller.set_grade(Side.LEFT, MCIGrade.C1)
    controller.add_quality_flag("artefact", "Ghost image over the left ramus", Side.LEFT)
    window.compute_texture()
    settle(app, 900)
    return app, window, config, repo, database


def settle(app, milliseconds: int = 700) -> None:
    """Process events for a real duration so debounced timers fire."""
    import time

    from PySide6.QtCore import QEventLoop

    deadline = time.monotonic() + milliseconds / 1000.0
    while time.monotonic() < deadline:
        app.processEvents(QEventLoop.AllEvents, 20)


def capture(window, app, module: str, layout: str, path: Path, zoom=None) -> None:
    window.set_module(module)
    window.set_layout(layout)
    settle(app, 400)
    if zoom is not None:
        window.canvas.zoom_to_rect(zoom)
    else:
        window.workspace.apply_to_all(lambda c: c.fit_to_window())
    settle(app, 500)
    path.parent.mkdir(parents=True, exist_ok=True)
    window.grab().save(str(path))
    print(f"  {path.name}")


def main() -> int:
    from PySide6.QtCore import QRectF

    app, window, config, repo, database = build()
    out = ROOT / "docs" / "images"
    print("Writing screenshots:")

    capture(window, app, "annotate", "one_up", out / "workspace_annotate.png")

    image = window.controller.image
    region = QRectF(
        image.columns * 0.24, image.rows * 0.58,
        image.columns * 0.22, image.rows * 0.30,
    )
    capture(window, app, "annotate", "one_up", out / "workspace_zoom.png", zoom=region)

    capture(window, app, "measure", "one_up", out / "module_measure.png")
    capture(window, app, "cases", "one_up", out / "module_cases.png")
    capture(window, app, "review", "side_by_side", out / "module_review.png")
    capture(window, app, "administration", "one_up", out / "module_administration.png")
    capture(window, app, "audit", "one_up", out / "module_audit.png")
    capture(window, app, "export", "one_up", out / "module_export.png")
    capture(window, app, "annotate", "two_by_two", out / "layout_four_pane.png")

    # Dialogs.
    from aria.ui.dialogs.about_dialog import AboutDialog
    from aria.ui.dialogs.shortcuts_dialog import ShortcutsDialog
    from aria.ui.dialogs.system_check_dialog import SystemCheckDialog
    from aria.ui.dialogs.diagnostics_dialog import DiagnosticsDialog
    from aria.ui.dialogs.preferences_dialog import PreferencesDialog

    for factory, name in (
        (lambda: AboutDialog(window), "dialog_about.png"),
        (lambda: ShortcutsDialog(window), "dialog_shortcuts.png"),
        (lambda: PreferencesDialog(config, window), "dialog_preferences.png"),
    ):
        dialog = factory()
        dialog.resize(700, 600)
        for _ in range(6):
            app.processEvents()
        dialog.grab().save(str(out / name))
        print(f"  {name}")
        dialog.close()

    check = SystemCheckDialog(config.paths, window)
    check.resize(860, 640)
    for _ in range(40):
        app.processEvents()
    check.thread.wait(30000)
    for _ in range(12):
        app.processEvents()
    check.grab().save(str(out / "dialog_compatibility.png"))
    print("  dialog_compatibility.png")
    check.close()

    diagnostics = DiagnosticsDialog(window.controller, window)
    diagnostics.resize(880, 660)
    for _ in range(40):
        app.processEvents()
    diagnostics.thread.wait(120000)
    for _ in range(12):
        app.processEvents()
    diagnostics.grab().save(str(out / "dialog_diagnostics.png"))
    print("  dialog_diagnostics.png")
    diagnostics.close()

    # The guided tour, over the annotation workspace.
    from aria.ui.dialogs.tour import GuidedTour

    window.set_module("annotate")
    for _ in range(6):
        app.processEvents()
    tour = GuidedTour(window)
    tour.start(5)
    for _ in range(10):
        app.processEvents()
    window.grab().save(str(out / "guided_tour.png"))
    print("  guided_tour.png")
    tour.stop(False)

    window.controller.close_case()
    window.close()
    repo.release_all_locks()
    database.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
