; Inno Setup script for ARIA.
;
; Build with:  ISCC.exe packaging\installer\aria.iss
;
; Installs per machine by default so several accounts on a shared annotation
; workstation get the same application. Each account keeps its own data folder,
; so annotations are never shared between accounts by accident.

#define AppName       "ARIA"
#define AppLongName   "ARIA: Anatomy aware Radiomorphometric Index Annotator"
#define AppVersion    "1.0.0"
#define AppPublisher  "DiceMed"
#define AppExe        "ARIA.exe"

[Setup]
AppId={{7D2A4E1C-9B3F-4A62-8E51-1C5A9F0D3B77}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion={#AppVersion}
VersionInfoDescription={#AppLongName}

DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=no
AllowNoIcons=yes

OutputDir=..\..\dist\installer
OutputBaseFilename=ARIA-{#AppVersion}-windows-setup
SetupIconFile=..\aria.ico
UninstallDisplayIcon={app}\{#AppExe}
WizardStyle=modern

Compression=lzma2/max
SolidCompression=yes

; A 64 bit application on a 64 bit system.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; Windows 10 is the oldest supported release.
MinVersion=10.0.17763

PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
LicenseFile=..\..\LICENSE.txt

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; \
    GroupDescription: "Shortcuts"; Flags: unchecked

[Files]
Source: "..\..\dist\ARIA\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\docs\USER_GUIDE.md"; DestDir: "{app}\docs"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\..\docs\COLOUR_NOMENCLATURE.md"; DestDir: "{app}\docs"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\..\docs\DATA_DICTIONARY.md"; DestDir: "{app}\docs"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\..\third_party_licences.txt"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; Comment: "{#AppLongName}"
Name: "{group}\User guide"; Filename: "{app}\docs\USER_GUIDE.md"; \
    Flags: createonlyiffileexists
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Remove only what the installer wrote. Annotation data lives in the user's
; own data folder and is deliberately left in place, because uninstalling an
; application must not destroy a study.
Type: filesandordirs; Name: "{app}\_internal"

[Messages]
FinishedLabel=ARIA is installed.%n%nAnnotation data is stored in each user's own application data folder and is not removed when ARIA is uninstalled.

[Code]
function InitializeSetup(): Boolean;
var
  Version: TWindowsVersion;
begin
  GetWindowsVersionEx(Version);
  if Version.Major < 10 then
  begin
    MsgBox('ARIA needs Windows 10 or later.', mbCriticalError, MB_OK);
    Result := False;
    exit;
  end;
  Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    { The first launch runs a compatibility check, so nothing is verified here
      beyond the operating system version. }
  end;
end;
