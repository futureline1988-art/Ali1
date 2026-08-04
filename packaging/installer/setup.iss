; ==============================================================================
; Attendance Management System - Inno Setup script
; ==============================================================================
; Packages the PyInstaller onedir build (dist\AttendanceManagementSystem\,
; produced by packaging\pyinstaller\main.spec) into a single Setup.exe.
;
; Build with Inno Setup 6:
;   ISCC packaging\installer\setup.iss
;
; Run from the repository root (or adjust SourceRoot below).
; See BUILD_WINDOWS.md for the full build procedure.
; ==============================================================================

#define MyAppName "Attendance Management System"
#define MyAppVersion "1.1.0"
#define MyAppPublisher "Attendance Systems"
#define MyAppExeName "AttendanceManagementSystem.exe"
#define MyAppDataFolder "AttendanceManagementSystem"

; Project root; overridable at build time with /DSourceRoot=... if ISCC is
; invoked from somewhere other than packaging\installer\.
#ifndef SourceRoot
  #define SourceRoot "..\.."
#endif

; Where `pyinstaller main.spec` placed the onedir build.
#ifndef DistDir
  #define DistDir SourceRoot + "\dist\AttendanceManagementSystem"
#endif

[Setup]
AppId={{8F3B2C1A-6E4D-4A9B-9C7E-2D5F8A1B3C9E}}
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
OutputBaseFilename=Setup
SetupIconFile={#SourceRoot}\assets\icons\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=6.1sp1
; No LicenseFile is set: the project has no end-user license/EULA text of
; its own yet (only the bundled DejaVu font's license, which is unrelated).
; Add `LicenseFile=..\..\LICENSE.txt` here once a real EULA exists to show
; a license-acceptance page in the wizard.

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
// Requirement: "Create the required application data folders
// automatically" + "Preserve the database and license files during
// upgrades." The app itself creates %LOCALAPPDATA%\AttendanceManagementSystem
// and its subfolders on first launch (config.PathsConfig.ensure_created(),
// called from main.py's main()), so nothing here needs to pre-create them.
// What this section guarantees is the other half of that requirement: this
// installer NEVER touches %LOCALAPPDATA% on install, upgrade, or uninstall,
// so the database, license file, logs, and backups a user has accumulated
// always survive both an in-place upgrade (new files just overwrite the old
// ones under {app}, per-user data is untouched) and a full uninstall.

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
      if MsgBox('Do you want to also delete your attendance data, license, and logs (' + AppDataFolder + ')?' + #13#10 +
                'Choose "No" to keep them for a future reinstall.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      begin
        DelTree(AppDataFolder, True, True, True);
      end;
    end;
  end;
end;
