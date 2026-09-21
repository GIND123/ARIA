"""Workstation compatibility and readiness checks.

This runs before the first session and can be repeated at any time from the Help
menu. The purpose is to tell someone whether this machine can actually do the
work before they import a study and find out the hard way.

Every check reports a status, the value it measured, the requirement it was
measured against, and what to do when it does not pass. A check never guesses:
when something cannot be determined it says so rather than reporting a pass.
"""

from __future__ import annotations

import os
import platform
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from ..version import APP_NAME, APP_VERSION


class CheckStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    UNKNOWN = "unknown"

    @property
    def glyph(self) -> str:
        return {
            CheckStatus.PASS: "✓",
            CheckStatus.WARN: "!",
            CheckStatus.FAIL: "✗",
            CheckStatus.UNKNOWN: "?",
        }[self]

    @property
    def display(self) -> str:
        return {
            CheckStatus.PASS: "Pass",
            CheckStatus.WARN: "Warning",
            CheckStatus.FAIL: "Not met",
            CheckStatus.UNKNOWN: "Not determined",
        }[self]


class Category(str, Enum):
    RUNTIME = "Runtime"
    HARDWARE = "Hardware"
    DISPLAY = "Display"
    STORAGE = "Storage"
    SECURITY = "Security"
    PERFORMANCE = "Performance"


@dataclass
class CheckResult:
    key: str
    name: str
    category: str
    status: str
    measured: str = ""
    requirement: str = ""
    remedy: str = ""
    detail: str = ""

    @property
    def status_enum(self) -> CheckStatus:
        return CheckStatus(self.status)

    def to_dict(self) -> dict:
        return {
            "key": self.key, "name": self.name, "category": self.category,
            "status": self.status, "measured": self.measured,
            "requirement": self.requirement, "remedy": self.remedy,
            "detail": self.detail,
        }


@dataclass
class SystemReport:
    results: list = field(default_factory=list)
    machine: dict = field(default_factory=dict)
    generated_at: str = ""

    def add(self, result: CheckResult) -> None:
        self.results.append(result)

    @property
    def failures(self) -> list:
        return [r for r in self.results if r.status == CheckStatus.FAIL.value]

    @property
    def warnings(self) -> list:
        return [r for r in self.results if r.status == CheckStatus.WARN.value]

    @property
    def can_run(self) -> bool:
        return not self.failures

    def verdict(self) -> str:
        if self.failures:
            return (
                f"This workstation does not meet {len(self.failures)} "
                f"{'requirement' if len(self.failures) == 1 else 'requirements'}. "
                f"{APP_NAME} may not work correctly until they are resolved."
            )
        if self.warnings:
            return (
                f"This workstation meets every requirement, with "
                f"{len(self.warnings)} {'point' if len(self.warnings) == 1 else 'points'} "
                f"worth noting."
            )
        return "This workstation meets every requirement."

    def by_category(self) -> dict:
        grouped: dict = {}
        for r in self.results:
            grouped.setdefault(r.category, []).append(r)
        return grouped

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "application": APP_NAME,
            "version": APP_VERSION,
            "can_run": self.can_run,
            "verdict": self.verdict(),
            "machine": dict(self.machine),
            "results": [r.to_dict() for r in self.results],
        }

    def to_text(self) -> str:
        lines = [
            f"{APP_NAME} {APP_VERSION} compatibility report",
            f"Generated {self.generated_at}",
            "",
            self.verdict(),
            "",
        ]
        for category, items in self.by_category().items():
            lines.append(category)
            lines.append("-" * len(category))
            for r in items:
                lines.append(f"  [{r.status_enum.glyph}] {r.name}: {r.measured}")
                if r.requirement:
                    lines.append(f"      Requirement: {r.requirement}")
                if r.status != CheckStatus.PASS.value and r.remedy:
                    lines.append(f"      Action: {r.remedy}")
            lines.append("")
        return "\n".join(lines)


