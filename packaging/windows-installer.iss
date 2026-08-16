; Build with: ISCC.exe /DMyAppVersion=0.2.0 packaging\windows-installer.iss
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif

#define MyAppName "Family Finance"
#define MyAppPublisher "Family Finance"
#define MyAppExeName "StatementPipeline.exe"

[Setup]
AppId={{8A60CB52-8D5D-46B7-8B49-347C392129A0}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
SetupIconFile=assets\family-finance.ico
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\installer
OutputBaseFilename=StatementPipelineSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
; Older in-app updaters may start Setup while StatementPipeline.exe is still
; alive. Force-close it so file replace and relaunch can succeed.
CloseApplications=force
CloseApplicationsFilter={#MyAppExeName}
RestartApplications=no

[Files]
Source: "..\dist\StatementPipeline\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

; Relaunch once after install. Dual [Run] entries previously launched two hosts
; and the second hit the single-instance lock ("already running").
[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifdoesntexist

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  { Belt-and-suspenders for hung UI processes that ignore Restart Manager. }
  Exec('taskkill.exe', '/F /IM {#MyAppExeName}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  MsgBox(
    'Family Finance keeps your financial data in %LOCALAPPDATA%\Statement Pipeline. Uninstalling does not remove it. Delete that folder manually only if you want to permanently remove your data.',
    mbInformation,
    MB_OK
  );
  Result := True;
end;
