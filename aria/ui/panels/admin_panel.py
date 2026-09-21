"""Administration: projects, schema, users, thresholds, privacy and policy.

The decisions that need clinical sign off are collected here with the sign off
state visible, rather than buried in a configuration file. A screening threshold
cannot be switched on without recording who approved it, and DICOM derived
output cannot be claimed without recording the validator and target viewer
results.
"""

from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.models import Project, Role, utc_now
from ...core.schema import LABEL_CLASSES, ProjectSchema, ThresholdRule
from ...io.deident import BUILTIN_PROFILES
from ...security.auth import AuthService, Permission
from ..theme import PALETTE
from ..widgets.common import (
    Banner,
    CollapsibleSection,
    KeyValueGrid,
    ScrollPanel,
    SectionLabel,
    StatusChip,
    make_button,
)


class AdminPanel(QWidget):
    """Project, schema, user and policy administration."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.scroll = ScrollPanel(self)
        layout.addWidget(self.scroll)

        self._build_projects_section()
        self._build_schema_section()
        self._build_thresholds_section()
        self._build_users_section()
        self._build_privacy_section()
        self._build_dicom_section()
        self._build_signoff_section()

    # -- projects ------------------------------------------------------------

    def _build_projects_section(self) -> None:
        self.projects_section = CollapsibleSection("Projects", self, True, "folder")
        body = self.projects_section.body_layout()

        self.project_list = QListWidget(self)
        self.project_list.setMaximumHeight(120)
        body.addWidget(self.project_list)

        row = QHBoxLayout()
        self.new_project_button = make_button("New project", "folder", parent=self)
        self.rename_project_button = make_button("Rename", parent=self)
        self.archive_project_button = make_button("Archive", danger=True, parent=self)
        for b in (self.new_project_button, self.rename_project_button, self.archive_project_button):
            row.addWidget(b)
        body.addLayout(row)

        self.project_details = KeyValueGrid(self)
        body.addWidget(self.project_details)

        self.new_project_button.clicked.connect(self._new_project)
        self.rename_project_button.clicked.connect(self._rename_project)
        self.archive_project_button.clicked.connect(self._archive_project)
        self.project_list.currentItemChanged.connect(self._on_project_selected)

        self.scroll.add_section(self.projects_section)

    def _new_project(self) -> None:
        if not self.controller.can(Permission.MANAGE_PROJECTS):
            return
        name, ok = QInputDialog.getText(self, "New project", "Project name:")
        if not ok or not name.strip():
            return
        project = Project(
            name=name.strip(),
            created_by=self.controller.user.id if self.controller.user else "",
            schema_json=json.dumps(ProjectSchema().to_dict()),
        )
        self.controller.repo.create_project(project)
        self.controller.set_project(project)
        self.refresh()

    def _rename_project(self) -> None:
        project = self._selected_project()
        if project is None:
            return
        name, ok = QInputDialog.getText(
            self, "Rename project", "Project name:", text=project.name
        )
        if not ok or not name.strip():
            return
        project.name = name.strip()
        self.controller.repo.update_project(project, "Project renamed.")
        self.refresh()

    def _archive_project(self) -> None:
        project = self._selected_project()
        if project is None:
            return
        answer = QMessageBox.question(
            self, "Archive project",
            f"Archive {project.name}?\n\nIts cases and annotations are kept and "
            f"stay readable. The project stops appearing in the case browser.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        project.archived = True
        self.controller.repo.update_project(project, "Project archived.")
        self.refresh()

    def _selected_project(self):
        item = self.project_list.currentItem()
        if item is None:
            return None
        return self.controller.repo.get_project(item.data(Qt.UserRole))

    def _on_project_selected(self, current, _previous) -> None:
        if current is None:
            return
        project = self.controller.repo.get_project(current.data(Qt.UserRole))
        if project is None:
            return
        stats = self.controller.repo.project_statistics(project.id)
        self.project_details.set("name", "Name", project.name)
        self.project_details.set("created", "Created", project.created_at[:19].replace("T", " "))
        self.project_details.set("profile", "Privacy profile", project.deid_profile)
        self.project_details.set("cases", "Cases", str(stats["total_cases"]))
        self.project_details.set("objects", "Annotation objects", str(stats["total_annotations"]))
        by_state = ", ".join(f"{k} {v}" for k, v in stats["by_state"].items())
        self.project_details.set("states", "By state", by_state or "none")
        self.controller.set_project(project)
        self._load_schema_into_form()

    # -- schema --------------------------------------------------------------

    def _build_schema_section(self) -> None:
        self.schema_section = CollapsibleSection("Label schema and tolerances", self, False, "grade")
        body = self.schema_section.body_layout()

        note = QLabel(
            "Required labels, allowed omissions and acceptance tolerances are "
            "project configuration. Changing them is versioned and existing "
            "annotations stay readable.",
            self,
        )
        note.setWordWrap(True)
        note.setProperty("dim", True)
        body.addWidget(note)

        body.addWidget(SectionLabel("Required labels", self))
        self.required_tree = QTreeWidget(self)
        self.required_tree.setHeaderLabels(["Label", "Required"])
        self.required_tree.setRootIsDecorated(False)
        self.required_tree.setAlternatingRowColors(True)
        self.required_tree.setMinimumHeight(180)
        self.required_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        body.addWidget(self.required_tree)

        row = QHBoxLayout()
        row.addWidget(QLabel("Mandible mode", self))
        self.mandible_mode = QComboBox(self)
        self.mandible_mode.addItem("Whole mandible", "whole")
        self.mandible_mode.addItem("Paired hemimandibles", "hemimandible")
        row.addWidget(self.mandible_mode, 1)
        body.addLayout(row)

        body.addWidget(SectionLabel("Acceptance tolerances", self))
        self.tolerance_fields = {}
        for key, label, minimum, maximum, step, decimals in (
            ("point_tolerance_px", "Point distance, pixels", 0.5, 200.0, 0.5, 1),
            ("line_endpoint_tolerance_px", "Line endpoint, pixels", 0.5, 200.0, 0.5, 1),
            ("measurement_tolerance_mm", "Measurement, millimetres", 0.01, 10.0, 0.05, 2),
            ("mask_dice_tolerance", "Mask Dice", 0.1, 1.0, 0.05, 2),
            ("duplicate_fraction", "Duplicate annotation fraction", 0.0, 1.0, 0.05, 2),
        ):
            field_row = QHBoxLayout()
            field_row.addWidget(QLabel(label, self))
            spin = QDoubleSpinBox(self)
            spin.setRange(minimum, maximum)
            spin.setSingleStep(step)
            spin.setDecimals(decimals)
            field_row.addWidget(spin)
            field_row.addStretch(1)
            body.addLayout(field_row)
            self.tolerance_fields[key] = spin

        roi_row = QHBoxLayout()
        roi_row.addWidget(QLabel("Analysis region size, pixels", self))
        self.roi_size = QSpinBox(self)
        self.roi_size.setRange(16, 512)
        self.roi_size.setSingleStep(8)
        self.roi_size.setToolTip(
            "Region size changes texture feature values, so it is fixed per "
            "project and recorded with every result."
        )
        roi_row.addWidget(self.roi_size)
        roi_row.addStretch(1)
        body.addLayout(roi_row)

        self.require_mci = QCheckBox("Require a cortical index grade on both sides", self)
        self.require_laterality = QCheckBox("Require confirmed anatomical orientation", self)
        self.require_calibration = QCheckBox(
            "Require a validated calibration before submission", self
        )
        self.allow_magnification = QCheckBox(
            "Allow magnification correction, device policy approved", self
        )
        for box in (
            self.require_mci, self.require_laterality,
            self.require_calibration, self.allow_magnification,
        ):
            body.addWidget(box)

        self.save_schema_button = make_button(
            "Save schema", "save", accent=True, parent=self
        )
        self.save_schema_button.clicked.connect(self._save_schema)
        body.addWidget(self.save_schema_button)

        self.scroll.add_section(self.schema_section)

    def _load_schema_into_form(self) -> None:
        schema = self.controller.schema
        self.required_tree.clear()
        for cls in LABEL_CLASSES:
            item = QTreeWidgetItem(self.required_tree, [cls.display_name, ""])
            item.setData(0, Qt.UserRole, cls.key)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(
                1, Qt.Checked if schema.is_required(cls.key) else Qt.Unchecked
            )
            item.setToolTip(0, cls.description)

        index = self.mandible_mode.findData(schema.mandible_mode)
        if index >= 0:
            self.mandible_mode.setCurrentIndex(index)
        for key, spin in self.tolerance_fields.items():
            spin.setValue(float(getattr(schema, key)))
        self.roi_size.setValue(int(schema.roi_size_px))
        self.require_mci.setChecked(schema.require_mci_grade)
        self.require_laterality.setChecked(schema.require_laterality_confirmation)
        self.require_calibration.setChecked(schema.require_calibration_for_submission)
        self.allow_magnification.setChecked(schema.allow_magnification_correction)
        self._refresh_thresholds()

    def _save_schema(self) -> None:
        if not self.controller.can(Permission.MANAGE_SCHEMA):
            QMessageBox.information(self, "Not permitted", "This account cannot change the schema.")
            return
        schema = self.controller.schema
        required = []
        root = self.required_tree.invisibleRootItem()
        for i in range(root.childCount()):
            item = root.child(i)
            if item.checkState(1) == Qt.Checked:
                required.append(item.data(0, Qt.UserRole))
        schema.required_classes = required
        schema.mandible_mode = self.mandible_mode.currentData()
        for key, spin in self.tolerance_fields.items():
            setattr(schema, key, spin.value())
        schema.roi_size_px = self.roi_size.value()
        schema.require_mci_grade = self.require_mci.isChecked()
        schema.require_laterality_confirmation = self.require_laterality.isChecked()
        schema.require_calibration_for_submission = self.require_calibration.isChecked()
        schema.allow_magnification_correction = self.allow_magnification.isChecked()

        self.controller.save_schema(schema)
        QMessageBox.information(
            self, "Schema saved",
            "The project schema was saved and the change is recorded in the audit "
            "history. Existing annotations remain readable.",
        )

    # -- thresholds ----------------------------------------------------------

    def _build_thresholds_section(self) -> None:
        self.thresholds_section = CollapsibleSection(
            "Screening thresholds", self, False, "measure"
        )
        body = self.thresholds_section.body_layout()

        warning = Banner(self)
        warning.show_message(
            "A threshold is a screening rule for a study protocol. It is never a "
            "diagnosis, it is versioned, and it stays off until clinical approval "
            "is recorded here.",
            "info",
        )
        body.addWidget(warning)

        self.threshold_table = QTableWidget(0, 5, self)
        self.threshold_table.setHorizontalHeaderLabels(
            ["Rule", "Measure", "Condition", "Approved", "Active"]
        )
        self.threshold_table.verticalHeader().setVisible(False)
        self.threshold_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.threshold_table.setAlternatingRowColors(True)
        self.threshold_table.setMinimumHeight(130)
        self.threshold_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        body.addWidget(self.threshold_table)

        row = QHBoxLayout()
        self.add_threshold_button = make_button("Add rule", parent=self)
        self.approve_threshold_button = make_button("Record clinical approval", parent=self)
        self.remove_threshold_button = make_button("Remove", danger=True, parent=self)
        for b in (
            self.add_threshold_button, self.approve_threshold_button,
            self.remove_threshold_button,
        ):
            row.addWidget(b)
        body.addLayout(row)

        self.add_threshold_button.clicked.connect(self._add_threshold)
        self.approve_threshold_button.clicked.connect(self._approve_threshold)
        self.remove_threshold_button.clicked.connect(self._remove_threshold)

        self.scroll.add_section(self.thresholds_section)

    def _refresh_thresholds(self) -> None:
        schema = self.controller.schema
        self.threshold_table.setRowCount(len(schema.thresholds))
        for row, rule in enumerate(schema.thresholds):
            self.threshold_table.setItem(row, 0, QTableWidgetItem(rule.display_name))
            self.threshold_table.setItem(row, 1, QTableWidgetItem(rule.measure))
            self.threshold_table.setItem(
                row, 2, QTableWidgetItem(f"{rule.comparator} {rule.value} {rule.unit}")
            )
            approved = QTableWidgetItem(
                f"{rule.approved_by} {(rule.approved_at or '')[:10]}"
                if rule.clinically_approved else "not approved"
            )
            self.threshold_table.setItem(row, 3, approved)
            active = QTableWidgetItem("active" if rule.is_active() else "inactive")
            self.threshold_table.setItem(row, 4, active)

    def _add_threshold(self) -> None:
        from ...core.measurements import MeasurementKind

        measures = [k.value for k in MeasurementKind]
        measure, ok = QInputDialog.getItem(
            self, "New screening rule", "Measure:", measures, 0, False
        )
        if not ok:
            return
        name, ok = QInputDialog.getText(self, "New screening rule", "Rule name:")
        if not ok or not name.strip():
            return
        comparator, ok = QInputDialog.getItem(
            self, "New screening rule", "Condition:", ["lt", "lte", "gt", "gte"], 0, False
        )
        if not ok:
            return
        value, ok = QInputDialog.getDouble(
            self, "New screening rule", "Threshold value:", 3.0, -1e6, 1e6, 4
        )
        if not ok:
            return
        unit, ok = QInputDialog.getItem(
            self, "New screening rule", "Unit:", ["mm", "ratio", "px"], 0, False
        )
        if not ok:
            return

        rule = ThresholdRule(
            key=name.strip().lower().replace(" ", "_"),
            display_name=name.strip(), measure=measure, comparator=comparator,
            value=value, unit=unit, enabled=False, clinically_approved=False,
        )
        self.controller.schema.thresholds.append(rule)
        self.controller.save_schema(self.controller.schema)
        self._refresh_thresholds()

    def _approve_threshold(self) -> None:
        row = self.threshold_table.currentRow()
        if row < 0 or row >= len(self.controller.schema.thresholds):
            return
        rule = self.controller.schema.thresholds[row]
        approver, ok = QInputDialog.getText(
            self, "Record clinical approval",
            f"Approving {rule.display_name} makes it active and its outcome "
            f"appears beside measurements as a screening rule.\n\n"
            f"Name of the clinician approving this rule:",
        )
        if not ok or not approver.strip():
            return
        citation, _ = QInputDialog.getText(
            self, "Record clinical approval", "Protocol reference or citation:"
        )
        rule.clinically_approved = True
        rule.enabled = True
        rule.approved_by = approver.strip()
        rule.approved_at = utc_now()
        rule.citation = citation.strip()
        self.controller.save_schema(self.controller.schema)
        self._refresh_thresholds()

    def _remove_threshold(self) -> None:
        row = self.threshold_table.currentRow()
        if row < 0 or row >= len(self.controller.schema.thresholds):
            return
        del self.controller.schema.thresholds[row]
        self.controller.save_schema(self.controller.schema)
        self._refresh_thresholds()

    # -- users ---------------------------------------------------------------

    def _build_users_section(self) -> None:
        self.users_section = CollapsibleSection("Accounts", self, False, "users")
        body = self.users_section.body_layout()

        self.user_table = QTableWidget(0, 5, self)
        self.user_table.setHorizontalHeaderLabels(
            ["Name", "Role", "Pseudonym", "Calibration set", "Active"]
        )
        self.user_table.verticalHeader().setVisible(False)
        self.user_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.user_table.setAlternatingRowColors(True)
        self.user_table.setMinimumHeight(160)
        self.user_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        body.addWidget(self.user_table)

        row = QHBoxLayout()
        self.add_user_button = make_button("Add account", "user", parent=self)
        self.reset_password_button = make_button("Reset password", parent=self)
        self.toggle_active_button = make_button("Activate or deactivate", parent=self)
        for b in (self.add_user_button, self.reset_password_button, self.toggle_active_button):
            row.addWidget(b)
        body.addLayout(row)

        self.approve_calibration_button = make_button(
            "Approve calibration set", "check",
            tooltip=(
                "Record that this annotator passed the calibration set and may "
                "take production cases."
            ),
            parent=self,
        )
        body.addWidget(self.approve_calibration_button)

        self.add_user_button.clicked.connect(self._add_user)
        self.reset_password_button.clicked.connect(self._reset_password)
        self.toggle_active_button.clicked.connect(self._toggle_active)
        self.approve_calibration_button.clicked.connect(self._approve_calibration)

        self.scroll.add_section(self.users_section)

    def _refresh_users(self) -> None:
        users = self.controller.repo.list_users(include_inactive=True)
        self.user_table.setRowCount(len(users))
        for row, user in enumerate(users):
            self.user_table.setItem(row, 0, QTableWidgetItem(user.display_name or user.username))
            self.user_table.setItem(row, 1, QTableWidgetItem(user.role_display))
            self.user_table.setItem(row, 2, QTableWidgetItem(user.pseudonym))
            self.user_table.setItem(
                row, 3,
                QTableWidgetItem("passed" if user.calibration_passed else "not recorded"),
            )
            self.user_table.setItem(
                row, 4, QTableWidgetItem("active" if user.active else "inactive")
            )
            self.user_table.item(row, 0).setData(Qt.UserRole, user.id)

    def _selected_user(self):
        row = self.user_table.currentRow()
        if row < 0:
            return None
        item = self.user_table.item(row, 0)
        return self.controller.repo.get_user(item.data(Qt.UserRole)) if item else None

    def _add_user(self) -> None:
        if not self.controller.can(Permission.MANAGE_USERS):
            return
        from ..dialogs.account_dialog import AccountDialog

        dialog = AccountDialog(self.controller, self)
        if dialog.exec():
            self._refresh_users()

    def _reset_password(self) -> None:
        user = self._selected_user()
        if user is None or not self.controller.can(Permission.MANAGE_USERS):
            return
        service = AuthService(self.controller.repo, self.controller.settings)
        temporary = service.reset_password(self.controller.user, user)
        QMessageBox.information(
            self, "Password reset",
            f"A temporary password was set for {user.username}:\n\n{temporary}\n\n"
            f"They must change it at the next sign in. Pass it to them directly "
            f"rather than by email.",
        )
        self._refresh_users()

    def _toggle_active(self) -> None:
        user = self._selected_user()
        if user is None or not self.controller.can(Permission.MANAGE_USERS):
            return
        if user.id == (self.controller.user.id if self.controller.user else ""):
            QMessageBox.information(
                self, "Not allowed", "An account cannot deactivate itself."
            )
            return
        user.active = not user.active
        self.controller.repo.update_user(
            user, f"Account {'activated' if user.active else 'deactivated'}."
        )
        self._refresh_users()

    def _approve_calibration(self) -> None:
        user = self._selected_user()
        if user is None or not self.controller.can(Permission.APPROVE_CALIBRATION_SET):
            return
        answer = QMessageBox.question(
            self, "Approve calibration set",
            f"Record that {user.display_name} completed the calibration set and "
            f"may take production cases?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        user.calibration_passed = True
        user.calibration_passed_at = utc_now()
        user.calibration_approved_by = (
            self.controller.user.pseudonym if self.controller.user else ""
        )
        self.controller.repo.update_user(user, "Calibration set approved.")

        from ...core.models import CalibrationSetResult

        self.controller.repo.save_calibration_set(
            CalibrationSetResult(
                user_id=user.id,
                project_id=self.controller.project.id if self.controller.project else "",
                passed=True,
                approved_by=user.calibration_approved_by,
                approved_at=user.calibration_passed_at,
                notes="Approved by a reviewer in the administration module.",
            )
        )
        self._refresh_users()

    # -- privacy -------------------------------------------------------------

    def _build_privacy_section(self) -> None:
        self.privacy_section = CollapsibleSection("Privacy profile", self, False, "lock")
        body = self.privacy_section.body_layout()

        self.profile_combo = QComboBox(self)
        for key, profile in BUILTIN_PROFILES.items():
            self.profile_combo.addItem(profile.display_name, key)
            self.profile_combo.setItemData(
                self.profile_combo.count() - 1, profile.description, Qt.ToolTipRole
            )
        body.addWidget(self.profile_combo)

        self.profile_description = QLabel("", self)
        self.profile_description.setWordWrap(True)
        self.profile_description.setProperty("dim", True)
        body.addWidget(self.profile_description)

        body.addWidget(SectionLabel("Prohibited terms", self))
        terms_note = QLabel(
            "Institution specific words that must never appear in an export, one "
            "per line. Every export is scanned for them before it is written.",
            self,
        )
        terms_note.setWordWrap(True)
        terms_note.setProperty("dim", True)
        body.addWidget(terms_note)

        self.prohibited_terms = QTextEdit(self)
        self.prohibited_terms.setMaximumHeight(90)
        body.addWidget(self.prohibited_terms)

        self.save_privacy_button = make_button("Save privacy settings", "save", parent=self)
        self.save_privacy_button.clicked.connect(self._save_privacy)
        body.addWidget(self.save_privacy_button)

        self.profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        self.scroll.add_section(self.privacy_section)

    def _on_profile_changed(self, _index: int) -> None:
        key = self.profile_combo.currentData()
        profile = BUILTIN_PROFILES.get(key)
        if profile:
            self.profile_description.setText(profile.description)

    def _save_privacy(self) -> None:
        if not self.controller.can(Permission.MANAGE_POLICY):
            return
        project = self.controller.project
        if project is not None:
            project.deid_profile = self.profile_combo.currentData()
            self.controller.repo.update_project(
                project, f"Deidentification profile set to {project.deid_profile}."
            )
        terms = [
            line.strip() for line in self.prohibited_terms.toPlainText().splitlines()
            if line.strip()
        ]
        self.controller.settings.prohibited_terms = terms
        self.controller.config.save()
        self.controller.repo.log(
            "deid_profile_changed", "project",
            project.id if project else "",
            after={"profile": self.profile_combo.currentData(), "n_terms": len(terms)},
            detail="Privacy settings updated.",
        )
        QMessageBox.information(
            self, "Privacy settings saved",
            f"The profile applies to images imported from now on. "
            f"{len(terms)} prohibited terms will be checked on every export.",
        )

    # -- DICOM output --------------------------------------------------------

    def _build_dicom_section(self) -> None:
        self.dicom_section = CollapsibleSection("DICOM derived output", self, False, "export")
        body = self.dicom_section.body_layout()

        note = QLabel(
            "Structured Report and Segmentation output stays switched off until a "
            "validator result and each target viewer result are recorded here. An "
            "object a receiving system mis-reads is worse than no object at all.",
            self,
        )
        note.setWordWrap(True)
        note.setProperty("dim", True)
        body.addWidget(note)

        self.dicom_status = StatusChip("Not accepted", "warn", self)
        body.addWidget(self.dicom_status)

        row = QHBoxLayout()
        row.addWidget(QLabel("Validator", self))
        self.validator_name = QLineEdit(self)
        self.validator_name.setPlaceholderText("Name of the conformance validator used")
        row.addWidget(self.validator_name, 1)
        body.addLayout(row)

        self.validator_passed = QCheckBox("The objects passed the validator", self)
        body.addWidget(self.validator_passed)

        body.addWidget(SectionLabel("Target viewers", self))
        self.viewer_list = QListWidget(self)
        self.viewer_list.setMaximumHeight(100)
        body.addWidget(self.viewer_list)

        row = QHBoxLayout()
        self.add_viewer_button = make_button("Record a viewer result", parent=self)
        self.remove_viewer_button = make_button("Remove", parent=self)
        row.addWidget(self.add_viewer_button)
        row.addWidget(self.remove_viewer_button)
        body.addLayout(row)

        self.dicom_enabled = QCheckBox("Enable DICOM derived output", self)
        body.addWidget(self.dicom_enabled)

        self.dicom_reasons = QLabel("", self)
        self.dicom_reasons.setWordWrap(True)
        self.dicom_reasons.setProperty("dim", True)
        body.addWidget(self.dicom_reasons)

        self.save_dicom_button = make_button("Save interoperability record", "save", parent=self)
        self.save_dicom_button.clicked.connect(self._save_dicom)
        body.addWidget(self.save_dicom_button)

        self.add_viewer_button.clicked.connect(self._add_viewer)
        self.remove_viewer_button.clicked.connect(
            lambda: self.viewer_list.takeItem(self.viewer_list.currentRow())
        )

        self.scroll.add_section(self.dicom_section)

    def _load_dicom(self) -> None:
        from ...io.exporters.dicom_output import InteroperabilityRecord

        record = InteroperabilityRecord.from_dict(
            json.loads(self.controller.repo.db.get_meta("dicom_interop") or "{}")
        )
        self.validator_name.setText(record.validator_name)
        self.validator_passed.setChecked(record.validator_passed)
        self.viewer_list.clear()
        for viewer in record.target_viewers:
            item = QListWidgetItem(
                f"{viewer.get('viewer')} {viewer.get('version', '')}   "
                f"{'passed' if viewer.get('passed') else 'not passed'}",
                self.viewer_list,
            )
            item.setData(Qt.UserRole, viewer)
        self.dicom_enabled.setChecked(self.controller.settings.dicom_output_enabled)
        satisfied = record.is_satisfied()
        self.dicom_status.set_state(
            "Interoperability accepted" if satisfied else "Not accepted",
            "ok" if satisfied else "warn",
        )
        self.dicom_reasons.setText(
            "" if satisfied else "Outstanding: " + "  ".join(record.blocking_reasons())
        )

    def _add_viewer(self) -> None:
        name, ok = QInputDialog.getText(
            self, "Record a viewer result", "Viewer name:"
        )
        if not ok or not name.strip():
            return
        version, _ = QInputDialog.getText(self, "Record a viewer result", "Version:")
        answer = QMessageBox.question(
            self, "Record a viewer result",
            f"Did the derived objects open correctly in {name.strip()}?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        entry = {
            "viewer": name.strip(), "version": version.strip(),
            "passed": answer == QMessageBox.Yes, "notes": "",
        }
        item = QListWidgetItem(
            f"{entry['viewer']} {entry['version']}   "
            f"{'passed' if entry['passed'] else 'not passed'}",
            self.viewer_list,
        )
        item.setData(Qt.UserRole, entry)

    def _save_dicom(self) -> None:
        if not self.controller.can(Permission.MANAGE_POLICY):
            return
        from ...io.exporters.dicom_output import (
            COMPREHENSIVE_SR_SOP_CLASS, EXPLICIT_VR_LITTLE_ENDIAN,
            InteroperabilityRecord, SEGMENTATION_SOP_CLASS,
        )

        viewers = [
            self.viewer_list.item(i).data(Qt.UserRole)
            for i in range(self.viewer_list.count())
        ]
        record = InteroperabilityRecord(
            validator_name=self.validator_name.text().strip(),
            validator_passed=self.validator_passed.isChecked(),
            validated_at=utc_now(),
            validated_by=self.controller.user.pseudonym if self.controller.user else "",
            target_viewers=viewers,
            sop_classes_documented=[COMPREHENSIVE_SR_SOP_CLASS, SEGMENTATION_SOP_CLASS],
            transfer_syntaxes_documented=[EXPLICIT_VR_LITTLE_ENDIAN],
        )
        self.controller.repo.db.set_meta("dicom_interop", json.dumps(record.to_dict()))

        satisfied = record.is_satisfied()
        wanted = self.dicom_enabled.isChecked()
        if wanted and not satisfied:
            QMessageBox.warning(
                self, "Cannot enable DICOM output yet",
                "Interoperability is not accepted:\n\n"
                + "\n".join(f"  {r}" for r in record.blocking_reasons())
                + "\n\nThe record was saved and output stays switched off.",
            )
            self.controller.settings.dicom_output_enabled = False
            self.dicom_enabled.setChecked(False)
        else:
            self.controller.settings.dicom_output_enabled = wanted
        self.controller.settings.dicom_output_validated = satisfied
        self.controller.config.save()
        self.controller.repo.log(
            "policy_changed", "settings", "dicom_output",
            after=record.to_dict(), detail="DICOM interoperability record updated.",
        )
        self._load_dicom()

    # -- sign off ------------------------------------------------------------

    def _build_signoff_section(self) -> None:
        self.signoff_section = CollapsibleSection(
            "Decisions needing clinical sign off", self, False, "check"
        )
        body = self.signoff_section.body_layout()

        note = QLabel(
            "These decisions belong to the clinical team. ARIA records the state "
            "so nobody has to remember which have been settled.",
            self,
        )
        note.setWordWrap(True)
        note.setProperty("dim", True)
        body.addWidget(note)

        self.signoff_list = QListWidget(self)
        self.signoff_list.setMinimumHeight(170)
        self.signoff_list.setWordWrap(True)
        body.addWidget(self.signoff_list)

        self.scroll.add_section(self.signoff_section)

    def _refresh_signoff(self) -> None:
        schema = self.controller.schema
        self.signoff_list.clear()

        items = [
            (
                "Cortical width naming convention",
                "The canonical record is stored once and exported with the agreed "
                "aliases MCW, CWI and MI beside it.",
                True,
            ),
            (
                "Analysis region placement and size",
                f"Region size is fixed at {schema.roi_size_px} by {schema.roi_size_px} "
                f"pixels and recorded with every texture result.",
                schema.roi_size_px > 0,
            ),
            (
                "Calibration policy per device",
                "Magnification correction is "
                + ("allowed." if schema.allow_magnification_correction else "switched off."),
                True,
            ),
            (
                "Mandatory labels and not assessable rules",
                f"{len(schema.required_classes)} labels are required. Absence is "
                f"recorded explicitly.",
                bool(schema.required_classes),
            ),
            (
                "Duplicate annotation fraction and agreement thresholds",
                f"Duplicate fraction {schema.duplicate_fraction:.0%}. Tolerances are "
                f"project configuration, not a universal cutoff.",
                schema.duplicate_fraction > 0,
            ),
            (
                "Supported DICOM classes, syntaxes, validators and viewers",
                "Recorded in the DICOM derived output section above.",
                self.controller.settings.dicom_output_validated,
            ),
        ]
        for title, detail, settled in items:
            glyph = "✓" if settled else "○"
            item = QListWidgetItem(f"{glyph}  {title}\n      {detail}", self.signoff_list)
            item.setToolTip(detail)
            if not settled:
                from PySide6.QtGui import QBrush, QColor

                item.setForeground(QBrush(QColor(PALETTE.warning)))

    # -- refresh -------------------------------------------------------------

    def refresh(self) -> None:
        self.project_list.clear()
        for project in self.controller.repo.list_projects():
            item = QListWidgetItem(project.name, self.project_list)
            item.setData(Qt.UserRole, project.id)
            if self.controller.project and project.id == self.controller.project.id:
                self.project_list.setCurrentItem(item)
        if self.project_list.currentItem() is None and self.project_list.count():
            self.project_list.setCurrentRow(0)

        self._load_schema_into_form()
        self._refresh_users()
        self._load_dicom()
        self._refresh_signoff()

        project = self.controller.project
        if project is not None:
            index = self.profile_combo.findData(project.deid_profile)
            if index >= 0:
                self.profile_combo.setCurrentIndex(index)
        self.prohibited_terms.setPlainText(
            "\n".join(self.controller.settings.prohibited_terms)
        )

        allowed = self.controller.can(Permission.MANAGE_PROJECTS)
        for widget in (
            self.new_project_button, self.rename_project_button,
            self.archive_project_button, self.save_schema_button,
            self.add_user_button, self.reset_password_button,
            self.toggle_active_button, self.save_privacy_button,
            self.save_dicom_button,
        ):
            widget.setEnabled(allowed)
