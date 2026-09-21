"""Application paths and persisted preferences.

Locations follow each platform's own convention rather than inventing one, so
backup tools, profile roaming and disk cleanup behave the way an administrator
expects:

Windows
    Data     %LOCALAPPDATA%\\ARIA
    Config   %APPDATA%\\ARIA
    Logs     %LOCALAPPDATA%\\ARIA\\logs

macOS
    Data     ~/Library/Application Support/ARIA
    Config   ~/Library/Preferences/ARIA
    Logs     ~/Library/Logs/ARIA

The data root can be moved to another drive from Preferences, Storage, which is
the usual case on a research workstation where the system drive is small.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .version import APP_NAME, APP_VERSION

IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"
IS_LINUX = not IS_WINDOWS and not IS_MACOS


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def default_data_dir() -> Path:
    override = os.environ.get("ARIA_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or str(_home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    if IS_MACOS:
        return _home() / "Library" / "Application Support" / APP_NAME
    base = os.environ.get("XDG_DATA_HOME") or str(_home() / ".local" / "share")
    return Path(base) / APP_NAME.lower()


def default_config_dir() -> Path:
    override = os.environ.get("ARIA_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    if IS_WINDOWS:
        base = os.environ.get("APPDATA") or str(_home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    if IS_MACOS:
        return _home() / "Library" / "Preferences" / APP_NAME
    base = os.environ.get("XDG_CONFIG_HOME") or str(_home() / ".config")
    return Path(base) / APP_NAME.lower()


def default_log_dir() -> Path:
    if IS_MACOS:
        return _home() / "Library" / "Logs" / APP_NAME
    return default_data_dir() / "logs"


def default_cache_dir() -> Path:
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or str(_home() / "AppData" / "Local")
        return Path(base) / APP_NAME / "cache"
    if IS_MACOS:
        return _home() / "Library" / "Caches" / APP_NAME
    base = os.environ.get("XDG_CACHE_HOME") or str(_home() / ".cache")
    return Path(base) / APP_NAME.lower()


@dataclass
class Paths:
    """Every location ARIA writes to, resolved once at startup."""

    data_dir: Path = field(default_factory=default_data_dir)
    config_dir: Path = field(default_factory=default_config_dir)
    log_dir: Path = field(default_factory=default_log_dir)
    cache_dir: Path = field(default_factory=default_cache_dir)

    @property
    def database(self) -> Path:
        return self.data_dir / "aria.db"

    @property
    def sources_dir(self) -> Path:
        """Retained original files, never modified after import (FR 002)."""
        return self.data_dir / "sources"

    @property
    def working_dir(self) -> Path:
        """Working representations, derived and rebuildable from the sources."""
        return self.data_dir / "working"

    @property
    def thumbnails_dir(self) -> Path:
        return self.cache_dir / "thumbnails"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def journal_dir(self) -> Path:
        """Crash recovery journal written alongside every material change."""
        return self.data_dir / "journal"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def settings_file(self) -> Path:
        return self.config_dir / "settings.json"

    @property
    def key_file(self) -> Path:
        return self.config_dir / "vault.key"

    def all_dirs(self) -> tuple:
        return (
            self.data_dir, self.config_dir, self.log_dir, self.cache_dir,
            self.sources_dir, self.working_dir, self.thumbnails_dir,
            self.exports_dir, self.journal_dir, self.backups_dir,
        )

    def ensure(self) -> None:
        for d in self.all_dirs():
            d.mkdir(parents=True, exist_ok=True)

    def source_path(self, sha256: str, filename: str) -> Path:
        """Content addressed location of a retained original.

        Two levels of prefix directories keep any single folder from holding
        tens of thousands of entries, which some file systems handle poorly.
        """
        return self.sources_dir / sha256[:2] / sha256[2:4] / f"{sha256}_{filename}"

    def working_path(self, case_id: str) -> Path:
        return self.working_dir / f"{case_id}.npz"

    def thumbnail_path(self, case_id: str) -> Path:
        return self.thumbnails_dir / f"{case_id}.png"

    def to_dict(self) -> dict:
        return {k: str(v) for k, v in asdict(self).items()}


@dataclass
class Settings:
    """User and workstation preferences.

    Institution policy settings (session timeout, password rules, retention)
    live here so they can be configured to an approved policy (NFR 005).
    """

    # -- first run ---------------------------------------------------------
    first_run_completed: bool = False
    tour_completed: bool = False
    tour_version_seen: str = ""
    compatibility_check_passed: bool = False
    compatibility_check_at: str = ""
    last_version_run: str = ""

    # -- appearance --------------------------------------------------------
    theme: str = "dark"
    ui_scale: float = 1.0
    font_point_size: int = 9
    high_contrast_annotations: bool = False
    show_annotation_labels: bool = True
    annotation_line_width: float = 2.0
    landmark_size: float = 7.0
    show_data_probe: bool = True
    layout: str = "one_up"
    remember_window_geometry: bool = True
    window_geometry: str = ""
    window_state: str = ""

    # -- viewer ------------------------------------------------------------
    invert_scroll_zoom: bool = False
    zoom_step: float = 1.15
    smooth_zoom: bool = True
    crosshair: bool = True
    magnifier_enabled: bool = True
    magnifier_factor: float = 4.0

    # -- storage -----------------------------------------------------------
    data_dir_override: str = ""
    keep_working_copies: bool = True
    thumbnail_max_edge: int = 480

    # -- import limits (guardrails) ---------------------------------------
    max_file_mb: int = 512
    max_pixels_millions: int = 250
    max_batch_files: int = 5000

    # -- autosave and recovery --------------------------------------------
    autosave_enabled: bool = True
    autosave_debounce_ms: int = 400
    journal_enabled: bool = True
    backup_on_launch: bool = True
    backup_keep_count: int = 10

    # -- security policy ---------------------------------------------------
    session_timeout_minutes: int = 30
    lock_on_idle: bool = True
    password_min_length: int = 10
    password_require_mixed_case: bool = True
    password_require_digit: bool = True
    password_require_symbol: bool = False
    password_max_age_days: int = 365
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    encrypt_at_rest: bool = True
    retention_days: int = 0            # zero means no automatic deletion
    #: Institution declared prohibited identifier terms, checked on export.
    prohibited_terms: list = field(default_factory=list)
    #: Third party transmission is off. Kept explicit so the state is visible.
    allow_third_party_services: bool = False

    # -- exports -----------------------------------------------------------
    default_export_dir: str = ""
    include_masks_in_bundle: bool = True
    include_raw_in_bundle: bool = True
    bundle_compression: int = 6
    dicom_output_enabled: bool = False
    dicom_output_validated: bool = False

    # -- diagnostics -------------------------------------------------------
    log_level: str = "INFO"
    log_retention_days: int = 30
    recent_projects: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Settings":
        allowed = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in (data or {}).items() if k in allowed})

    def import_limits(self) -> dict:
        return {
            "max_file_bytes": int(self.max_file_mb) * 1024 * 1024,
            "max_pixels": int(self.max_pixels_millions) * 1_000_000,
            "max_batch_files": int(self.max_batch_files),
        }

    def validate_password(self, password: str) -> list:
        """Check a password against the configured policy."""
        problems: list = []
        if len(password) < self.password_min_length:
            problems.append(
                f"The password must be at least {self.password_min_length} characters."
            )
        if self.password_require_mixed_case and (
            password.lower() == password or password.upper() == password
        ):
            problems.append("The password must contain both upper and lower case letters.")
        if self.password_require_digit and not any(c.isdigit() for c in password):
            problems.append("The password must contain at least one digit.")
        if self.password_require_symbol and password.isalnum():
            problems.append("The password must contain at least one symbol.")
        return problems


class Config:
    """Loads and saves settings, and resolves paths."""

    def __init__(self, paths: Paths | None = None):
        self.paths = paths or Paths()
        self.settings = Settings()
        self._loaded = False

    def load(self) -> "Config":
        self.paths.config_dir.mkdir(parents=True, exist_ok=True)
        f = self.paths.settings_file
        if f.exists():
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    self.settings = Settings.from_dict(json.load(fh))
            except (OSError, ValueError):
                # A damaged settings file must not stop the application. The
                # file is kept aside so it can be inspected.
                try:
                    f.rename(f.with_suffix(".json.damaged"))
                except OSError:
                    pass
                self.settings = Settings()
        if self.settings.data_dir_override:
            self.paths.data_dir = Path(self.settings.data_dir_override).expanduser()
        self.paths.ensure()
        self._loaded = True
        return self

    def save(self) -> None:
        from .io.fsutil import atomic_write_text

        self.paths.config_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            self.paths.settings_file,
            json.dumps(self.settings.to_dict(), indent=2, sort_keys=True),
        )

    def reset_to_defaults(self) -> None:
        keep_first_run = self.settings.first_run_completed
        self.settings = Settings()
        self.settings.first_run_completed = keep_first_run
        self.save()

    def describe(self) -> dict:
        return {
            "application": APP_NAME,
            "version": APP_VERSION,
            "platform": sys.platform,
            "paths": self.paths.to_dict(),
        }


_CONFIG: Config | None = None


def get_config() -> Config:
    """Process wide configuration, loaded on first use."""
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = Config().load()
    return _CONFIG


def set_config(config: Config) -> None:
    """Replace the process wide configuration. Used by the test suite."""
    global _CONFIG
    _CONFIG = config