#: Minimum and recommended requirements for the reference workstation.
REQUIREMENTS = {
    "python_minimum": (3, 10),
    "cpu_cores_minimum": 2,
    "cpu_cores_recommended": 4,
    "memory_minimum_gb": 4.0,
    "memory_recommended_gb": 8.0,
    "disk_minimum_gb": 5.0,
    "disk_recommended_gb": 50.0,
    "screen_width_minimum": 1280,
    "screen_height_minimum": 720,
    "screen_width_recommended": 1920,
    "screen_height_recommended": 1080,
    "colour_depth_minimum": 24,
    "load_seconds_target": 5.0,
}


def _bytes_to_gb(n) -> float:
    return float(n) / (1024 ** 3)


def machine_summary() -> dict:
    info = {
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor() or platform.machine(),
        "python": platform.python_version(),
        "python_build": " ".join(platform.python_build()),
        "executable": sys.executable,
        "frozen": bool(getattr(sys, "frozen", False)),
        "node": "",
    }
    try:
        import psutil

        info["cpu_logical"] = psutil.cpu_count(logical=True)
        info["cpu_physical"] = psutil.cpu_count(logical=False)
        info["memory_total_gb"] = round(_bytes_to_gb(psutil.virtual_memory().total), 1)
    except Exception:
        info["cpu_logical"] = os.cpu_count()
    return info


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_python() -> CheckResult:
    required = REQUIREMENTS["python_minimum"]
    current = sys.version_info[:2]
    ok = current >= required
    return CheckResult(
        key="python_version",
        name="Python runtime",
        category=Category.RUNTIME.value,
        status=CheckStatus.PASS.value if ok else CheckStatus.FAIL.value,
        measured=platform.python_version(),
        requirement=f"{required[0]}.{required[1]} or later",
        remedy=(
            "" if ok else
            "Install the packaged build of ARIA, which carries its own runtime."
        ),
    )


def check_qt() -> CheckResult:
    try:
        from PySide6 import __version__ as pyside_version
        from PySide6.QtCore import qVersion

        return CheckResult(
            key="qt", name="Interface toolkit", category=Category.RUNTIME.value,
            status=CheckStatus.PASS.value,
            measured=f"Qt {qVersion()} through PySide {pyside_version}",
            requirement="Qt 6.5 or later",
        )
    except ImportError as exc:
        return CheckResult(
            key="qt", name="Interface toolkit", category=Category.RUNTIME.value,
            status=CheckStatus.FAIL.value, measured="Not available",
            requirement="Qt 6.5 or later",
            remedy="Reinstall ARIA. The interface toolkit is part of the installation.",
            detail=str(exc),
        )


def check_dependencies() -> list:
    """Confirm each library ARIA relies on is importable."""
    required = [
        ("numpy", "Numerical processing", True),
        ("pydicom", "DICOM reading", True),
        ("PIL", "PNG reading", True),
        ("sqlite3", "Local database", True),
        ("cryptography", "Encryption at rest", False),
        ("psutil", "Resource monitoring", False),
    ]
    results: list = []
    for module, purpose, mandatory in required:
        try:
            mod = __import__(module)
            version = getattr(mod, "__version__", getattr(mod, "version", "present"))
            results.append(
                CheckResult(
                    key=f"dep_{module}", name=purpose, category=Category.RUNTIME.value,
                    status=CheckStatus.PASS.value, measured=f"{module} {version}",
                    requirement="Available",
                )
            )
        except ImportError as exc:
            results.append(
                CheckResult(
                    key=f"dep_{module}", name=purpose, category=Category.RUNTIME.value,
                    status=CheckStatus.FAIL.value if mandatory else CheckStatus.WARN.value,
                    measured="Not available", requirement="Available",
                    remedy=(
                        "Reinstall ARIA."
                        if mandatory
                        else f"{purpose} is unavailable until this component is installed."
                    ),
                    detail=str(exc),
                )
            )
    return results


