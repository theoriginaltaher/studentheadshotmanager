#define AppVersion "1.1.0"
#define AppSourceDir GetEnv("SHM_APP_SOURCE_DIR")
#if AppSourceDir == ""
  #define AppSourceDir "..\dist\StudentHeadshotManager"
#endif
[Setup]
AppId={{F58BDCC2-2C40-4F93-BB09-F6CEBB681A67}
AppName=Student Headshot Manager
AppVersion={#AppVersion}
AppPublisher=Taher Hussain
AppPublisherURL=https://www.linkedin.com/company/taherhussaincreations/
AppSupportURL=https://www.linkedin.com/company/taherhussaincreations/
AppUpdatesURL=https://www.linkedin.com/company/taherhussaincreations/
DefaultDirName={autopf}\Student Headshot Manager
DefaultGroupName=Student Headshot Manager
DisableProgramGroupPage=yes
OutputDir=..\release
OutputBaseFilename=StudentHeadshotManager-Setup
SetupIconFile=..\assets\app.ico
UninstallDisplayIcon={app}\StudentHeadshotManager.exe
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog commandline
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
CloseApplications=yes
RestartApplications=no
Uninstallable=yes
VersionInfoVersion={#AppVersion}
[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked
[Files]
Source: "{#AppSourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\Student Headshot Manager"; Filename: "{app}\StudentHeadshotManager.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Student Headshot Manager"; Filename: "{app}\StudentHeadshotManager.exe"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{group}\User guide"; Filename: "{sys}\notepad.exe"; Parameters: """{app}\_internal\docs\README.md"""
[Run]
Filename: "{app}\StudentHeadshotManager.exe"; Description: "Launch Student Headshot Manager"; Flags: nowait postinstall skipifsilent runasoriginaluser
[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    if not UninstallSilent then
      MsgBox('Student records, backups, logs and photographs have been preserved in your user data and configured photo folders.', mbInformation, MB_OK);
end;
