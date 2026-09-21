# Packaging ARIA

Written for: whoever produces the installers.

## What is produced

| Platform | Output | Notes |
| --- | --- | --- |
| Windows | `dist\ARIA\` and `dist\installer\ARIA-1.0.0-windows-setup.exe` | Inno Setup 6, per machine install |
| macOS | `dist/ARIA.app` and `dist/ARIA-1.0.0-macos-<arch>.dmg` | Signed and notarised when credentials are supplied |

Both are **one folder** builds rather than one file executables. A single file
executable unpacks itself to a temporary folder on every launch, which adds
seconds to startup and trips endpoint protection software on a clinical
workstation. A folder starts immediately and is straightforward to sign.

## Before building

Have these on the build machine:

* Python 3.10 or later
* Windows: [Inno Setup 6](https://jrsoftware.org/isinfo.php) for the installer,
  and the Windows SDK if you are signing
* macOS: Xcode command line tools, and optionally `create-dmg`

The build scripts create their own virtual environment and install
dependencies, so nothing else needs to be installed globally.

## Building

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1 -SignThumbprint <thumbprint>
```

```bash
# macOS
bash packaging/build_macos.sh
bash packaging/build_macos.sh --sign "Developer ID Application: Name (TEAMID)" --notarize aria-notary
```

Useful flags: `--skip-tests` / `-SkipTests` while iterating,
`--skip-dmg` / `-SkipInstaller` to stop after the application.

## What the scripts do, in order

1. Create a clean virtual environment and install the declared dependencies, so
   the packaged application contains what the specification declares rather than
   whatever happens to be installed on the build machine.
2. Record third party licences to `third_party_licences.txt`.
3. Generate the icon files from the application's own vector icon, so the icon
   in the taskbar, the installer and the application are the same drawing.
4. Run the test suite. **A failure stops the build.**
5. Run PyInstaller against `packaging/aria.spec`.
6. Run the packaged binary's own `--self-test`, which catches a missing module
   or data file before anyone installs it.
7. Sign, if credentials were supplied.
8. Build the installer or disk image.

## The specification

`packaging/aria.spec` serves both platforms.

**Exclusions matter.** Roughly a third of the packaged size is Qt modules ARIA
does not use: QML, Quick, WebEngine, 3D, Charts, Multimedia and the rest. They
are listed explicitly rather than trimmed afterwards, so the exclusion survives
a rebuild.

**pydicom handlers are probed, not assumed.** pydicom moved its pixel handling
between major versions. The spec checks which module names exist in the build
environment and names only those, so the build log does not fill with errors
about modules that are simply not part of this installation.

**UPX is off.** UPX compressed executables are a common false positive for
endpoint protection, which is not a fight worth having on a clinical
workstation.

## Signing

**Windows.** Pass `-SignThumbprint` with the thumbprint of a code signing
certificate in the current user's certificate store. The script uses
`signtool` with SHA-256 and a timestamp.

**macOS.** Pass `--sign` with a Developer ID Application identity. Nested
binaries are signed before the bundle, which is what the notary service
expects. `packaging/entitlements.plist` requests the three hardened runtime
exceptions a packaged Python application needs, plus read and write access to
files the user selects. It requests **no network access**, client or server,
which is the strongest statement the entitlements can make about an application
that has no network layer.

Add `--notarize` with a `notarytool` keychain profile to submit and staple.

## Verifying a build

```
dist\ARIA\ARIA.exe --self-test      # 19 self tests against the packaged code
dist\ARIA\ARIA.exe --system-check   # the workstation check
dist\ARIA\ARIA.exe --version        # every version identifier
```

On macOS the same flags work against `dist/ARIA.app/Contents/MacOS/ARIA`.

Check the platform plugin shipped, since its absence is the usual cause of a
packaged Qt application failing to open a window:

```
dir dist\ARIA\_internal\PySide6\plugins\platforms
```

`qwindows.dll` must be present on Windows, `libqcocoa.dylib` on macOS.

## Installer behaviour

The Windows installer installs per machine by default, so several accounts on a
shared annotation workstation get the same application. Each account keeps its
own data folder, so annotations are never shared between accounts by accident.

**Uninstalling does not remove annotation data.** Data lives in the user's own
application data folder. Uninstalling an application must not destroy a study.

## Sizes

A Windows build is around 174 MB unpacked and around 60 MB as an installer. Most
of that is Qt and numpy. It fits comfortably on any workstation that meets the
stated requirements.
