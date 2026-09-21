"""First run: compatibility check, administrator account, first project.

The compatibility check runs before anything else, because finding out that a
workstation cannot do the work after importing a study wastes real time. A
failed check can be overridden deliberately, with the override recorded, since
a research machine sometimes falls short of a requirement in a way the team has
already accepted.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from ...core.models import Project, Role
from ...core.schema import ProjectSchema
from ...platform.system_check import CheckStatus, run_system_check
from ...security.auth import AuthService
from ...version import APP_LONG_NAME, APP_NAME, APP_VERSION
from ..icons import application_icon, icon as make_icon
from ..theme import PALETTE
from ..widgets.common import Banner, StatusChip


class WelcomePage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle(f"Welcome to {APP_NAME}")
        self.setSubTitle(APP_LONG_NAME)

        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        heading = QLabel(
            f"{APP_NAME} version {APP_VERSION} is set up on this workstation once, "
            f"and this takes a minute.",
            self,
        )
        heading.setWordWrap(True)
        layout.addWidget(heading)

        body = QLabel(
            "Three things happen next:\n\n"
            "1.  This workstation is checked against the requirements, so you know "
            "before you start whether it can do the work.\n\n"
            "2.  An administrator account is created. Everything you do in ARIA is "
            "recorded against an account, which is what makes the audit history "
            "meaningful.\n\n"
            "3.  A first project is created, holding its own label schema, "
            "tolerances and privacy profile.",
            self,
        )
        body.setWordWrap(True)
        layout.addWidget(body)

        scope = QLabel(
            "ARIA is an annotation and research data tool. It records landmarks, "
            "contours, regions, measurements, grades and quality flags, with the "
            "provenance needed to reproduce them. It does not diagnose "
            "osteoporosis, it does not estimate bone mineral density, and it does "
            "not recommend treatment.",
            self,
        )
        scope.setWordWrap(True)
        scope.setStyleSheet(
            f"color: {PALETTE.text_dim}; border-left: 3px solid {PALETTE.accent};"
            f" padding: 10px 12px; background: {PALETTE.panel_alt};"
        )
        layout.addWidget(scope)

        offline = QLabel(
            "Everything runs on this machine. ARIA has no network layer: it does "
            "not send images, metadata or annotations anywhere.",
            self,
        )
        offline.setWordWrap(True)
        offline.setProperty("dim", True)
        layout.addWidget(offline)
        layout.addStretch(1)


class CheckWorker(QThread):
    finished_check = Signal(object)

    def __init__(self, paths, parent=None):
        super().__init__(parent)
        self.paths = paths

    def run(self) -> None:
        report = run_system_check(self.paths, include_display=False)
        self.finished_check.emit(report)


class CompatibilityPage(QWizardPage):
    """Runs the checks and reports them, grouped by category."""

    def __init__(self, paths, config, parent=None):
        super().__init__(parent)
        self.paths = paths
        self.config = config
        self.report = None
        self._override = False

        self.setTitle("Workstation compatibility")
        self.setSubTitle(
            "Checking that this machine meets the requirements for annotation work."
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        self.progress = QProgressBar(self)
        self.progress.setRange(0, 0)
        layout.addWidget(self.progress)

        self.verdict = QLabel("Running checks...", self)
        self.verdict.setWordWrap(True)
        font = QFont()
        font.setBold(True)
        self.verdict.setFont(font)
        layout.addWidget(self.verdict)

        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels(["Check", "Result", "Requirement"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setMinimumHeight(280)
        self.tree.setColumnWidth(0, 250)
        self.tree.setColumnWidth(1, 250)
        layout.addWidget(self.tree, 1)

        self.banner = Banner(self)
        layout.addWidget(self.banner)

        self.override = QCheckBox(
            "Continue even though a requirement is not met, and record that choice",
            self,
        )
        self.override.setVisible(False)
        self.override.toggled.connect(self._on_override)
        layout.addWidget(self.override)

    def initializePage(self) -> None:
        self.worker = CheckWorker(self.paths, self)
        self.worker.finished_check.connect(self._on_report)
        self.worker.start()

    def _on_report(self, report) -> None:
        self.report = report
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.progress.setVisible(False)

        self.tree.clear()
        for category, results in report.by_category().items():
            parent = QTreeWidgetItem(self.tree, [category, "", ""])
            font = QFont()
            font.setBold(True)
            parent.setFont(0, font)
            parent.setExpanded(True)
            for result in results:
                status = result.status_enum
                item = QTreeWidgetItem(
                    parent,
                    [f"{status.glyph}  {result.name}", result.measured, result.requirement],
                )
                colour = {
                    CheckStatus.PASS: PALETTE.success,
                    CheckStatus.WARN: PALETTE.warning,
                    CheckStatus.FAIL: PALETTE.danger,
                    CheckStatus.UNKNOWN: PALETTE.text_dim,
                }[status]
                from PySide6.QtGui import QBrush, QColor

                item.setForeground(0, QBrush(QColor(colour)))
                tooltip = f"{result.name}\n\nMeasured: {result.measured}"
                if result.requirement:
                    tooltip += f"\nRequirement: {result.requirement}"
                if result.remedy:
                    tooltip += f"\n\nWhat to do: {result.remedy}"
                if result.detail:
                    tooltip += f"\n\n{result.detail}"
                for column in range(3):
                    item.setToolTip(column, tooltip)

        self.verdict.setText(report.verdict())
        if report.failures:
            self.banner.show_message(
                "   ".join(
                    f"{f.name}: {f.remedy}" for f in report.failures[:2]
                ),
                "danger",
            )
            self.override.setVisible(True)
        elif report.warnings:
            self.banner.show_message(
                f"{len(report.warnings)} points worth noting. Hover any row for "
                f"the detail.",
                "warn",
            )
        else:
            self.banner.show_message("Every requirement is met.", "ok")

        self.config.settings.compatibility_check_passed = report.can_run
        from ...core.models import utc_now

        self.config.settings.compatibility_check_at = utc_now()
        self.config.save()
        self.completeChanged.emit()

    def _on_override(self, value: bool) -> None:
        self._override = value
        self.completeChanged.emit()

    def isComplete(self) -> bool:
        if self.report is None:
            return False
        return self.report.can_run or self._override

    def override_recorded(self) -> bool:
        return self._override


class AccountPage(QWizardPage):
    """Creates the first administrator account."""

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setTitle("Administrator account")
        self.setSubTitle(
            "This account manages projects, accounts and policy. Create other "
            "accounts from the Administration module afterwards."
        )

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(9)

        self.username = QLineEdit(self)
        self.username.setPlaceholderText("Used to sign in")
        self.display_name = QLineEdit(self)
        self.display_name.setPlaceholderText("Shown in the interface")
        self.password = QLineEdit(self)
        self.password.setEchoMode(QLineEdit.Password)
        self.confirm = QLineEdit(self)
        self.confirm.setEchoMode(QLineEdit.Password)

        form.addRow("Username", self.username)
        form.addRow("Display name", self.display_name)
        form.addRow("Password", self.password)
        form.addRow("Confirm password", self.confirm)
        layout.addLayout(form)

        self.policy = QLabel("", self)
        self.policy.setWordWrap(True)
        self.policy.setProperty("dim", True)
        layout.addWidget(self.policy)

        self.status = QLabel("", self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        note = QLabel(
            "Passwords are stored as a memory hard digest, never as text. If this "
            "password is lost there is no way to recover the account, so an "
            "administrator should create a second administrator account once "
            "setup is finished.",
            self,
        )
        note.setWordWrap(True)
        note.setProperty("dim", True)
        layout.addWidget(note)
        layout.addStretch(1)

        for field in (self.username, self.display_name, self.password, self.confirm):
            field.textChanged.connect(self._validate)

        self.registerField("username*", self.username)
        self.registerField("display_name", self.display_name)
        self.registerField("password*", self.password)

    def initializePage(self) -> None:
        settings = self.config.settings
        rules = [f"at least {settings.password_min_length} characters"]
        if settings.password_require_mixed_case:
            rules.append("upper and lower case")
        if settings.password_require_digit:
            rules.append("a digit")
        if settings.password_require_symbol:
            rules.append("a symbol")
        self.policy.setText("The password needs " + ", ".join(rules) + ".")

    def _validate(self) -> None:
        problems = []
        if not self.username.text().strip():
            problems.append("A username is required.")
        problems.extend(self.config.settings.validate_password(self.password.text()))
        if self.password.text() != self.confirm.text():
            problems.append("The two passwords do not match.")

        if problems:
            self.status.setText("   ".join(problems))
            self.status.setStyleSheet(f"color: {PALETTE.warning};")
        else:
            self.status.setText("This password meets the policy.")
            self.status.setStyleSheet(f"color: {PALETTE.success};")
        self._complete = not problems
        self.completeChanged.emit()

    def isComplete(self) -> bool:
        return getattr(self, "_complete", False)


class ProjectPage(QWizardPage):
    """Creates the first project."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("First project")
        self.setSubTitle(
            "A project holds its cases, its label schema, its tolerances and its "
            "privacy profile."
        )

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(9)

        self.name = QLineEdit(self)
        self.name.setText("Mandibular radiomorphometric study")
        self.description = QLineEdit(self)
        self.description.setPlaceholderText("Optional")

        self.profile = QComboBox(self)
        from ...io.deident import BUILTIN_PROFILES

        for key, profile in BUILTIN_PROFILES.items():
            self.profile.addItem(profile.display_name, key)
            self.profile.setItemData(
                self.profile.count() - 1, profile.description, Qt.ToolTipRole
            )
        index = self.profile.findData("aria_default")
        if index >= 0:
            self.profile.setCurrentIndex(index)

        self.mandible = QComboBox(self)
        self.mandible.addItem("Whole mandible outline", "whole")
        self.mandible.addItem("Paired hemimandibles", "hemimandible")

        form.addRow("Project name", self.name)
        form.addRow("Description", self.description)
        form.addRow("Privacy profile", self.profile)
        form.addRow("Mandible labelling", self.mandible)
        layout.addLayout(form)

        self.profile_description = QLabel("", self)
        self.profile_description.setWordWrap(True)
        self.profile_description.setProperty("dim", True)
        layout.addWidget(self.profile_description)

        self.tour_checkbox = QCheckBox("Show the guided tour after setup", self)
        self.tour_checkbox.setChecked(True)
        layout.addWidget(self.tour_checkbox)
        layout.addStretch(1)

        self.profile.currentIndexChanged.connect(self._on_profile)
        self._on_profile(0)
        self.registerField("project_name*", self.name)

    def _on_profile(self, _index: int) -> None:
        from ...io.deident import BUILTIN_PROFILES

        profile = BUILTIN_PROFILES.get(self.profile.currentData())
        if profile:
            self.profile_description.setText(profile.description)


