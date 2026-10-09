; Inno Setup script for DropTray. Build the app first (PyInstaller), then:
;   "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" installer\DropTray.iss
; Produces installer\Output\DropTraySetup.exe (a stable name, so releases/latest/download links keep working)
;
; Installs for the current user only (no administrator prompt). Settings and history live in
; %APPDATA%\DropTray and survive updates and uninstalling.

#define AppName "DropTray"
; release.ps1 passes /DAppVersion=<the version in core.py>
#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif
#define AppExe "DropTray.exe"

[Setup]
AppId={{6F3E2B1C-8D4A-4E5B-9C7F-2A1D3B4C5E6F}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=TechnoQuantum
AppPublisherURL=https://github.com/TechnoQuantum/droptray
AppSupportURL=https://github.com/TechnoQuantum/droptray/issues
DefaultDirName={localappdata}\Programs\{#AppName}
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=DropTraySetup
SetupIconFile=..\droptray.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.22000
CloseApplications=no

[Tasks]
Name: "startup"; Description: "Open DropTray when I sign in"; Flags: unchecked
Name: "firewall"; Description: "Let phones on my home network reach DropTray (asks for administrator)"

[Files]
Source: "..\dist\DropTray\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\setup-firewall.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\SHORTCUT.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Comment: "Receive files from your iPhone"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#AppName}"; \
    ValueData: """{app}\{#AppExe}"""; Tasks: startup; Flags: uninsdeletevalue

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\setup-firewall.ps1"""; \
    Flags: runhidden waituntilterminated; Tasks: firewall; StatusMsg: "Allowing DropTray through Windows Firewall..."
Filename: "{app}\{#AppExe}"; Description: "Start DropTray"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "taskkill.exe"; Parameters: "/F /IM {#AppExe}"; Flags: runhidden; RunOnceId: "StopDropTray"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
{ Stop a running copy so its files can be replaced; settings in %APPDATA% are untouched. }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  Exec('taskkill.exe', '/F /IM {#AppExe}', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(800);
  Result := '';
end;
