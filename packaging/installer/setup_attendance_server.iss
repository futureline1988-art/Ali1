; ==============================================================================
; Attendance Server - Inno Setup script
; ==============================================================================
; Packages the PyInstaller onedir build (dist\AttendanceServer\, produced by
; packaging\pyinstaller\attendance_server.spec) into a single
; AttendanceServer-Setup.exe.
;
; Mirrors packaging\installer\setup_developer_suite.iss deliberately --
; same structure, same rationale for each setting -- pointed at this
; application's own build output, data folder, and a distinct AppId so
; all three applications (Attendance Client, Developer Suite,
; Attendance Server) can be installed side by side on the same machine
; without conflicting. Unlike the other two, the [Run] entry below
; launches a console application, not a windowed one -- the operator
; sees a console window with the server's log output, the same
; behavior `python -m server.main` already has in development.
;
; Build with Inno Setup 6:
;   ISCC packaging\installer\setup_attendance_server.iss
;
; Run from the repository root (or adjust SourceRoot below).
; ==============================================================================

#define MyAppName "Attendance Server"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Attendance Systems"
#define MyAppExeName "AttendanceServer.exe"
#define MyAppDataFolder "AttendanceServer"

; Project root; overridable at build time with /DSourceRoot=... if ISCC is
; invoked from somewhere other than packaging\installer\.
#ifndef SourceRoot
  #define SourceRoot "..\.."
#endif

; Where `pyinstaller attendance_server.spec` placed the onedir build.
#ifndef DistDir
  #define DistDir SourceRoot + "\dist\AttendanceServer"
#endif

[Setup]
; Distinct from the Attendance Client's AppId
; ({{8F3B2C1A-6E4D-4A9B-9C7E-2D5F8A1B3C9E}}) and the Developer Suite's
; ({{C7C78EAB-C948-4344-915B-ACBD871BB8B7}}) -- a different,
; independently versioned application, so it must never be treated as
; an upgrade of (or be upgraded by) either of those installers.
AppId={{11D9B539-3E5C-4E93-A378-500C67541C71}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppVerName={#MyAppName} {#MyAppVersion}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Standard-user install by default; the server itself never needs
; elevation since it only ever writes to %LOCALAPPDATA% and binds to
; an unprivileged port (8000 by default). Installing to Program Files
; still needs admin once, at install time, which Inno Setup requests
; automatically because DefaultDirName resolves under {autopf}.
PrivilegesRequired=admin
OutputDir={#SourceRoot}\Release
OutputBaseFilename=AttendanceServer-Setup
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
; The entire onedir output (exe + _internal\ with all DLLs and data
; files) copied as-is; nothing here should ever need to enumerate
; individual files.
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Console app: this launches a visible console window showing the
; server's own startup log lines, exactly like running
; `python -m server.main` from a terminal -- nowait/postinstall/
; skipifsilent mirror the other two installers' "offer to launch,
; never block the wizard on it" convenience.
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
// Same requirement as the other two installers (see setup.iss's and
// setup_developer_suite.iss's identical [Code] sections): this
// installer never touches %LOCALAPPDATA% on install, upgrade, or
// uninstall, so the database and logs an operator has accumulated
// always survive both an in-place upgrade and a full uninstall unless
// they explicitly opt in to deleting them below.

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
      if MsgBox('Do you want to also delete the Attendance Server''s database and logs (' + AppDataFolder + ')?' + #13#10 +
                'Choose "No" to keep them for a future reinstall.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      begin
        DelTree(AppDataFolder, True, True, True);
      end;
    end;
  end;
end;