def check_dicom_codecs() -> CheckResult:
    """Report which compressed transfer syntaxes can be decoded."""
    available: list = []
    for module, label in (("libjpeg", "JPEG family"), ("openjpeg", "JPEG 2000")):
        try:
            __import__(module)
            available.append(label)
        except ImportError:
            continue
    if available:
        return CheckResult(
            key="dicom_codecs", name="Compressed DICOM decoders",
            category=Category.RUNTIME.value, status=CheckStatus.PASS.value,
            measured=", ".join(available),
            requirement="Optional, needed only for compressed studies",
        )
    return CheckResult(
        key="dicom_codecs", name="Compressed DICOM decoders",
        category=Category.RUNTIME.value, status=CheckStatus.WARN.value,
        measured="None installed",
        requirement="Optional, needed only for compressed studies",
        remedy=(
            "Uncompressed studies work as they are. To read JPEG or JPEG 2000 "
            "compressed studies, install the optional decoder package."
        ),
    )


def check_cpu() -> CheckResult:
    cores = os.cpu_count() or 1
    physical = cores
    try:
        import psutil

        physical = psutil.cpu_count(logical=False) or cores
    except Exception:
        pass
    minimum = REQUIREMENTS["cpu_cores_minimum"]
    recommended = REQUIREMENTS["cpu_cores_recommended"]
    if cores < minimum:
        status = CheckStatus.FAIL.value
    elif cores < recommended:
        status = CheckStatus.WARN.value
    else:
        status = CheckStatus.PASS.value
    return CheckResult(
        key="cpu", name="Processor cores", category=Category.HARDWARE.value,
        status=status,
        measured=f"{cores} logical, {physical} physical",
        requirement=f"{minimum} minimum, {recommended} recommended",
        remedy=(
            "" if status == CheckStatus.PASS.value else
            "Texture analysis and large exports will be slower on this machine."
        ),
    )


def check_memory() -> CheckResult:
    try:
        import psutil

        vm = psutil.virtual_memory()
        total = _bytes_to_gb(vm.total)
        available = _bytes_to_gb(vm.available)
    except Exception:
        return CheckResult(
            key="memory", name="System memory", category=Category.HARDWARE.value,
            status=CheckStatus.UNKNOWN.value, measured="Could not be determined",
            requirement=f"{REQUIREMENTS['memory_minimum_gb']} GB minimum",
            remedy="Install the resource monitoring component to enable this check.",
        )
    minimum = REQUIREMENTS["memory_minimum_gb"]
    recommended = REQUIREMENTS["memory_recommended_gb"]
    if total < minimum:
        status = CheckStatus.FAIL.value
        remedy = (
            "Large panoramic images may fail to open. Add memory, or work with "
            "smaller images."
        )
    elif total < recommended or available < 1.5:
        status = CheckStatus.WARN.value
        remedy = (
            "Close other applications before opening large studies."
            if available < 1.5
            else "More memory would give smoother panning on large images."
        )
    else:
        status = CheckStatus.PASS.value
        remedy = ""
    return CheckResult(
        key="memory", name="System memory", category=Category.HARDWARE.value,
        status=status,
        measured=f"{total:.1f} GB total, {available:.1f} GB available",
        requirement=f"{minimum:.0f} GB minimum, {recommended:.0f} GB recommended",
        remedy=remedy,
    )


def check_disk(data_dir) -> CheckResult:
    path = Path(data_dir)
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        usage = shutil.disk_usage(probe)
        free = _bytes_to_gb(usage.free)
        total = _bytes_to_gb(usage.total)
    except OSError as exc:
        return CheckResult(
            key="disk", name="Free storage", category=Category.STORAGE.value,
            status=CheckStatus.UNKNOWN.value, measured="Could not be determined",
            requirement=f"{REQUIREMENTS['disk_minimum_gb']} GB free minimum",
            remedy="Check that the storage location is reachable.", detail=str(exc),
        )
    minimum = REQUIREMENTS["disk_minimum_gb"]
    recommended = REQUIREMENTS["disk_recommended_gb"]
    if free < minimum:
        status = CheckStatus.FAIL.value
    elif free < recommended:
        status = CheckStatus.WARN.value
    else:
        status = CheckStatus.PASS.value
    return CheckResult(
        key="disk", name="Free storage", category=Category.STORAGE.value,
        status=status,
        measured=f"{free:.1f} GB free of {total:.1f} GB on {probe}",
        requirement=f"{minimum:.0f} GB minimum, {recommended:.0f} GB recommended",
        remedy=(
            "" if status == CheckStatus.PASS.value else
            "Free space, or move the ARIA data folder to a larger drive in "
            "Preferences, Storage."
        ),
    )


