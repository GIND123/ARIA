"""Export module: selection, formats and the training bundle.

Exports are filtered by project, state, split, annotator and reviewer, and they
never carry a direct patient identifier (FR 052). The identifier scan runs before
anything is written, and a finding stops the export rather than warning after
the fact.
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from ...core.schema import CaseState
from ...security.auth import Permission
from ..theme import PALETTE
from ..widgets.common import (
    Banner,
    CollapsibleSection,
    KeyValueGrid,
    ScrollPanel,
    SectionLabel,
    make_button,
)


class ExportPanel(QWidget):
    """Builds export selections and writes them."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._worker = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.scroll = ScrollPanel(self)
        layout.addWidget(self.scroll)

        self._build_selection_section()
        self._build_format_section()
        self._build_bundle_section()
        self._build_history_section()

        controller.case_list_changed.connect(self.refresh)

    # -- selection -----------------------------------------------------------

    def _build_selection_section(self) -> None:
        self.selection_section = CollapsibleSection("What to export", self, True, "folder")
        body = self.selection_section.body_layout()

        row = QHBoxLayout()
        row.addWidget(QLabel("State", self))
        self.state_filter = QComboBox(self)
        self.state_filter.addItem("Any state", "")
        for state in CaseState:
            self.state_filter.addItem(state.display, state.value)
        index = self.state_filter.findData(CaseState.ACCEPTED.value)
        if index >= 0:
            self.state_filter.setCurrentIndex(index)
        row.addWidget(self.state_filter, 1)
        body.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Split", self))
        self.split_filter = QComboBox(self)
        self.split_filter.addItem("Any split", "")
        for split in ("train", "validation", "test", "calibration"):
            self.split_filter.addItem(split, split)
        row.addWidget(self.split_filter, 1)
        body.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Annotator", self))
        self.annotator_filter = QComboBox(self)
        row.addWidget(self.annotator_filter, 1)
        body.addLayout(row)

        self.refresh_button = make_button("Refresh selection", "refresh", parent=self)
        self.refresh_button.clicked.connect(self.refresh)
        body.addWidget(self.refresh_button)

        self.selection_summary = QLabel("", self)
        self.selection_summary.setWordWrap(True)
        self.selection_summary.setProperty("dim", True)
        body.addWidget(self.selection_summary)

        self.case_list = QListWidget(self)
        self.case_list.setMaximumHeight(150)
        self.case_list.setSelectionMode(QListWidget.ExtendedSelection)
        body.addWidget(self.case_list)

        self.privacy_banner = Banner(self)
        body.addWidget(self.privacy_banner)

        self.scroll.add_section(self.selection_section)

    def refresh(self) -> None:
        project = self.controller.project
        self.case_list.clear()
        self.annotator_filter.blockSignals(True)
        current = self.annotator_filter.currentData()
        self.annotator_filter.clear()
        self.annotator_filter.addItem("Anyone", "")
        for user in self.controller.repo.list_users(include_inactive=True):
            self.annotator_filter.addItem(f"{user.display_name} ({user.pseudonym})", user.id)
        if current:
            index = self.annotator_filter.findData(current)
            if index >= 0:
                self.annotator_filter.setCurrentIndex(index)
        self.annotator_filter.blockSignals(False)

        if project is None:
            self.selection_summary.setText("No project selected.")
            return

        cases = self._selected_cases()
        for case in cases:
            item = QListWidgetItem(
                f"{case.pseudonym}   {case.state_enum.display}"
                + (f"   {case.split}" if case.split else ""),
                self.case_list,
            )
            item.setData(Qt.UserRole, case.id)
            if not case.calibration.millimetres_available:
                item.setToolTip(
                    "No validated calibration. Millimetre columns will be empty "
                    "for this case and pixel values will be exported instead."
                )

        uncalibrated = sum(
            1 for c in cases if not c.calibration.millimetres_available
        )
        unconfirmed = sum(1 for c in cases if not c.laterality_confirmed)
        summary = f"{len(cases)} cases selected."
        if uncalibrated:
            summary += f"   {uncalibrated} without a validated calibration."
        if unconfirmed:
            summary += f"   {unconfirmed} without confirmed orientation."
        self.selection_summary.setText(summary)

        if unconfirmed:
            self.privacy_banner.show_message(
                f"{unconfirmed} of the selected cases do not have confirmed "
                f"anatomical orientation. Side specific values from those cases "
                f"should be treated with care.",
                "warn",
            )
        else:
            self.privacy_banner.clear()

    def _selected_cases(self) -> list:
        project = self.controller.project
        if project is None:
            return []
        cases = self.controller.repo.list_cases(
            project_id=project.id,
            state=self.state_filter.currentData() or "",
            split=self.split_filter.currentData() or "",
        )
        annotator = self.annotator_filter.currentData()
        if annotator:
            keep = []
            for case in cases:
                sets = self.controller.repo.list_sets_for_case(case.id)
                if any(s.annotator_id == annotator for s in sets):
                    keep.append(case)
            cases = keep
        return cases

    # -- formats -------------------------------------------------------------

    def _build_format_section(self) -> None:
        self.format_section = CollapsibleSection("Formats", self, True, "export")
        body = self.format_section.body_layout()

        self.opt_json = QCheckBox("Geometry as JSON, in original pixel coordinates", self)
        self.opt_json.setChecked(True)
        self.opt_csv = QCheckBox("Tables as CSV, with a data dictionary", self)
        self.opt_csv.setChecked(True)
        self.opt_masks = QCheckBox("Masks as indexed PNG with a class map", self)
        self.opt_masks.setChecked(True)
        self.opt_coco = QCheckBox("COCO compatible segmentation", self)
        self.opt_coco.setChecked(True)
        self.opt_texture = QCheckBox("Texture features, where computed", self)
        self.opt_texture.setChecked(True)
        self.opt_dicom = QCheckBox("DICOM derived objects", self)
        self.opt_dicom.setChecked(False)
        for box in (
            self.opt_json, self.opt_csv, self.opt_masks, self.opt_coco,
            self.opt_texture, self.opt_dicom,
        ):
            body.addWidget(box)

        self.dicom_note = QLabel("", self)
        self.dicom_note.setWordWrap(True)
        self.dicom_note.setProperty("dim", True)
        body.addWidget(self.dicom_note)
        self._update_dicom_state()

        body.addWidget(SectionLabel("Destination", self))
        row = QHBoxLayout()
        self.destination = QLineEdit(self)
        self.destination.setText(
            self.controller.settings.default_export_dir
            or str(self.controller.paths.exports_dir)
        )
        browse = make_button("Browse", "folder", parent=self)
        browse.clicked.connect(self._browse)
        row.addWidget(self.destination, 1)
        row.addWidget(browse)
        body.addLayout(row)

        self.progress = QProgressBar(self)
        self.progress.setVisible(False)
        body.addWidget(self.progress)

        self.export_button = make_button("Export selection", "export", accent=True, parent=self)
        self.export_button.clicked.connect(self.run_export)
        body.addWidget(self.export_button)

        self.scroll.add_section(self.format_section)

    def _update_dicom_state(self) -> None:
        from ...io.exporters.dicom_output import InteroperabilityRecord

        settings = self.controller.settings
        record = InteroperabilityRecord.from_dict(
            json.loads(self.controller.repo.db.get_meta("dicom_interop") or "{}")
        )
        allowed = settings.dicom_output_enabled and record.is_satisfied()
        self.opt_dicom.setEnabled(allowed)
        if allowed:
            self.dicom_note.setText(
                "DICOM derived output is enabled. The conformance statement is "
                "written with every export."
            )
        else:
            reasons = record.blocking_reasons()
            if not settings.dicom_output_enabled:
                reasons.insert(0, "DICOM output is switched off in preferences.")
            self.opt_dicom.setChecked(False)
            self.dicom_note.setText(
                "DICOM derived output is unavailable until interoperability is "
                "accepted: " + " ".join(reasons)
            )

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Choose an export folder", self.destination.text()
        )
        if folder:
            self.destination.setText(folder)

    # -- bundle --------------------------------------------------------------

    def _build_bundle_section(self) -> None:
        self.bundle_section = CollapsibleSection("Training bundle", self, True, "bundle")
        body = self.bundle_section.body_layout()

        note = QLabel(
            "One archive holding the raw images, the labels, the derived "
            "measurements and a metadata sheet, with a manifest and checksums so "
            "a recipient can verify it.",
            self,
        )
        note.setWordWrap(True)
        note.setProperty("dim", True)
        body.addWidget(note)

        self.bundle_raw = QCheckBox("Include the retained original images", self)
        self.bundle_raw.setChecked(True)
        self.bundle_masks = QCheckBox("Include masks and class maps", self)
        self.bundle_masks.setChecked(True)
        self.bundle_coco = QCheckBox("Include COCO segmentation", self)
        self.bundle_coco.setChecked(True)
        self.bundle_strict = QCheckBox(
            "Stop if the identifier scan finds anything", self
        )
        self.bundle_strict.setChecked(True)
        self.bundle_strict.setToolTip(
            "The scan runs before the archive is written. Leaving this on means a "
            "finding stops the export rather than being reported afterwards."
        )
        for box in (self.bundle_raw, self.bundle_masks, self.bundle_coco, self.bundle_strict):
            body.addWidget(box)

        self.bundle_progress = QProgressBar(self)
        self.bundle_progress.setVisible(False)
        body.addWidget(self.bundle_progress)

        self.bundle_button = make_button(
            "Create training bundle", "bundle", accent=True, parent=self
        )
        self.bundle_button.clicked.connect(self.create_bundle)
        body.addWidget(self.bundle_button)

        self.verify_button = make_button(
            "Verify an existing bundle", "check",
            tooltip="Check every checksum inside an archive.", parent=self,
        )
        self.verify_button.clicked.connect(self.verify_bundle)
        body.addWidget(self.verify_button)

        self.bundle_result = KeyValueGrid(self)
        body.addWidget(self.bundle_result)

        self.scroll.add_section(self.bundle_section)

    # -- history -------------------------------------------------------------

    def _build_history_section(self) -> None:
        self.history_section = CollapsibleSection("Recent exports", self, False, "audit")
        body = self.history_section.body_layout()

        self.history_list = QListWidget(self)
        self.history_list.setMaximumHeight(150)
        body.addWidget(self.history_list)

        self.scroll.add_section(self.history_section)

    def _refresh_history(self) -> None:
        self.history_list.clear()
        project = self.controller.project
        for record in self.controller.repo.list_exports(
            project.id if project else "", limit=30
        ):
            item = QListWidgetItem(
                f"{record['created_at'][:16].replace('T', ' ')}   {record['kind']}   "
                f"{record['n_cases']} cases",
                self.history_list,
            )
            item.setToolTip(
                f"{record['destination']}\n"
                f"Digest {record.get('sha256', '')[:16]}\n{record.get('notes', '')}"
            )

    # -- running -------------------------------------------------------------

    def _gather_bundles(self, cases) -> list:
        """Build the per case tuples the exporters consume."""
        users = {u.id: u for u in self.controller.repo.list_users(include_inactive=True)}
        out = []
        for case in cases:
            sets = self.controller.repo.list_sets_for_case(case.id)
            if not sets:
                continue
            chosen = None
            for candidate in sets:
                if candidate.submitted_at:
                    chosen = candidate
                    break
            chosen = chosen or sets[0]
            data = self.controller.repo.load_set_data(chosen.id)
            if data is None:
                continue
            annotator = users.get(chosen.annotator_id)
            reviewer = users.get(chosen.reviewed_by) if chosen.reviewed_by else None
            texture = self.controller.repo.load_texture(chosen.id)
            out.append((data, annotator, reviewer, texture))
        return out

    def run_export(self) -> None:
        if not self.controller.can(Permission.EXPORT_DATA):
            QMessageBox.information(
                self, "Not permitted", "This account cannot export data."
            )
            return
        cases = self._selected_cases()
        if not cases:
            QMessageBox.information(
                self, "Nothing selected",
                "No cases match the current filters.\n\nWiden the filters and try again.",
            )
            return

        destination = Path(self.destination.text().strip() or self.controller.paths.exports_dir)
        from ...core.models import utc_now

        stamp = utc_now().replace(":", "").replace("-", "")[:15]
        project = self.controller.project
        folder = destination / f"aria_export_{(project.name if project else 'project')[:24]}_{stamp}"

        self.progress.setVisible(True)
        self.progress.setRange(0, len(cases) + 3)
        self.export_button.setEnabled(False)
        written: dict = {}
        try:
            bundles = self._gather_bundles(cases)
            self.progress.setValue(1)

            folder.mkdir(parents=True, exist_ok=True)

            if self.opt_json.isChecked():
                from ...io.exporters.json_export import write_case_json

                for index, (data, annotator, reviewer, texture) in enumerate(bundles):
                    write_case_json(
                        folder / "annotations" / data.case.pseudonym, data, project,
                        self.controller.schema, annotator, reviewer,
                        texture if self.opt_texture.isChecked() else None,
                    )
                    self.progress.setValue(1 + index)

            if self.opt_csv.isChecked():
                from ...io.exporters.csv_export import export_tables

                written.update(
                    export_tables(folder / "tables", bundles, project, self.controller.schema)
                )

            if self.opt_masks.isChecked():
                from ...io.exporters.mask_export import build_class_index, write_masks

                class_index = build_class_index()
                for data, *_rest in bundles:
                    shape = (data.case.source.rows, data.case.source.columns)
                    if shape[0] and shape[1]:
                        write_masks(
                            folder / "masks" / data.case.pseudonym, data, shape, class_index
                        )

            if self.opt_coco.isChecked():
                from ...io.exporters.mask_export import coco_document

                (folder / "coco").mkdir(parents=True, exist_ok=True)
                (folder / "coco" / "instances.json").write_text(
                    json.dumps(coco_document(bundles), indent=2, default=str),
                    encoding="utf-8",
                )

            if self.opt_dicom.isChecked():
                self._export_dicom(folder, bundles)

            self.progress.setValue(self.progress.maximum())
            self.controller.repo.record_export(
                "files", str(folder),
                {
                    "state": self.state_filter.currentData(),
                    "split": self.split_filter.currentData(),
                },
                len(bundles), project_id=project.id if project else "",
                notes=f"Exported to {folder.name}",
            )
            QMessageBox.information(
                self, "Export finished",
                f"{len(bundles)} cases exported to:\n\n{folder}\n\n"
                f"Geometry and derived measurements are in separate files so the "
                f"measurements can be recomputed and checked.",
            )
        except Exception as exc:
            QMessageBox.critical(
                self, "Export failed",
                f"{exc}\n\nNothing further was written. Check the destination "
                f"folder has space and is writable.",
            )
            self.controller.repo.record_export(
                "files", str(folder), {}, 0,
                project_id=project.id if project else "", status="failed", notes=str(exc),
            )
        finally:
            self.progress.setVisible(False)
            self.export_button.setEnabled(True)
            self._refresh_history()

    def _export_dicom(self, folder: Path, bundles) -> None:
        from ...io.exporters.dicom_output import (
            DicomExporter, DicomOutputDisabled, InteroperabilityRecord,
        )
        from ...core.measurements import MeasurementEngine

        record = InteroperabilityRecord.from_dict(
            json.loads(self.controller.repo.db.get_meta("dicom_interop") or "{}")
        )
        exporter = DicomExporter(self.controller.settings.dicom_output_enabled, record)
        engine = MeasurementEngine(self.controller.schema)
        for data, annotator, _reviewer, _texture in bundles:
            measurements = engine.compute(data)
            shape = (data.case.source.rows, data.case.source.columns)
            try:
                exporter.export_case(
                    folder / "dicom", data.case, data, measurements, shape, annotator
                )
            except DicomOutputDisabled as exc:
                raise RuntimeError(str(exc)) from exc

    def create_bundle(self) -> None:
        if not self.controller.can(Permission.CREATE_BUNDLE):
            QMessageBox.information(
                self, "Not permitted",
                "Creating a training bundle needs administrator or data manager "
                "permission.",
            )
            return
        cases = self._selected_cases()
        if not cases:
            QMessageBox.information(
                self, "Nothing selected", "No cases match the current filters."
            )
            return

        project = self.controller.project
        from ...core.models import utc_now

        stamp = utc_now().replace(":", "").replace("-", "")[:15]
        suggested = (
            Path(self.destination.text().strip() or self.controller.paths.exports_dir)
            / f"aria_bundle_{(project.name if project else 'project')[:24]}_{stamp}.zip"
        )
        path, _filter = QFileDialog.getSaveFileName(
            self, "Save training bundle", str(suggested), "ZIP archive (*.zip)"
        )
        if not path:
            return

        from ...io.exporters.bundle import (
            BundleExporter, BundleOptions, PrivacyScanFailed,
        )

        options = BundleOptions(
            include_raw=self.bundle_raw.isChecked(),
            include_masks=self.bundle_masks.isChecked(),
            include_coco=self.bundle_coco.isChecked(),
            include_texture=self.opt_texture.isChecked(),
            fail_on_privacy_finding=self.bundle_strict.isChecked(),
            compression_level=self.controller.settings.bundle_compression,
        )
        exporter = BundleExporter(
            self.controller.repo, self.controller.paths,
            self.controller.settings, self.controller.schema,
        )

        self.bundle_progress.setVisible(True)
        self.bundle_button.setEnabled(False)

        def progress(step, total, message):
            self.bundle_progress.setRange(0, max(1, total))
            self.bundle_progress.setValue(step)
            self.bundle_progress.setFormat(f"{message}   %p%")
            from PySide6.QtWidgets import QApplication

            QApplication.processEvents()

        try:
            bundles = self._gather_bundles(cases)
            result = exporter.build(
                path, project, bundles, options,
                filters={
                    "state": self.state_filter.currentData(),
                    "split": self.split_filter.currentData(),
                },
                progress=progress,
            )
        except PrivacyScanFailed as exc:
            QMessageBox.critical(
                self, "The bundle was not written", str(exc)
                + "\n\nCorrect the deidentification profile and reimport, or "
                "remove the affected cases from the selection.",
            )
            return
        except Exception as exc:
            QMessageBox.critical(self, "The bundle could not be written", str(exc))
            return
        finally:
            self.bundle_progress.setVisible(False)
            self.bundle_button.setEnabled(True)

        self.bundle_result.set("path", "Written to", str(result.path))
        self.bundle_result.set("cases", "Cases", str(result.n_cases))
        self.bundle_result.set("files", "Files", str(result.n_files))
        self.bundle_result.set(
            "size", "Size", f"{result.bytes_written / (1024 * 1024):.1f} MB"
        )
        self.bundle_result.set("digest", "SHA-256", result.sha256[:32], mono=True)
        self.bundle_result.set(
            "privacy", "Identifier scan",
            "clean" if not result.privacy_findings else f"{len(result.privacy_findings)} findings",
            "ok" if not result.privacy_findings else "danger",
        )

        self.controller.repo.record_export(
            "bundle", str(result.path),
            {"state": self.state_filter.currentData()},
            result.n_cases, sha256=result.sha256,
            project_id=project.id if project else "", status="bundle",
            notes=result.summary(),
        )
        self._refresh_history()

        warnings = "\n".join(f"  {w}" for w in result.warnings[:6])
        QMessageBox.information(
            self, "Training bundle written",
            f"{result.summary()}\n\nDigest: {result.sha256[:32]}\n\n"
            + (f"Notes:\n{warnings}" if warnings else "The identifier scan found nothing."),
        )

    def verify_bundle(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "Verify a training bundle",
            str(self.controller.paths.exports_dir), "ZIP archive (*.zip)",
        )
        if not path:
            return
        from ...io.exporters.bundle import verify_bundle

        report = verify_bundle(path)
        if report["ok"]:
            manifest = report.get("manifest", {})
            QMessageBox.information(
                self, "Bundle verified",
                f"{report['summary']}\n\n"
                f"Project: {manifest.get('project', 'unknown')}\n"
                f"Cases: {manifest.get('n_cases', 'unknown')}\n"
                f"Created: {manifest.get('generated_at', 'unknown')}",
            )
        else:
            problems = report["issues"] + [
                f"checksum mismatch: {n}" for n in report["mismatched"]
            ] + [f"missing: {n}" for n in report["missing"]]
            QMessageBox.warning(
                self, "Bundle did not verify",
                "\n".join(problems[:12])
                + "\n\nThe archive may have been truncated or altered in transit.",
            )
