# PyInstaller specification for ARIA.
#
# One spec serves both platforms. Run it from the repository root:
#
#     pyinstaller packaging/aria.spec --noconfirm
#
# The build is a one folder build rather than a single file. A single file
# executable unpacks itself to a temporary folder on every launch, which adds
# seconds to startup and trips some endpoint protection software on a clinical
# workstation. A folder starts immediately and is straightforward to sign.

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

SPEC_DIR = Path(SPECPATH).resolve()
ROOT = SPEC_DIR.parent

IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"

APP_NAME = "ARIA"
VERSION = "1.0.0"

# Qt modules ARIA does not use. Excluding them removes roughly a third of the
# packaged size, which matters when the build is distributed to a site.
EXCLUDED_QT = [
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickWidgets",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets", "PySide6.Qt3DCore", "PySide6.Qt3DRender",
    "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning",
    "PySide6.QtLocation", "PySide6.QtSerialPort", "PySide6.QtSerialBus",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSensors",
    "PySide6.QtSpatialAudio", "PySide6.QtTextToSpeech", "PySide6.QtHelp",
    "PySide6.QtDesigner", "PySide6.QtUiTools", "PySide6.QtTest",
    "PySide6.QtSql", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
]

EXCLUDED_OTHER = [
    "tkinter", "matplotlib", "scipy", "pandas", "IPython", "jupyter",
    "notebook", "pytest", "setuptools", "pip", "PyQt5", "PyQt6", "wx",
    "sphinx", "docutils", "cv2", "torch", "tensorflow", "sklearn",
]

def _importable(name):
    """True when a module exists in the build environment.

    pydicom moved its pixel handling between major versions, so the candidates
    are probed rather than assumed. Naming a module that does not exist makes
    PyInstaller log an error for something that is simply not part of this
    installation, which buries the errors that matter.
    """
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


# pydicom resolves its pixel handlers by name at runtime, so they are named
# here. The set differs between pydicom 2 and pydicom 3, so both are probed.
PYDICOM_CANDIDATES = [
    # pydicom 3
    "pydicom.pixels",
    "pydicom.pixels.decoders",
    "pydicom.pixels.decoders.gdcm",
    "pydicom.pixels.decoders.pillow",
    "pydicom.pixels.decoders.pylibjpeg",
    "pydicom.pixels.decoders.rle",
    "pydicom.pixels.encoders",
    "pydicom.pixels.encoders.gdcm",
    "pydicom.pixels.encoders.native",
    "pydicom.pixels.encoders.pylibjpeg",
    # pydicom 2
    "pydicom.encoders.gdcm",
    "pydicom.encoders.native",
    "pydicom.encoders.pylibjpeg",
    "pydicom.pixel_data_handlers.numpy_handler",
    "pydicom.pixel_data_handlers.pillow_handler",
    "pydicom.pixel_data_handlers.rle_handler",
]

hidden = [name for name in PYDICOM_CANDIDATES if _importable(name)]

# The application loads its panels and dialogs lazily, so they are collected
# explicitly rather than discovered by following imports.
hidden += collect_submodules("aria")

datas = [
    (str(ROOT / "aria" / "resources"), "aria/resources"),
    (str(ROOT / "docs" / "USER_GUIDE.md"), "docs"),
    (str(ROOT / "docs" / "COLOUR_NOMENCLATURE.md"), "docs"),
    (str(ROOT / "docs" / "DATA_DICTIONARY.md"), "docs"),
    (str(ROOT / "LICENSE.txt"), "."),
]
datas = [(src, dst) for src, dst in datas if Path(src).exists()]

analysis = Analysis(
    [str(ROOT / "aria" / "__main__.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDED_QT + EXCLUDED_OTHER,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(analysis.pure)

icon_path = None
if IS_WINDOWS and (SPEC_DIR / "aria.ico").exists():
    icon_path = str(SPEC_DIR / "aria.ico")
elif IS_MACOS and (SPEC_DIR / "aria.icns").exists():
    icon_path = str(SPEC_DIR / "aria.icns")

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                 # UPX compression triggers false positives in
                               # endpoint protection, so it stays off.
    console=False,             # A windowed application; logs go to the log file.
    disable_windowed_traceback=False,
    argv_emulation=IS_MACOS,   # Lets a file dropped on the icon reach the app.
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path,
    version=str(SPEC_DIR / "version_info.txt")
    if IS_WINDOWS and (SPEC_DIR / "version_info.txt").exists()
    else None,
)

collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)

if IS_MACOS:
    app = BUNDLE(
        collection,
        name=f"{APP_NAME}.app",
        icon=icon_path,
        bundle_identifier="in.dicemed.aria",
        version=VERSION,
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": "ARIA",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
            "LSMinimumSystemVersion": "11.0",
            "LSApplicationCategoryType": "public.app-category.medical",
            "NSHumanReadableCopyright": "DiceMed",
            # ARIA reads files the user chooses and writes to its own data
            # folder. It does not use the camera, the microphone or location,
            # so no usage descriptions for those are declared.
            "CFBundleDocumentTypes": [
                {
                    "CFBundleTypeName": "DICOM image",
                    "CFBundleTypeRole": "Viewer",
                    "LSItemContentTypes": ["org.nema.dicom"],
                    "LSHandlerRank": "Alternate",
                },
                {
                    "CFBundleTypeName": "PNG image",
                    "CFBundleTypeRole": "Viewer",
                    "LSItemContentTypes": ["public.png"],
                    "LSHandlerRank": "Alternate",
                },
            ],
        },
    )
