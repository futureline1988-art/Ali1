; ==============================================================================
; Developer Suite - Inno Setup script
; ==============================================================================
; Packages the PyInstaller onedir build (dist\DeveloperSuite\, produced by
; packaging\pyinstaller\developer_suite.spec) into a single
; DeveloperSuite-Setup.exe.
;
; Mirrors packaging\installer\setup.iss (the Attendance Client's own
; installer script) deliberately -- same structure, same rationale for
; each setting -- pointed at this application's own build output, data
; folder, and a distinct AppId so both applications can be installed
; side by side on the same vendor machine without conflicting.
;
; Build with Inno Setup 6:
;   ISCC packaging\installer\setup_developer_suite.iss
;
; Run from the repository root (or adjust SourceRoot below).
; ==============================================================================

#define MyAppName "Developer Suite"
#define MyAppVersion "1.1.3"
#define MyAppPublisher "Attendance Systems"
#define MyAppExeName "DeveloperSuite.exe"
#define MyAppDataFolder "DeveloperSuite"

; Project root; overridable at build time with /DSourceRoot=... if ISCC is
; invoked from somewhere other than packaging\installer\.
#ifndef SourceRoot
  #define SourceRoot "..\.."
#endif

; Where `pyinstaller developer_suite.spec` placed the onedir build.
#ifndef DistDir
  #define DistDir SourceRoot + "\dist\DeveloperSuite"
#endif

[Setup]
; Distinct from the Attendance Client's AppId (setup.iss's
; {{8F3B2C1A-6E4D-4A9B-9C7E-2D5F8A1B3C9E}}) -- a different, independently
; versioned application, so it must never be treated as an upgrade of
; (or be upgraded by) that installer.
AppId={{C7C78EAB-C948-4344-915B-ACBD871BB8B7}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppVerName={#MyAppName} {#MyAppVersion}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Standard-user install by default; the app itself never needs elevation
; since it only ever writes to %LOCALAPPDATA%. Installing to Program Files
; still needs admin once, at install time, which Inno Setup requests
; automatically because DefaultDirName resolves under {autopf}.
PrivilegesRequired=admin
OutputDir={#SourceRoot}\Release
; Not "Setup" -- this installer's output is published alongside the
; Attendance Client's own Release\Setup.exe in the *same* GitHub
; Release (see windows-release.yml's build-developer-suite job), so it
; needs a distinct filename both to avoid a release-asset name
; collision and so a user browsing the release's file list can tell
; the two installers apart at a glance.
OutputBaseFilename=DeveloperSuite-Setup
SetupIconFile={#SourceRoot}\assets\icons\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=6.1sp1
; No LicenseFile is set: the project has no end-user license/EULA text of
; its own yet. Add `LicenseFile=..\..\LICENSE.txt` here once a real EULA
; exists to show a license-acceptance page in the wizard.

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "arabic"; MessagesFile: "compiler:Languages\Arabic.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; The entire onedir output (exe + _internal\ with all DLLs, Qt plugins,
; and bundled assets) copied as-is; nothing here should ever need to
; enumerate individual files.
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
// Same requirement as the Attendance Client's installer (see
// setup.iss's identical [Code] section): this installer never touches
// %LOCALAPPDATA% on install, upgrade, or uninstall, so the database,
// signing keys, and logs a vendor operator has accumulated always
// survive both an in-place upgrade and a full uninstall unless they
// explicitly opt in to deleting them below.

function GetAppDataFolder(): String;
begin
  Result := ExpandConstant('{localappdata}\{#MyAppDataFolder}');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  AppDataFolder: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    AppDataFolder := GetAppDataFolder();
    if DirExists(AppDataFolder) then
    begin
      if MsgBox('Do you want to also delete your Developer Suite data, signing keys, and logs (' + AppDataFolder + ')?' + #13#10 +
                'Choose "No" to keep them for a future reinstall.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      begin
        DelTree(AppDataFolder, True, True, True);
      end;
    end;
  end;
end;
