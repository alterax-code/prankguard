; Inno Setup 6 — installeur PrankGuard (par utilisateur, sans droits administrateur)
#define AppName "PrankGuard"
#define AppVersion "3.0.0"

[Setup]
AppId={{6E2B5C1A-4F3D-4B8E-9C2A-PRANKGUARD3}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=PrankGuard
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=PrankGuard-Setup-{#AppVersion}
SetupIconFile=prankguard.ico
UninstallDisplayIcon={app}\PrankGuard.exe
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer une icône sur le Bureau"; Flags: unchecked
Name: "autostart"; Description: "Lancer PrankGuard au démarrage de Windows"

[Files]
Source: "..\dist\PrankGuard\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\PrankGuard.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\PrankGuard.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "PrankGuard"; ValueData: """{app}\PrankGuard.exe"" --background"; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\PrankGuard.exe"; Description: "Lancer PrankGuard"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /IM PrankGuard.exe /F"; Flags: runhidden; RunOnceId: "KillPG"

[UninstallDelete]
Type: filesandordirs; Name: "{userappdata}\PrankGuard\v3\webview"
