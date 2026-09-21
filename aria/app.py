"""Application startup.

The order matters and is deliberate:

1. logging, so a failure in any later step is recorded,
2. configuration and paths, so everything else knows where to write,
3. the database, with migrations applied and a startup backup taken,
4. first run setup when there is no account yet,
5. sign in,
6. the main window, and the guided tour when it has not been seen.

A failure in any of these is reported in a dialog that says what happened and
what to do, rather than a traceback on a console nobody is watching.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import traceback
from pathlib import Path

from .config import Config, get_config, set_config
from .version import APP_LONG_NAME, APP_NAME, APP_VERSION

LOG_FORMAT = "%(asctime)s  %(levelname)-8s  %(name)-28s  %(message)s"


def configure_logging(config: Config) -> logging.Logger:
    """File and console logging, both forced to UTF-8.

    The console on Windows defaults to a legacy code page, and a status glyph in
    a log line would otherwise raise an encoding error inside the logger itself.
    """
    log_dir = config.paths.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(getattr(logging, config.settings.log_level.upper(), logging.INFO))
    for handler in list(root.handlers):
        root.removeHandler(handler)

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "aria.log", maxBytes=4 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(file_handler)

    try:
        stream = logging.StreamHandler(sys.stderr)
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        stream.setFormatter(logging.Formatter(LOG_FORMAT))
        root.addHandler(stream)
    except (OSError, ValueError, AttributeError):
        # A packaged build without a console has no usable stderr. The file
        # handler is the one that matters.
        pass

    logger = logging.getLogger("aria")
    logger.info("%s %s starting", APP_NAME, APP_VERSION)
    logger.info("Data folder: %s", config.paths.data_dir)
    return logger


def prune_logs(config: Config) -> None:
    """Remove log files older than the configured retention."""
    import time

    days = max(1, int(config.settings.log_retention_days))
    cutoff = time.time() - days * 86400
    try:
        for path in config.paths.log_dir.glob("aria.log.*"):
            if path.stat().st_mtime < cutoff:
                path.unlink()
    except OSError:
        pass


def take_startup_backup(database, config: Config) -> None:
    """Keep a rolling set of database backups."""
    if not config.settings.backup_on_launch:
        return
    from datetime import datetime

    logger = logging.getLogger("aria.backup")
    try:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        destination = config.paths.backups_dir / f"aria_backup_{stamp}.db"
        database.backup_to(destination)
        logger.info("Startup backup written to %s", destination)

        backups = sorted(
            config.paths.backups_dir.glob("aria_backup_*.db"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in backups[max(1, int(config.settings.backup_keep_count)) :]:
            old.unlink()
    except Exception as exc:
        logger.warning("The startup backup could not be taken: %s", exc)


def install_excepthook(app, log_dir: Path) -> None:
    """Report an unexpected error in a dialog rather than losing it."""
    from PySide6.QtWidgets import QMessageBox

    logger = logging.getLogger("aria.unhandled")

    def handle(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        logger.error("Unhandled error\n%s", text)

        box = QMessageBox()
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle(f"{APP_NAME} met an unexpected problem")
        box.setText(
            f"{exc_type.__name__}: {exc_value}\n\n"
            f"Work already saved is safe. Every completed change is written as it "
            f"is made."
        )
        box.setInformativeText(
            f"The details were written to the log in:\n{log_dir}\n\n"
            f"Run Tools, Run diagnostics and include the report when asking for help."
        )
        box.setDetailedText(text)
        box.exec()

    sys.excepthook = handle


def apply_theme(app, config: Config) -> None:
    from .ui.theme import HIGH_CONTRAST, PALETTE, stylesheet

    settings = config.settings
    palette = HIGH_CONTRAST if settings.high_contrast_annotations else PALETTE
    app.setStyleSheet(
        stylesheet(palette, settings.font_point_size, settings.ui_scale)
    )


def create_application(argv=None):
    """Build the QApplication with the platform settings ARIA needs."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

    existing = QApplication.instance()
    if existing is not None:
        # Creating a second QApplication in one process raises. Reusing the
        # existing one keeps startup working when ARIA is launched twice in a
        # session, driven by a test, or hosted inside another Qt application.
        app = existing
    else:
        if hasattr(Qt, "AA_UseHighDpiPixmaps"):
            QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
        app = QApplication(argv if argv is not None else sys.argv)

    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("DiceMed")
    app.setStyle("Fusion")

    if sys.platform == "darwin":
        font = QFont(".AppleSystemUIFont")
    elif sys.platform.startswith("win"):
        font = QFont("Segoe UI")
    else:
        font = QFont("Noto Sans")
    app.setFont(font)
    return app