def check_write_access(paths) -> list:
    from ..io.fsutil import ensure_writable

    results: list = []
    for label, path in paths:
        ok, message = ensure_writable(path)
        results.append(
            CheckResult(
                key=f"write_{label.lower().replace(' ', '_')}",
                name=f"Write access, {label}", category=Category.STORAGE.value,
                status=CheckStatus.PASS.value if ok else CheckStatus.FAIL.value,
                measured=str(path), requirement="Readable and writable",
                remedy="" if ok else "Grant write access to this folder, or choose another location.",
                detail=message,
            )
        )
    return results


def check_sqlite(data_dir) -> CheckResult:
    """Confirm SQLite can use write ahead logging and full synchronous writes.

    Both are required for an autosave that survives a power loss, and a network
    share is the usual reason they are unavailable.
    """
    probe = Path(data_dir) / ".aria_sqlite_probe.db"
    try:
        conn = sqlite3.connect(str(probe))
        mode = conn.execute("PRAGMA journal_mode = WAL").fetchone()[0]
        conn.execute("PRAGMA synchronous = FULL")
        sync = conn.execute("PRAGMA synchronous").fetchone()[0]
        conn.execute("CREATE TABLE IF NOT EXISTS probe (a INTEGER)")
        conn.execute("INSERT INTO probe VALUES (1)")
        conn.commit()
        conn.close()
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(probe) + suffix)
            if p.exists():
                p.unlink()
    except sqlite3.Error as exc:
        return CheckResult(
            key="sqlite", name="Database durability", category=Category.STORAGE.value,
            status=CheckStatus.FAIL.value, measured="The database could not be opened",
            requirement="Write ahead logging with full synchronous writes",
            remedy=(
                "Choose a local folder for ARIA data. A network share or a "
                "synchronised folder cannot provide the durability autosave needs."
            ),
            detail=str(exc),
        )
    except OSError as exc:
        return CheckResult(
            key="sqlite", name="Database durability", category=Category.STORAGE.value,
            status=CheckStatus.FAIL.value, measured="The folder is not usable",
            requirement="Write ahead logging with full synchronous writes",
            remedy="Choose a local folder that this account can write to.", detail=str(exc),
        )

    if str(mode).lower() != "wal":
        return CheckResult(
            key="sqlite", name="Database durability", category=Category.STORAGE.value,
            status=CheckStatus.WARN.value,
            measured=f"Journal mode {mode}, SQLite {sqlite3.sqlite_version}",
            requirement="Write ahead logging with full synchronous writes",
            remedy=(
                "Write ahead logging is unavailable here, which usually means a "
                "network share. Move the ARIA data folder to a local drive so "
                "autosave is durable."
            ),
        )
    return CheckResult(
        key="sqlite", name="Database durability", category=Category.STORAGE.value,
        status=CheckStatus.PASS.value,
        measured=f"SQLite {sqlite3.sqlite_version}, journal mode {mode}, synchronous {sync}",
        requirement="Write ahead logging with full synchronous writes",
    )


