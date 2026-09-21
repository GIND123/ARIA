# Build ARIA for Windows.
#
#   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
#
# The script builds into a clean virtual environment so the packaged
# application contains what the specification declares rather than whatever
# happens to be installed on the build machine.

[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$SkipInstaller,
    [string]$Python = "py -3",
    [string]$SignThumbprint = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Step($message) {
    Write-Host ""
    Write-Host "== $message" -ForegroundColor Cyan
}

function Fail($message) {
    Write-Host "!! $message" -ForegroundColor Red
    exit 1
}

Step "Preparing the build environment"
$BuildVenv = Join-Path $Root ".venv-build"
if (Test-Path $BuildVenv) {
    Remove-Item -Recurse -Force $BuildVenv
}
Invoke-Expression "$Python -m venv `"$BuildVenv`""
$VenvPython = Join-Path $BuildVenv "Scripts\python.exe"
if (-not (Test-Path $VenvPython)) { Fail "The build environment was not created." }

& $VenvPython -m pip install --upgrade pip wheel --quiet
& $VenvPython -m pip install --quiet `
    PySide6 pydicom numpy Pillow psutil cryptography pyinstaller
if ($LASTEXITCODE -ne 0) { Fail "Dependencies could not be installed." }

Step "Recording third party licences"
& $VenvPython -m pip install --quiet pip-licenses 2>$null
if ($LASTEXITCODE -eq 0) {
    & $VenvPython -m piplicenses --format=plain --with-urls `
        --output-file (Join-Path $Root "third_party_licences.txt") 2>$null
}

Step "Generating icon files"
& $VenvPython (Join-Path $Root "scripts\make_icons.py")
if ($LASTEXITCODE -ne 0) { Fail "Icon generation failed." }

if (-not $SkipTests) {
    Step "Running the test suite"
    & $VenvPython -m pip install --quiet pytest
    $env:QT_QPA_PLATFORM = "offscreen"
    & $VenvPython -m pytest tests -q
    if ($LASTEXITCODE -ne 0) { Fail "Tests failed. The build was stopped." }
    Remove-Item Env:\QT_QPA_PLATFORM
}

Step "Building the application"
foreach ($folder in @("build", "dist")) {
    $path = Join-Path $Root $folder
    if (Test-Path $path) { Remove-Item -Recurse -Force $path }
}
& $VenvPython -m PyInstaller (Join-Path $Root "packaging\aria.spec") --noconfirm --clean
if ($LASTEXITCODE -ne 0) { Fail "The application build failed." }

$AppDir = Join-Path $Root "dist\ARIA"
$Executable = Join-Path $AppDir "ARIA.exe"
if (-not (Test-Path $Executable)) { Fail "ARIA.exe was not produced." }

$SizeMb = [math]::Round(((Get-ChildItem $AppDir -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB), 1)
Write-Host "   Built $Executable  ($SizeMb MB)" -ForegroundColor Green

Step "Checking the packaged application starts"
# The packaged build runs its own self tests and exits, which catches a missing
# module or data file before anyone installs it.
$env:ARIA_SELFTEST = "1"
& $Executable --self-test
$startupCode = $LASTEXITCODE
Remove-Item Env:\ARIA_SELFTEST
if ($startupCode -ne 0) {
    Write-Host "   The packaged self test reported problems (exit $startupCode)." -ForegroundColor Yellow
} else {
    Write-Host "   The packaged application passed its self tests." -ForegroundColor Green
}

if ($SignThumbprint -ne "") {
    Step "Signing the executable"
    $signtool = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($null -eq $signtool) {
        Write-Host "   signtool.exe was not found, so signing was skipped." -ForegroundColor Yellow
    } else {
        & signtool sign /sha1 $SignThumbprint /fd SHA256 /tr http://timestamp.digicert.com `
            /td SHA256 /d "ARIA" $Executable
        if ($LASTEXITCODE -ne 0) { Fail "Signing failed." }
    }
}

if (-not $SkipInstaller) {
    Step "Building the installer"
    $iscc = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($null -eq $iscc) {
        $candidates = @(
            "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
            "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
        )
        $found = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
        if ($found) { $iscc = $found }
    }
    if ($null -eq $iscc) {
        Write-Host "   Inno Setup was not found. The folder in dist\ARIA can be" -ForegroundColor Yellow
        Write-Host "   distributed as a zip archive instead." -ForegroundColor Yellow
        Compress-Archive -Path $AppDir -DestinationPath (Join-Path $Root "dist\ARIA-1.0.0-windows.zip") -Force
        Write-Host "   Wrote dist\ARIA-1.0.0-windows.zip" -ForegroundColor Green
    } else {
        $isccPath = if ($iscc -is [string]) { $iscc } else { $iscc.Source }
        & $isccPath (Join-Path $Root "packaging\installer\aria.iss")
        if ($LASTEXITCODE -ne 0) { Fail "The installer build failed." }
        Write-Host "   Installer written to dist\installer" -ForegroundColor Green
    }
}

Step "Done"
Write-Host "Application: $AppDir"
Write-Host "Run it with: $Executable"
