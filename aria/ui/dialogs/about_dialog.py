"""The about dialog, including the scope statement and the versions."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...version import APP_LONG_NAME, APP_NAME, APP_VENDOR, APP_VERSION, version_block
from ..icons import application_icon
from ..theme import PALETTE

SCOPE = (
    "ARIA is an annotation and research data tool for dental panoramic "
    "radiographs. It records landmarks, contours, regions, measurements, "
    "qualitative grades, provenance and quality flags, and exports them in "
    "machine readable form.\n\n"
    "It does not diagnose osteoporosis. It does not estimate bone mineral "
    "density. It does not recommend treatment. Where a study protocol uses a "
    "screening threshold, that threshold is project configuration, it is "
    "versioned, it is labelled as a screening rule wherever it appears, and it "
    "stays switched off until clinical approval is recorded."
)

PRIVACY = (
    "Everything runs on this workstation.\n\n"
    "ARIA has no network layer. It does not send images, metadata or "
    "annotations to any service, and it contains no model that runs remotely or "
    "locally. Geometry assists such as the cortical width construction are "
    "deterministic calculations from the contours you traced.\n\n"
    "Identifiers are removed or remapped at import according to an "
    "administrator approved profile, and every export is scanned for direct "
    "identifiers before it is written."
)

REFERENCES = [
    "Evaluation of Radiomorphometric Indices in Panoramic Radiograph, a screening tool",
    "Panoramic radiographs and quantitative ultrasound in postmenopausal women",
    "Morphological evaluation of gonial and antegonial regions in bruxers",
    "Assessment of Panoramic Radiomorphometric Indices in a Brazilian Population",
    "Computer Aided System of Mandibular Cortical Bone Porosity",
    "The DICOM Standard",
]


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setMinimumSize(620, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(14)
        logo = QLabel(self)
        logo.setPixmap(application_icon(72).pixmap(72, 72))
        header.addWidget(logo, 0, Qt.AlignTop)

        titles = QVBoxLayout()
        titles.setSpacing(3)
        name = QLabel(APP_NAME, self)
        name.setProperty("heading", True)
        long_name = QLabel(APP_LONG_NAME, self)
        long_name.setWordWrap(True)
        long_name.setProperty("dim", True)
        version = QLabel(f"Version {APP_VERSION}   {APP_VENDOR}", self)
        version.setProperty("dim", True)
        titles.addWidget(name)
        titles.addWidget(long_name)
        titles.addWidget(version)
        titles.addStretch(1)
        header.addLayout(titles, 1)
        layout.addLayout(header)

        tabs = QTabWidget(self)
        tabs.addTab(self._text_tab(SCOPE), "Scope")
        tabs.addTab(self._text_tab(PRIVACY), "Privacy")
        tabs.addTab(self._versions_tab(), "Versions")
        tabs.addTab(self._references_tab(), "References")
        layout.addWidget(tabs, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close, Qt.Horizontal, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _text_tab(self, text: str) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        label = QLabel(text, page)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignTop)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(label)
        layout.addStretch(1)
        return page

    def _versions_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)

        lines = []
        for key, value in version_block().items():
            lines.append(f"{key.replace('_', ' '):32s} {value}")
        lines.append("")

        import platform
        import sys

        lines.append(f"{'python':32s} {platform.python_version()}")
        lines.append(f"{'platform':32s} {platform.system()} {platform.release()}")
        try:
            from PySide6.QtCore import qVersion
            from PySide6 import __version__ as pyside_version

            lines.append(f"{'qt':32s} {qVersion()}")
            lines.append(f"{'pyside':32s} {pyside_version}")
        except ImportError:
            pass
        for module in ("numpy", "pydicom", "PIL", "cryptography", "psutil"):
            try:
                mod = __import__(module)
                lines.append(
                    f"{module:32s} {getattr(mod, '__version__', 'present')}"
                )
            except ImportError:
                lines.append(f"{module:32s} not installed")

        text = QTextEdit(page)
        text.setReadOnly(True)
        text.setProperty("mono", True)
        text.setPlainText("\n".join(lines))
        layout.addWidget(text)

        note = QLabel(
            "The calculation and schema versions are written into every export, "
            "so a measurement can always be traced to the rules that produced it.",
            page,
        )
        note.setWordWrap(True)
        note.setProperty("dim", True)
        layout.addWidget(note)
        return page

    def _references_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        heading = QLabel(
            "The annotation targets and measurement geometry follow the published "
            "descriptions of the mandibular radiomorphometric indices:",
            page,
        )
        heading.setWordWrap(True)
        layout.addWidget(heading)
        for reference in REFERENCES:
            item = QLabel(f"•  {reference}", page)
            item.setWordWrap(True)
            item.setProperty("dim", True)
            layout.addWidget(item)
        layout.addStretch(1)
        return page
