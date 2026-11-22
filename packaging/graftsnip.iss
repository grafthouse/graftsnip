; Inno Setup script for the GraftSnip Windows installer.
; Build with packaging\build.ps1 (it runs PyInstaller first, then this).

#define AppName "GraftSnip"
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{6E0C3B7A-4F1D-4C8E-9E5B-2A7D9F3C1B54}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Grafthouse
AppPublisherURL=https://grafthouse.vercel.app
AppSupportURL=https://grafthouse.vercel.app/projects/graftsnip
DefaultDirName={autopf}\Grafthouse\{#AppName}
DefaultGroupName=Grafthouse
DisableProgramGroupPage=yes
; Per-user install by default: no admin prompt. Users can still choose all users.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=out
OutputBaseFilename=GraftSnip-{#AppVersion}-windows-x64-setup
SetupIconFile=..\graftsnip.ico
UninstallDisplayIcon={app}\GraftSnip.exe
LicenseFile=..\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "work\dist\GraftSnip\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\GraftSnip.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\GraftSnip.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\GraftSnip.exe"; Parameters: "--tray"; Description: "Start {#AppName} in the system tray"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Stop a running copy so its files can be removed.
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM GraftSnip.exe"; Flags: runhidden; RunOnceId: "StopGraftSnip"

[Registry]
; Remove the "start with Windows" entry on uninstall, if the user turned it on.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "GraftSnip"; Flags: uninsdeletevalue dontcreatekey