class FirstRunWizard(QWizard):
    """The first run sequence."""

    def __init__(self, repository, paths, config, parent=None):
        super().__init__(parent)
        self.repo = repository
        self.paths = paths
        self.config = config
        self.created_user = None
        self.created_project = None
        self.show_tour = True

        self.setWindowTitle(f"{APP_NAME} setup")
        self.setWindowIcon(application_icon())
        self.setWizardStyle(QWizard.ModernStyle)
        self.setOption(QWizard.NoBackButtonOnStartPage, True)
        self.setMinimumSize(760, 580)

        self.welcome_page = WelcomePage(self)
        self.compatibility_page = CompatibilityPage(paths, config, self)
        self.account_page = AccountPage(config, self)
        self.project_page = ProjectPage(self)

        self.addPage(self.welcome_page)
        self.addPage(self.compatibility_page)
        self.addPage(self.account_page)
        self.addPage(self.project_page)

        self.setButtonText(QWizard.FinishButton, "Finish setup")

    def accept(self) -> None:
        service = AuthService(self.repo, self.config.settings)
        try:
            user = service.create_account(
                self.account_page.username.text().strip(),
                self.account_page.display_name.text().strip(),
                Role.ADMIN,
                self.account_page.password.text(),
                must_change=False,
            )
        except ValueError as exc:
            QMessageBox.warning(self, "The account could not be created", str(exc))
            return

        self.repo.set_actor(user)
        self.created_user = user

        schema = ProjectSchema()
        schema.mandible_mode = self.project_page.mandible.currentData()
        project = Project(
            name=self.project_page.name.text().strip(),
            description=self.project_page.description.text().strip(),
            created_by=user.id,
            deid_profile=self.project_page.profile.currentData(),
            schema_json=__import__("json").dumps(schema.to_dict()),
        )
        self.repo.create_project(project)
        self.created_project = project

        if self.compatibility_page.override_recorded():
            self.repo.log(
                "policy_changed", "settings", "compatibility_override",
                after={"overridden": True},
                detail=(
                    "Setup continued although a compatibility requirement was not "
                    "met. The check result is in the diagnostics report."
                ),
            )

        self.config.settings.first_run_completed = True
        self.config.save()
        self.show_tour = self.project_page.tour_checkbox.isChecked()
        super().accept()
