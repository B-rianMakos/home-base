; Inno Setup script for the Home Base installer (PC-2, PC-3, PC-5, PC-9, FR-5; WO-FS-011 HB-2, HB-3).
; Built by tools/build_release.py after PyInstaller. Unsigned: SmartScreen shows "More info -> Run anyway".
;
; WO-FS-011: the app was called Family Scheduler up to 1.8.0. The AppId and the data folder
; (C:\ProgramData\FamilyScheduler) stay the same, so 1.9.0 installs over it in place with nothing lost. An upgrade
; removes the old program files and shortcuts, and moves the hub's startup task and firewall rule to the new names.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef DistDir
  #define DistDir "..\dist"
#endif
#define OldTask "Family Scheduler Hub"
#define Task "Home Base Hub"

[Setup]
AppId={{6F3C2A8E-5B1D-4C7A-9E2F-FA11C5CEDB01}
AppName=Home Base
AppVersion={#AppVersion}
AppPublisher=Home Base
; A new install goes to "Home Base"; an upgrade keeps the folder it was installed in (UsePreviousAppDir).
DefaultDirName={autopf}\Home Base
DefaultGroupName=Home Base
; An upgrade uses the new Start menu folder, not the old "Family Scheduler" one (removed in [InstallDelete]).
UsePreviousGroup=no
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#DistDir}
OutputBaseFilename=HomeBaseSetup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\HomeBase.exe
UninstallDisplayName=Home Base
SetupIconFile=app.ico
LicenseFile=..\LICENSE
InfoBeforeFile=install-notes.txt

[InstallDelete]
; HB-3: no leftovers from Family Scheduler 1.8.0 and earlier.
Type: files; Name: "{app}\FamilyScheduler.exe"
Type: files; Name: "{app}\FamilySchedulerHub.exe"
Type: files; Name: "{autodesktop}\Family Scheduler.lnk"
Type: files; Name: "{userdesktop}\Family Scheduler.lnk"
Type: filesandordirs; Name: "{autoprograms}\Family Scheduler"
Type: files; Name: "{autoprograms}\Family Scheduler.lnk"

[Files]
Source: "{#DistDir}\HomeBase\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD-PARTY-LICENSES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "hub-task.xml"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: IsHub

[Dirs]
; HB-4: the data folder keeps its name, so nothing moves and nothing is lost.
Name: "{commonappdata}\FamilyScheduler"
Name: "{commonappdata}\FamilyScheduler\updates"; Check: IsHub

[INI]
Filename: "{commonappdata}\FamilyScheduler\config.ini"; Section: "setup"; Key: "role"; String: "{code:GetRole}"

[Icons]
Name: "{group}\Home Base"; Filename: "{app}\HomeBase.exe"
Name: "{autodesktop}\Home Base"; Filename: "{app}\HomeBase.exe"

[Run]
; WO-FS-008 PS-2: a one-time move of the test hub's data into this hub. Off unless ticked, so an upgrade never
; replaces live data; the hub's earlier data is backed up first, and the hub shows what was moved.
Filename: "{app}\HomeBaseHub.exe"; Parameters: "import ""{code:TestHubFolder}"""; Flags: postinstall unchecked waituntilterminated runascurrentuser; Description: "Bring in the data from the test hub ({code:TestHubFolder}). This replaces this hub's data; a backup is kept."; Check: HasTestHub
Filename: "{app}\HomeBase.exe"; Description: "Open Home Base"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "schtasks.exe"; Parameters: "/End /TN ""{#Task}"""; Flags: runhidden; RunOnceId: "EndHub"
Filename: "schtasks.exe"; Parameters: "/Delete /TN ""{#Task}"" /F"; Flags: runhidden; RunOnceId: "DeleteHub"
Filename: "netsh.exe"; Parameters: "advfirewall firewall delete rule name=""{#Task}"""; Flags: runhidden; RunOnceId: "DeleteRule"
Filename: "schtasks.exe"; Parameters: "/Delete /TN ""{#OldTask}"" /F"; Flags: runhidden; RunOnceId: "DeleteOldHub"
Filename: "netsh.exe"; Parameters: "advfirewall firewall delete rule name=""{#OldTask}"""; Flags: runhidden; RunOnceId: "DeleteOldRule"

[Code]
var
  RolePage: TInputOptionWizardPage;

procedure InitializeWizard;
var
  Role: String;
begin
  RolePage := CreateInputOptionPage(wpSelectDir,
    'Home hub', 'Which PC is this?',
    'One PC in the house holds the family''s data. It must stay on. Every other PC connects to it over the home Wi-Fi.',
    True, False);
  RolePage.Add('This PC is the home hub');
  RolePage.Add('Connect to the home hub');
  Role := GetIniString('setup', 'role', 'client', ExpandConstant('{commonappdata}\FamilyScheduler\config.ini'));
  if Role = 'hub' then
    RolePage.SelectedValueIndex := 0
  else
    RolePage.SelectedValueIndex := 1;
end;

function IsHub: Boolean;
begin
  Result := RolePage.SelectedValueIndex = 0;
end;

function TestHubFolder(Param: String): String;
begin
  Result := 'C:\FamilySchedulerTest';
end;

function HasTestHub: Boolean;
begin
  Result := IsHub and FileExists(TestHubFolder('') + '\family.db');
end;

function GetRole(Param: String): String;
begin
  if IsHub then Result := 'hub' else Result := 'client';
end;

procedure RemoveTaskAndRule(Name: String);
var
  Code: Integer;
begin
  Exec('schtasks.exe', '/End /TN "' + Name + '"', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec('schtasks.exe', '/Delete /TN "' + Name + '" /F', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec('netsh.exe', 'advfirewall firewall delete rule name="' + Name + '"', '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  { Upgrading: stop a running hub, by its old or new name, so its files can be replaced (HB-3). }
  Exec('schtasks.exe', '/End /TN "{#Task}"', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec('schtasks.exe', '/End /TN "{#OldTask}"', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec('taskkill.exe', '/F /IM HomeBaseHub.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec('taskkill.exe', '/F /IM FamilySchedulerHub.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec('taskkill.exe', '/F /IM FamilyScheduler.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := '';
end;

procedure SetUpHub;
var
  Xml: AnsiString;
  XmlText: String;
  Code: Integer;
  TaskFile: String;
begin
  { WO-FS-008: the hub's key file is sealed to this PC; only SYSTEM (the hub) and administrators may read it. }
  ForceDirectories(ExpandConstant('{commonappdata}\FamilyScheduler\keys'));
  Exec('icacls.exe', '"' + ExpandConstant('{commonappdata}\FamilyScheduler\keys') +
       '" /inheritance:r /grant:r *S-1-5-18:(OI)(CI)F *S-1-5-32-544:(OI)(CI)F', '', SW_HIDE,
       ewWaitUntilTerminated, Code);
  { HB-3: the old startup task and firewall rule pointed at FamilySchedulerHub.exe; replace them. }
  RemoveTaskAndRule('{#OldTask}');
  TaskFile := ExpandConstant('{tmp}\hub-task.xml');
  LoadStringFromFile(TaskFile, Xml);
  XmlText := String(Xml);
  StringChangeEx(XmlText, '__HUB_EXE__', ExpandConstant('{app}\HomeBaseHub.exe'), True);
  SaveStringToFile(TaskFile, XmlText, False);
  Exec('schtasks.exe', '/Create /TN "{#Task}" /XML "' + TaskFile + '" /F', '', SW_HIDE,
       ewWaitUntilTerminated, Code);
  Exec('netsh.exe', 'advfirewall firewall delete rule name="{#Task}"', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec('netsh.exe', 'advfirewall firewall add rule name="{#Task}" dir=in action=allow program="'
       + ExpandConstant('{app}\HomeBaseHub.exe') + '" enable=yes profile=private', '', SW_HIDE,
       ewWaitUntilTerminated, Code);
  { PC-9: the hub serves this installer to the other PCs. }
  CopyFile(ExpandConstant('{srcexe}'),
           ExpandConstant('{commonappdata}\FamilyScheduler\updates\') + ExtractFileName(ExpandConstant('{srcexe}')),
           False);
  Exec('schtasks.exe', '/Run /TN "{#Task}"', '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

procedure RemoveHub;
begin
  { WO-FS-008 MH-1: a former hub PC re-installed as "Connect to the home hub" stops being a hub. Its data folder is
    left in place as a backup. Both the old and the new task names go. }
  RemoveTaskAndRule('{#Task}');
  RemoveTaskAndRule('{#OldTask}');
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    if IsHub then
      SetUpHub
    else
      RemoveHub;
end;
