"""Headless interface exercise.

Builds a real database, imports the sample images, constructs the main window
and drives the interface the way a person would: switch modules, open a case,
select tools, draw, construct index lines, grade, flag, validate, submit and
export. Anything that raises is reported with its traceback.

This runs offscreen, so it works in a build pipeline as well as on a desktop.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FAILURES: list = []
STEPS: list = []
DIALOGS: list = []


def suppress_modal_dialogs():
    """Answer modal dialogs automatically so the run cannot block.

    A message box waiting for a click would hang a headless run forever. The
    prompts are recorded instead, which is useful in itself: the list shows what
    the interface tried to tell the user during the exercise.
    """
    from PySide6.QtWidgets import QDialog, QInputDialog, QMessageBox

    def record(kind, args):
        text = " | ".join(str(a) for a in args if isinstance(a, str))
        DIALOGS.append((kind, text[:160]))

    for name, answer in (
        ("information", QMessageBox.Ok),
        ("warning", QMessageBox.Ok),
        ("critical", QMessageBox.Ok),
        ("question", QMessageBox.Yes),
        ("about", None),
    ):
        def make(n=name, a=answer):
            def handler(*args, **kwargs):
                record(n, args)
                return a

            return staticmethod(handler)

        setattr(QMessageBox, name, make())

    def exec_stub(self, *args, **kwargs):
        record("exec", [self.windowTitle(), getattr(self, "text", lambda: "")()])
        return QDialog.Accepted

    QMessageBox.exec = exec_stub
    QMessageBox.exec_ = exec_stub

    for name, value in (
        ("getText", ("smoke", True)),
        ("getDouble", (25.0, True)),
        ("getInt", (1, True)),
        ("getItem", ("smoke", True)),
    ):
        def make_input(v=value, n=name):
            def handler(*args, **kwargs):
                record(f"input.{n}", args)
                return v

            return staticmethod(handler)

        setattr(QInputDialog, name, make_input())


def step(name: str):
    """Decorator that records whether a step ran cleanly."""

    def wrap(fn):
        def run(*args, **kwargs):
            try:
                result = fn(*args, **kwargs)
                STEPS.append((name, True, ""))
                return result
            except Exception as exc:
                FAILURES.append((name, traceback.format_exc()))
                STEPS.append((name, False, f"{type(exc).__name__}: {exc}"))
                return None

        return run

    return wrap


def main() -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from aria.config import Config, Paths, set_config
    from aria.core.models import Project, Role, User
    from aria.core.schema import MCIGrade, Presence, ProjectSchema, Side
    from aria.store.db import open_database
    from aria.store.repository import Repository
    from aria.security.auth import AuthService

    tmp = Path(tempfile.mkdtemp(prefix="aria_ui_smoke_"))
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

    app = QApplication.instance() or QApplication([])
    suppress_modal_dialogs()
    from aria.app import apply_theme

    apply_theme(app, config)

    database = open_database(config.paths.database)
    repo = Repository(database)
    service = AuthService(repo, config.settings)

    admin = service.create_account(
        "smoke_admin", "Smoke Administrator", Role.ADMIN, "SmokeTest-2026", must_change=False
    )
    annotator = service.create_account(
        "smoke_ann", "Smoke Annotator", Role.ANNOTATOR, "SmokeTest-2026", must_change=False
    )
    reviewer = service.create_account(
        "smoke_rev", "Smoke Reviewer", Role.REVIEWER, "SmokeTest-2026", must_change=False
    )
    annotator.calibration_passed = True
    repo.update_user(annotator, "Calibration set approved for the smoke run.")
    repo.set_actor(admin)
    session = service.start_session(admin)

    project = Project(
        name="Smoke project", created_by=admin.id,
        schema_json=json.dumps(ProjectSchema().to_dict()),
    )
    repo.create_project(project)

    # -- import the sample images ---------------------------------------
    from aria.io.importer import Importer

    importer = Importer(repo, config.paths, config.settings)
    samples = sorted((ROOT / "Test Artifacts").glob("*"))
    summary = importer.import_files(
        [str(p) for p in samples], project.id, imported_by=admin.id
    )
    print(f"Import: {summary.summary_line()}")
    cases = repo.list_cases(project.id)
    if not cases:
        print("No cases were imported, so the interface cannot be exercised.")
        return 1

    # -- build the window ------------------------------------------------
    from aria.ui.main_window import MainWindow

    repo.set_actor(reviewer)
    session = service.start_session(reviewer)
    window = MainWindow(repo, config.paths, config, session)
    window.controller.set_project(project)
    window.resize(1600, 980)
    window.show()
    app.processEvents()

    controller = window.controller

    @step("switch through every module")
    def switch_modules():
        for key in ("cases", "annotate", "measure", "review", "export", "administration", "audit"):
            window.set_module(key)
            app.processEvents()

    @step("open a case")
    def open_case():
        dicom = [c for c in cases if c.source.source_format == "dicom"]
        target = dicom[0] if dicom else cases[0]
        assert window.controller.open_case(target.id), "The case did not open"
        app.processEvents()
        return target

    @step("confirm orientation and validate calibration")
    def prepare_case():
        controller.confirm_laterality("Confirmed during the smoke run")
        cal = controller.case_data.case.calibration
        if cal.has_spacing:
            from aria.core.models import utc_now

            cal.validate("ADM-001", utc_now())
            controller.set_calibration(cal, "Validated during the smoke run")
        app.processEvents()

    @step("select every drawing tool")
    def select_tools():
        from aria.ui.viewer.canvas import Tool

        for tool in (
            Tool.SELECT, Tool.POINT, Tool.LINE, Tool.POLYLINE, Tool.POLYGON,
            Tool.BOX, Tool.ROI, Tool.BRUSH, Tool.ERASER, Tool.RULER,
        ):
            window.set_tool(tool)
            app.processEvents()
        window.set_tool(Tool.SELECT)

    @step("draw a full annotation set")
    def draw():
        from aria.core.models import Annotation, new_id

        image = controller.image
        width, height = image.columns, image.rows
        set_id = controller.case_data.annotation_set.id

        def add(key, side, points, geometry):
            ann = Annotation(
                id=new_id("ann_"), set_id=set_id, class_key=key,
                side=side.value, geometry_type=geometry,
            )
            ann.set_points(points)
            assert controller.add_annotation(ann), f"{key} on {side.value} was not added"
            return ann

        y_peri = height * 0.78
        y_endo = y_peri - height * 0.028
        peri = [(x, y_peri) for x in range(int(width * 0.18), int(width * 0.84), 40)]
        endo = [(x, y_endo) for x in range(int(width * 0.18), int(width * 0.84), 40)]
        ramus_r = [(width * 0.16, y_peri - height * i * 0.05) for i in range(6)]
        ramus_l = [(width * 0.86, y_peri - height * i * 0.05) for i in range(6)]

        for side in (Side.RIGHT, Side.LEFT):
            add("periosteal_border", side, peri, "polyline")
            add("endosteal_border", side, endo, "polyline")
        add("posterior_ramus_border", Side.RIGHT, ramus_r, "polyline")
        add("posterior_ramus_border", Side.LEFT, ramus_l, "polyline")

        for side, fx in ((Side.RIGHT, 0.32), (Side.LEFT, 0.68)):
            cx = width * fx
            add("mental_foramen_centre", side, [(cx, y_peri - height * 0.115)], "point")
            add("mental_foramen_superior", side, [(cx, y_peri - height * 0.135)], "point")
            add("mental_foramen_inferior", side, [(cx, y_peri - height * 0.095)], "point")
            add("mci_region", side,
                [(cx - width * 0.06, y_endo - 12), (cx + width * 0.02, y_peri + 12)], "box")
            add("trabecular_roi", side,
                [(cx, y_peri - height * 0.22), (cx + 64, y_peri - height * 0.22 + 64)], "roi_rect")

        add("antegonial_point", Side.RIGHT, [(width * 0.21, y_peri - 4)], "point")
        add("antegonial_point", Side.LEFT, [(width * 0.79, y_peri - 4)], "point")
        add("gonion", Side.RIGHT, [(width * 0.175, y_peri - 10)], "point")
        add("gonion", Side.LEFT, [(width * 0.825, y_peri - 10)], "point")
        add("menton", Side.MIDLINE, [(width * 0.5, y_peri + height * 0.02)], "point")
        add("mandible_whole", Side.MIDLINE,
            [(width * 0.16, y_peri + 30), (width * 0.84, y_peri + 30),
             (width * 0.84, y_peri - height * 0.3), (width * 0.16, y_peri - height * 0.3)],
            "polygon")
        app.processEvents()

    @step("construct every index line from the contours")
    def construct():
        built = 0
        for key in (
            "mcw_line", "pmi_superior_line", "pmi_inferior_line",
            "antegonial_index_line", "gonial_index_line",
        ):
            for side in (Side.RIGHT, Side.LEFT):
                if controller.construct_index_line(key, side):
                    built += 1
                app.processEvents()
        assert built >= 8, f"Only {built} of 10 index lines were constructed"
        return built

    @step("assign grades and a quality flag")
    def grade():
        controller.set_grade(Side.RIGHT, MCIGrade.C2, "Semilunar defects present")
        controller.set_grade(Side.LEFT, MCIGrade.C1)
        controller.add_quality_flag("artefact", "Ghost image over the left ramus", Side.LEFT)
        app.processEvents()

    @step("record a structure as absent")
    def absent():
        controller.set_presence(
            "crestal_roi", Side.RIGHT, Presence.NOT_ASSESSABLE,
            "Crest obscured by superimposition",
        )
        app.processEvents()

    @step("compute texture features")
    def texture():
        window.compute_texture()
        app.processEvents()

    @step("exercise display controls")
    def display():
        window.action_invert.setChecked(True)
        window.toggle_invert()
        for name, _label in __import__(
            "aria.io.image", fromlist=["AVAILABLE_FILTERS"]
        ).AVAILABLE_FILTERS:
            window.set_filter(name)
            app.processEvents()
        window.set_filter("none")
        window.action_original_pixels.setChecked(True)
        window.toggle_original()
        window.action_original_pixels.setChecked(False)
        window.toggle_original()
        window.canvas.zoom_in()
        window.canvas.zoom_out()
        window.canvas.zoom_to_actual()
        window.action_fit.trigger()
        window.action_magnifier.setChecked(True)
        window.toggle_magnifier()
        window.probe.update_cursor(500.0, 400.0)
        app.processEvents()

    @step("switch every layout")
    def layouts():
        from aria.ui.viewer.view_frame import LAYOUTS

        for key in LAYOUTS:
            window.set_layout(key)
            app.processEvents()
        window.set_layout("one_up")

    @step("undo and redo")
    def undo_redo():
        before = len(controller.case_data.live_annotations())
        assert controller.undo(), "Undo did nothing"
        app.processEvents()
        assert controller.redo(), "Redo did nothing"
        app.processEvents()
        after = len(controller.case_data.live_annotations())
        assert after == before, f"Object count changed across undo and redo: {before} to {after}"

    @step("run validation")
    def validate():
        result = controller.validate()
        blockers = [i.code for i in result.blockers]
        print(f"   validation: {result.summary()}")
        for issue in result.blockers[:6]:
            print(f"      blocker: {issue.label()}")
        for issue in result.warnings[:4]:
            print(f"      warning: {issue.label()}")
        return result

    @step("check measurements were produced")
    def measurements():
        values = controller.recompute()
        assessable = [m for m in values if m.assessable and m.value_px is not None]
        assert assessable, "No measurement produced a value"
        for m in values:
            if m.side == "R" and m.assessable:
                print(f"      {m.kind:30s} {m.formatted()}")
        return assessable

    @step("submit for review")
    def submit():
        result = controller.validate()
        if not result.can_submit:
            print(f"   submission blocked by {len(result.blockers)} items, as designed")
            return False
        return controller.submit("Smoke run submission")

    @step("review and accept")
    def review():
        from aria.core.schema import CaseState
        from aria.core.models import ReviewDecision

        window.set_module("review")
        window.review_panel.refresh()
        app.processEvents()
        if controller.case_data.case.state_enum is CaseState.SUBMITTED:
            controller.record_review(
                ReviewDecision.ACCEPT, "Accepted during the smoke run", [], CaseState.ACCEPTED
            )
        app.processEvents()

    @step("refresh every panel")
    def refresh_panels():
        window.case_browser.reload_projects()
        window.admin_panel.refresh()
        window.audit_panel.refresh()
        window.export_panel.refresh()
        window.review_panel.refresh()
        app.processEvents()

    @step("verify the audit chain")
    def audit():
        result = repo.verify_audit_chain()
        assert result["valid"], f"The audit chain is broken: {result}"
        print(f"   audit: {result['records_checked']} records, chain intact")

    @step("export files")
    def export_files():
        window.set_module("export")
        window.export_panel.refresh()
        window.export_panel.state_filter.setCurrentIndex(0)
        window.export_panel.destination.setText(str(tmp / "exports"))
        window.export_panel.run_export()
        app.processEvents()
        produced = list((tmp / "exports").rglob("*"))
        assert produced, "The export produced no files"
        print(f"   export produced {len([p for p in produced if p.is_file()])} files")

    @step("build and verify a training bundle")
    def bundle():
        from aria.io.exporters.bundle import (
            BundleExporter, BundleOptions, verify_bundle,
        )

        exporter = BundleExporter(repo, config.paths, config.settings, controller.schema)
        bundles = window.export_panel._gather_bundles(repo.list_cases(project.id))
        destination = tmp / "smoke_bundle.zip"
        result = exporter.build(destination, project, bundles, BundleOptions())
        assert result.path is not None, "No bundle was written"
        report = verify_bundle(destination)
        assert report["ok"], f"The bundle did not verify: {report}"
        print(f"   bundle: {result.summary()}  verified {report['verified']} files")

    @step("open the dialogs")
    def dialogs():
        from aria.ui.dialogs.about_dialog import AboutDialog
        from aria.ui.dialogs.shortcuts_dialog import ShortcutsDialog
        from aria.ui.dialogs.preferences_dialog import PreferencesDialog
        from aria.ui.dialogs.account_dialog import AccountDialog

        for factory in (
            lambda: AboutDialog(window),
            lambda: ShortcutsDialog(window),
            lambda: PreferencesDialog(config, window),
            lambda: AccountDialog(controller, window),
        ):
            dialog = factory()
            dialog.show()
            app.processEvents()
            dialog.close()

    @step("run the guided tour through every step")
    def tour():
        from aria.ui.dialogs.tour import STEPS as TOUR_STEPS, GuidedTour

        guide = GuidedTour(window)
        guide.start()
        app.processEvents()
        for _ in range(len(TOUR_STEPS) + 1):
            guide.next_step()
            app.processEvents()

    @step("capture a screenshot")
    def screenshot():
        target_dir = ROOT / "docs"
        target_dir.mkdir(parents=True, exist_ok=True)
        for module, layout, name in (
            ("annotate", "one_up", "screenshot_workspace.png"),
            ("measure", "one_up", "screenshot_measure.png"),
            ("annotate", "two_by_two", "screenshot_layout_four.png"),
            ("cases", "one_up", "screenshot_cases.png"),
        ):
            window.set_module(module)
            window.set_layout(layout)
            for _ in range(6):
                app.processEvents()
            window.workspace.apply_to_all(lambda c: c.fit_to_window())
            for _ in range(6):
                app.processEvents()
            window.grab().save(str(target_dir / name))
        print(f"   screenshots written to {target_dir}")

    # -- run -------------------------------------------------------------
    switch_modules()
    open_case()
    prepare_case()
    select_tools()
    draw()
    construct()
    grade()
    absent()
    texture()
    display()
    layouts()
    undo_redo()
    measurements()
    validate()
    submit()
    review()
    refresh_panels()
    audit()
    export_files()
    bundle()
    dialogs()
    tour()
    screenshot()

    window.controller.close_case()
    window.close()
    repo.release_all_locks()
    database.close()

    print("\n" + "=" * 72)
    passed = sum(1 for _n, ok, _e in STEPS if ok)
    for name, ok, error in STEPS:
        print(f"  [{'pass' if ok else 'FAIL'}] {name}" + (f"   {error}" if error else ""))
    print("=" * 72)
    print(f"{passed} of {len(STEPS)} interface steps passed.")

    if DIALOGS:
        print(f"\n{len(DIALOGS)} prompts were shown during the run:")
        for kind, text in DIALOGS[:25]:
            print(f"  {kind:18s} {text}")

    if FAILURES:
        print(f"\n{len(FAILURES)} failures:\n")
        for name, tb in FAILURES:
            print(f"--- {name} ---")
            print(tb)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