def run(argv=None) -> int:
    """Start ARIA. Returns the process exit code."""
    from PySide6.QtWidgets import QMessageBox

    config = Config().load()
    set_config(config)
    logger = configure_logging(config)
    prune_logs(config)

    app = create_application(argv)
    apply_theme(app, config)
    install_excepthook(app, config.paths.log_dir)

    from .ui.icons import application_icon

    app.setWindowIcon(application_icon())

    # -- database --------------------------------------------------------
    from .store.db import open_database
    from .store.repository import Repository

    try:
        database = open_database(config.paths.database)
    except Exception as exc:
        logger.exception("The database could not be opened")
        QMessageBox.critical(
            None, f"{APP_NAME} cannot start",
            f"The database could not be opened.\n\n{exc}\n\n"
            f"Check that the data folder is reachable and writable:\n"
            f"{config.paths.data_dir}\n\n"
            f"A backup may be available in the backups folder.",
        )
        return 1

    applied = database.migrate(database.schema_version())
    if applied:
        logger.info("Database migrations applied: %s", "; ".join(applied))

    integrity = database.integrity_check()
    if not integrity["ok"]:
        logger.error("Database integrity check failed: %s", integrity)
        answer = QMessageBox.warning(
            None, "Database problem",
            "The database did not pass its integrity check.\n\n"
            + "\n".join(f"{k}: {v}" for k, v in integrity["checks"].items())
            + "\n\nContinue anyway, or close and restore from a backup?",
            QMessageBox.Ok | QMessageBox.Close, QMessageBox.Close,
        )
        if answer == QMessageBox.Close:
            database.close()
            return 1

    take_startup_backup(database, config)
    repository = Repository(database)

    # -- first run -------------------------------------------------------
    from .security.auth import AuthService

    service = AuthService(repository, config.settings)
    show_tour_after_setup = False

    if service.bootstrap_needed() or not config.settings.first_run_completed:
        from PySide6.QtWidgets import QDialog

        from .ui.dialogs.first_run import FirstRunWizard

        wizard = FirstRunWizard(repository, config.paths, config, None)
        # The result code is a class attribute, not an instance one. Reading it
        # from the instance raises, which is exactly the kind of failure that
        # only shows up on a first launch.
        if wizard.exec() != QDialog.DialogCode.Accepted:
            logger.info("Setup was cancelled")
            database.close()
            return 0
        user = wizard.created_user
        project = wizard.created_project
        show_tour_after_setup = wizard.show_tour
        session = service.start_session(user)
        repository.set_actor(user)
    else:
        from PySide6.QtWidgets import QDialog

        from .ui.dialogs.login_dialog import LoginDialog

        dialog = LoginDialog(repository, config, None)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.user is None:
            logger.info("Sign in was cancelled")
            database.close()
            return 0
        user = dialog.user
        session = service.start_session(user)
        repository.set_actor(user)
        projects = repository.list_projects()
        project = projects[0] if projects else None

    # -- vault -----------------------------------------------------------
    if config.settings.encrypt_at_rest:
        from .security.crypto import Vault, VaultError

        try:
            vault = Vault(config.paths.key_file)
            vault.unlock()
        except VaultError as exc:
            logger.warning("Encryption at rest is unavailable: %s", exc)

    # -- main window -----------------------------------------------------
    from .ui.main_window import MainWindow

    window = MainWindow(repository, config.paths, config, session)
    if project is not None:
        window.controller.set_project(project)
    window.restore_geometry()
    window._apply_settings()
    window.show()
    window.case_browser.reload_projects()

    if config.settings.last_version_run != APP_VERSION:
        config.settings.last_version_run = APP_VERSION
        config.save()

    from .ui.dialogs.tour import should_offer_tour

    if show_tour_after_setup or should_offer_tour(config.settings):
        from PySide6.QtCore import QTimer
        from .ui.dialogs.tour import GuidedTour

        def start_tour():
            tour = GuidedTour(window)
            tour.start()

        QTimer.singleShot(420, start_tour)

    logger.info("Signed in as %s (%s)", user.username, user.role)
    exit_code = app.exec()

    try:
        repository.release_all_locks()
        database.checkpoint()
        database.close()
    except Exception:
        logger.exception("Shutdown did not complete cleanly")

    logger.info("%s closed with code %s", APP_NAME, exit_code)
    return exit_code


def main() -> int:
    try:
        return run()
    except Exception:
        traceback.print_exc()
        return 1
