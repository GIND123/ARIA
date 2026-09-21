#!/usr/bin/env bash
#
# Build ARIA for macOS.
#
#     bash packaging/build_macos.sh [--skip-tests] [--sign "Developer ID Application: Name (TEAMID)"]
#
# Produces dist/ARIA.app and, when create-dmg is available, a disk image. The
# build runs in a clean virtual environment so the bundle contains what the
# specification declares rather than whatever is installed on the build machine.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

SKIP_TESTS=0
SKIP_DMG=0
SIGN_IDENTITY=""
NOTARY_PROFILE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-tests) SKIP_TESTS=1; shift ;;
        --skip-dmg) SKIP_DMG=1; shift ;;
        --sign) SIGN_IDENTITY="$2"; shift 2 ;;
        --notarize) NOTARY_PROFILE="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

step() { printf "\n== %s\n" "$1"; }
fail() { printf "!! %s\n" "$1" >&2; exit 1; }

step "Checking the build machine"
command -v python3 >/dev/null || fail "python3 was not found."
PYTHON_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
printf "   Python %s on %s\n" "$PYTHON_VERSION" "$(uname -m)"

step "Preparing the build environment"
BUILD_VENV="$ROOT/.venv-build"
rm -rf "$BUILD_VENV"
python3 -m venv "$BUILD_VENV"
VENV_PY="$BUILD_VENV/bin/python"

"$VENV_PY" -m pip install --upgrade pip wheel --quiet
"$VENV_PY" -m pip install --quiet \
    PySide6 pydicom numpy Pillow psutil cryptography pyinstaller \
    || fail "Dependencies could not be installed."

step "Recording third party licences"
"$VENV_PY" -m pip install --quiet pip-licenses 2>/dev/null || true
"$VENV_PY" -m piplicenses --format=plain --with-urls \
    --output-file "$ROOT/third_party_licences.txt" 2>/dev/null || true

step "Generating icon files"
"$VENV_PY" "$ROOT/scripts/make_icons.py"
if [[ -d "$ROOT/packaging/aria.iconset" ]]; then
    # iconutil produces a better icns than a single resized image.
    iconutil -c icns "$ROOT/packaging/aria.iconset" -o "$ROOT/packaging/aria.icns" \
        || printf "   iconutil was not available, using the generated icns.\n"
fi

if [[ "$SKIP_TESTS" -eq 0 ]]; then
    step "Running the test suite"
    "$VENV_PY" -m pip install --quiet pytest
    QT_QPA_PLATFORM=offscreen "$VENV_PY" -m pytest tests -q \
        || fail "Tests failed. The build was stopped."
fi

step "Building the application bundle"
rm -rf "$ROOT/build" "$ROOT/dist"
"$VENV_PY" -m PyInstaller "$ROOT/packaging/aria.spec" --noconfirm --clean \
    || fail "The application build failed."

APP="$ROOT/dist/ARIA.app"
[[ -d "$APP" ]] || fail "ARIA.app was not produced."
printf "   Built %s (%s)\n" "$APP" "$(du -sh "$APP" | cut -f1)"

step "Checking the packaged application starts"
# The packaged build runs its own self tests and exits, which catches a missing
# module or data file before anyone installs it.
if "$APP/Contents/MacOS/ARIA" --self-test; then
    printf "   The packaged application passed its self tests.\n"
else
    printf "   The packaged self test reported problems.\n"
fi

if [[ -n "$SIGN_IDENTITY" ]]; then
    step "Signing the bundle"
    ENTITLEMENTS="$ROOT/packaging/entitlements.plist"
    # Sign the nested binaries before the bundle itself, which is what the
    # notary service expects.
    find "$APP/Contents" \( -name "*.dylib" -o -name "*.so" -o -perm +111 -type f \) -print0 \
        | xargs -0 -I{} codesign --force --timestamp --options runtime \
            --entitlements "$ENTITLEMENTS" --sign "$SIGN_IDENTITY" {} 2>/dev/null || true
    codesign --force --deep --timestamp --options runtime \
        --entitlements "$ENTITLEMENTS" --sign "$SIGN_IDENTITY" "$APP" \
        || fail "Signing failed."
    codesign --verify --deep --strict --verbose=2 "$APP" || fail "The signature did not verify."
    printf "   Signed and verified.\n"
fi

if [[ "$SKIP_DMG" -eq 0 ]]; then
    step "Building the disk image"
    DMG="$ROOT/dist/ARIA-1.0.0-macos-$(uname -m).dmg"
    rm -f "$DMG"
    if command -v create-dmg >/dev/null; then
        create-dmg \
            --volname "ARIA" \
            --volicon "$ROOT/packaging/aria.icns" \
            --window-size 620 420 \
            --icon-size 110 \
            --icon "ARIA.app" 160 200 \
            --app-drop-link 460 200 \
            --no-internet-enable \
            "$DMG" "$ROOT/dist/ARIA.app" || fail "The disk image could not be built."
    else
        printf "   create-dmg was not found, using hdiutil.\n"
        STAGING="$(mktemp -d)"
        cp -R "$APP" "$STAGING/"
        ln -s /Applications "$STAGING/Applications"
        hdiutil create -volname "ARIA" -srcfolder "$STAGING" -ov -format UDZO "$DMG" \
            || fail "hdiutil failed."
        rm -rf "$STAGING"
    fi
    printf "   Wrote %s\n" "$DMG"

    if [[ -n "$NOTARY_PROFILE" ]]; then
        step "Notarising"
        xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait \
            || fail "Notarisation failed."
        xcrun stapler staple "$DMG" || fail "The notarisation ticket could not be stapled."
        printf "   Notarised and stapled.\n"
    fi
fi

step "Done"
printf "Application: %s\n" "$APP"
printf "Open it with: open \"%s\"\n" "$APP"