def check_display() -> list:
    """Screen geometry and colour depth, measured through Qt when it is running."""
    results: list = []
    try:
        from PySide6.QtGui import QGuiApplication

        app = QGuiApplication.instance()
        if app is None:
            return [
                CheckResult(
                    key="display", name="Display", category=Category.DISPLAY.value,
                    status=CheckStatus.UNKNOWN.value,
                    measured="Not measured outside a running interface",
                    requirement=(
                        f"{REQUIREMENTS['screen_width_minimum']} by "
                        f"{REQUIREMENTS['screen_height_minimum']} minimum"
                    ),
                )
            ]
        screens = app.screens()
        primary = app.primaryScreen()
        geo = primary.geometry()
        width, height = geo.width(), geo.height()
        ratio = primary.devicePixelRatio()
        depth = primary.depth()

        min_w = REQUIREMENTS["screen_width_minimum"]
        min_h = REQUIREMENTS["screen_height_minimum"]
        rec_w = REQUIREMENTS["screen_width_recommended"]
        rec_h = REQUIREMENTS["screen_height_recommended"]
        if width < min_w or height < min_h:
            status = CheckStatus.FAIL.value
            remedy = (
                "The annotation workspace needs more screen area. Increase the "
                "resolution, or use a larger display."
            )
        elif width < rec_w or height < rec_h:
            status = CheckStatus.WARN.value
            remedy = (
                "The workspace fits, but panels will need collapsing on this "
                "screen. A larger display is recommended for annotation work."
            )
        else:
            status = CheckStatus.PASS.value
            remedy = ""
        results.append(
            CheckResult(
                key="display_geometry", name="Screen resolution",
                category=Category.DISPLAY.value, status=status,
                measured=(
                    f"{width} by {height} at {ratio:g}x scaling, "
                    f"{len(screens)} {'screen' if len(screens) == 1 else 'screens'}"
                ),
                requirement=f"{min_w} by {min_h} minimum, {rec_w} by {rec_h} recommended",
                remedy=remedy,
            )
        )

        min_depth = REQUIREMENTS["colour_depth_minimum"]
        results.append(
            CheckResult(
                key="display_depth", name="Colour depth",
                category=Category.DISPLAY.value,
                status=CheckStatus.PASS.value if depth >= min_depth else CheckStatus.WARN.value,
                measured=f"{depth} bits per pixel",
                requirement=f"{min_depth} bits minimum",
                remedy=(
                    "" if depth >= min_depth else
                    "Grey level differences across the cortical margin may not "
                    "be distinguishable at this colour depth."
                ),
            )
        )
    except Exception as exc:
        results.append(
            CheckResult(
                key="display", name="Display", category=Category.DISPLAY.value,
                status=CheckStatus.UNKNOWN.value, measured="Could not be determined",
                requirement="", detail=str(exc),
            )
        )
    return results


def check_encryption(key_path) -> list:
    from ..security.crypto import Vault, file_system_encryption_status

    results: list = []
    vault = Vault(key_path)
    status = vault.status()
    results.append(
        CheckResult(
            key="encryption_app", name="Application encryption",
            category=Category.SECURITY.value,
            status=CheckStatus.PASS.value if status.available else CheckStatus.WARN.value,
            measured=(
                ("Available, key created" if status.initialised else "Available, key not yet created")
                if status.available
                else "Not available"
            ),
            requirement="AES-256 in GCM for content at rest",
            remedy="" if status.available else "Reinstall ARIA to restore the encryption component.",
            detail=status.message,
        )
    )
    fs = file_system_encryption_status()
    fs_status = {
        "enabled": CheckStatus.PASS.value,
        "disabled": CheckStatus.WARN.value,
    }.get(fs["detected"], CheckStatus.UNKNOWN.value)
    results.append(
        CheckResult(
            key="encryption_volume", name="Volume encryption",
            category=Category.SECURITY.value, status=fs_status,
            measured=fs["detected"].capitalize(),
            requirement="Recommended by most institutional policies",
            remedy=(
                "" if fs_status == CheckStatus.PASS.value else
                "Enable full disk encryption on this workstation, following the "
                "institution's policy."
            ),
            detail=fs["detail"],
        )
    )
    return results


def check_locale() -> CheckResult:
    encoding = sys.getfilesystemencoding()
    ok = str(encoding).lower().replace("-", "") in ("utf8", "utf8mb4")
    return CheckResult(
        key="locale", name="File name encoding", category=Category.RUNTIME.value,
        status=CheckStatus.PASS.value if ok else CheckStatus.WARN.value,
        measured=str(encoding),
        requirement="UTF-8",
        remedy=(
            "" if ok else
            "File names with characters outside the system code page may not "
            "import. Enable UTF-8 support in the operating system settings."
        ),
    )


