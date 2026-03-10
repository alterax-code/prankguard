; PrankGuard — Inno Setup Script (Version Full)
;
; Installe PrankGuard avec Python portable + dependances + modeles IA.
; Taille cible : ~900 MB
;
; Pre-requis :
;   1. Build lite : python build_lite.py (genere dist/PrankGuard.exe)
;   2. Modeles IA dans dist/models/buffalo_sc/
;   3. Inno Setup 6+ installe (https://jrsoftware.org/isinfo.php)
;
; Usage :
;   Ouvrir ce fichier dans Inno Setup Compiler et cliquer "Compile"

[Setup]
AppName=PrankGuard
AppVersion=3.1.0
AppPublisher=PrankGuard Team
AppPublisherURL=https://github.com/prankguard/prankguard
DefaultDirName={autopf}\PrankGuard
DefaultGroupName=PrankGuard
OutputBaseFilename=PrankGuard_Setup_v3.1.0
OutputDir=..\dist\installer
SetupIconFile=..\assets\icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
UninstallDisplayIcon={app}\PrankGuard.exe
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Creer un raccourci sur le bureau"; GroupDescription: "Raccourcis :"; Flags: checked
Name: "startmenuicon"; Description: "Creer un raccourci dans le menu demarrer"; GroupDescription: "Raccourcis :"; Flags: checked

[Files]
; Executable principal
Source: "..\dist\PrankGuard.exe"; DestDir: "{app}"; Flags: ignoreversion

; Modeles IA pre-inclus
Source: "..\dist\models\buffalo_sc\*"; DestDir: "{app}\models\buffalo_sc"; Flags: ignoreversion recursesubdirs createallsubdirs

; Donnees
Source: "..\data\*"; DestDir: "{app}\data"; Flags: ignoreversion recursesubdirs createallsubdirs; Check: DirExists(ExpandConstant('{src}\..\data'))

[Icons]
; Raccourci bureau
Name: "{autodesktop}\PrankGuard"; Filename: "{app}\PrankGuard.exe"; Tasks: desktopicon

; Menu demarrer
Name: "{group}\PrankGuard"; Filename: "{app}\PrankGuard.exe"; Tasks: startmenuicon
Name: "{group}\Desinstaller PrankGuard"; Filename: "{uninstallexe}"; Tasks: startmenuicon

[Run]
; Lancer apres installation
Filename: "{app}\PrankGuard.exe"; Description: "Lancer PrankGuard"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Nettoyer les fichiers de configuration
Type: filesandordirs; Name: "{userappdata}\PrankGuard\logs"
Type: filesandordirs; Name: "{userappdata}\PrankGuard\models"
; Note : on ne supprime PAS les encodings (choix utilisateur RGPD)

[Code]
function DirExists(DirName: string): Boolean;
begin
  Result := DirExists(DirName);
end;