def check_long_paths() -> CheckResult:
    """Windows limits paths to 260 characters unless long paths are enabled."""
    if not sys.platform.startswith("win"):
        return CheckResult(
            key="long_paths", name="Long file path support",
            category=Category.STORAGE.value, status=CheckStatus.PASS.value,
            measured="Supported by this operating system", requirement="Paths beyond 260 characters",
        )
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem"
        ) as key:
            value, _ = winreg.QueryValueEx(key, "LongPathsEnabled")
        enabled = bool(value)
    except Exception:
        enabled = False
    return CheckResult(
        key="long_paths", name="Long file path support",
        category=Category.STORAGE.value,
        status=CheckStatus.PASS.value if enabled else CheckStatus.WARN.value,
        measured="Enabled" if enabled else "Not enabled",
        requirement="Paths beyond 260 characters",
        remedy=(
            "" if enabled else
            "Keep the ARIA data folder close to the drive root, or enable long "
            "path support in the operating system."
        ),
    )


def check_performance() -> CheckResult:
    """Measure the work that decides how quickly an image becomes interactive.

    A representative panoramic array is windowed to eight bits, which is the
    per frame cost of the display pipeline, and the result is scaled to the
    reference target in NFR 007.
    """
    try:
        import numpy as np

        rng = np.random.default_rng(7)
        array = rng.integers(0, 16384, (1504, 2868), dtype=np.uint16)

        start = time.perf_counter()
        for _ in range(3):
            scaled = (array.astype(np.float32) - 1000.0) * (255.0 / 12000.0)
            np.clip(scaled, 0, 255).astype(np.uint8)
        window_seconds = (time.perf_counter() - start) / 3.0

        start = time.perf_counter()
        float_copy = array.astype(np.float64)
        float_copy.mean()
        np.percentile(float_copy[::4, ::4], [1, 99])
        stats_seconds = time.perf_counter() - start

        estimate = window_seconds * 2 + stats_seconds + 0.35
        target = REQUIREMENTS["load_seconds_target"]
        if estimate <= target * 0.5:
            status = CheckStatus.PASS.value
            remedy = ""
        elif estimate <= target:
            status = CheckStatus.WARN.value
            remedy = "Loading will meet the target but with little margin on large studies."
        else:
            status = CheckStatus.FAIL.value
            remedy = (
                "This machine is slower than the reference workstation. Expect "
                "delays when opening large panoramic images."
            )
        return CheckResult(
            key="performance", name="Image preparation speed",
            category=Category.PERFORMANCE.value, status=status,
            measured=f"About {estimate:.2f} s to make a 2868 by 1504 image interactive",
            requirement=f"Under {target:.0f} s for a supported panoramic image",
            remedy=remedy,
            detail=(
                f"Windowing pass {window_seconds * 1000:.0f} ms, "
                f"statistics pass {stats_seconds * 1000:.0f} ms."
            ),
        )
    except Exception as exc:
        return CheckResult(
            key="performance", name="Image preparation speed",
            category=Category.PERFORMANCE.value, status=CheckStatus.UNKNOWN.value,
            measured="Could not be measured", requirement="", detail=str(exc),
        )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_system_check(paths=None, include_display: bool = True, include_performance: bool = True) -> SystemReport:
    """Run every check and return a report."""
    from ..config import Paths
    from ..core.models import utc_now

    paths = paths or Paths()
    report = SystemReport(generated_at=utc_now(), machine=machine_summary())

    report.add(check_python())
    report.add(check_qt())
    for r in check_dependencies():
        report.add(r)
    report.add(check_dicom_codecs())
    report.add(check_locale())

    report.add(check_cpu())
    report.add(check_memory())

    if include_display:
        for r in check_display():
            report.add(r)

    report.add(check_disk(paths.data_dir))
    for r in check_write_access(
        [("data", paths.data_dir), ("settings", paths.config_dir), ("logs", paths.log_dir)]
    ):
        report.add(r)
    report.add(check_sqlite(paths.data_dir))
    report.add(check_long_paths())

    for r in check_encryption(paths.key_file):
        report.add(r)

    if include_performance:
        report.add(check_performance())

    return report
