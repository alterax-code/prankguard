; PrankGuard — Script Inno Setup (Version Full)
; Prerequis : effectuer build_lite.bat en premier (genere dist\PrankGuard\)
; Outil     : Inno Setup 6.x — https://jrsoftware.org/isdl.php
; Compile   : build_full.bat  ou  ISCC.exe build_full.iss

#define AppName      "PrankGuard"
#define AppVersion   "1.0.0"
#define AppPublisher "EPITECH"
#define AppExeName   "PrankGuard.exe"
#define SourceDir    "dist\PrankGuard"

[Setup]
; GUID unique de l'application — NE PAS MODIFIER apres premiere distribution
AppId={{A7C3F891-2B4D-4E6A-9F1C-8D5E2A7B3C04}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL=https://github.com/alterax-code/prankguard
AppSupportURL=https://github.com/alterax-code/prankguard/issues
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Sortie de l'installeur
OutputDir=output
OutputBaseFilename=PrankGuard_Setup_{#AppVersion}
; Compression maximale (LZMA2 solid) — reduit ~40-50%
Compression=lzma2/ultra64
SolidCompression=yes
; UI moderne Windows
WizardStyle=modern
; Droits administrateur obligatoires (blocage USB/Bluetooth/Reseau)
PrivilegesRequired=admin
; Windows 10 64-bit minimum
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
; Icone de desinstallation dans Ajout/Suppression de programmes
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
; Ne pas afficher la progression de la decompression (plus propre)
ShowLanguageDialog=no

[Languages]
Name: "french";  MessagesFile: "compiler:Languages\French.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; Copie tout le contenu du build PyInstaller (inclut _internal/ si PyInstaller 6.x)
Source: "{#SourceDir}\*"; DestDir: "{app}"; \
  Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Raccourci Bureau
Name: "{autodesktop}\{#AppName}"; \
  Filename: "{app}\{#AppExeName}"; \
  WorkingDir: "{app}"; \
  Comment: "Surveillance et protection du PC contre les pranks physiques"

; Menu Demarrer
Name: "{group}\{#AppName}"; \
  Filename: "{app}\{#AppExeName}"; \
  WorkingDir: "{app}"; \
  Comment: "Lancer PrankGuard"

Name: "{group}\Desinstaller {#AppName}"; \
  Filename: "{uninstallexe}"

[Run]
; Proposer de lancer l'app a la fin de l'installation
Filename: "{app}\{#AppExeName}"; \
  Description: "Lancer {#AppName} maintenant"; \
  Flags: nowait postinstall skipifsilent runascurrentuser

[UninstallRun]
; Rien de special — les fichiers installes sont supprimes automatiquement

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
  // Verifier architecture 64-bit
  if not IsWin64 then
  begin
    MsgBox(
      '{#AppName} requiert Windows 64-bit (x64).' + #13#10 +
      'Ce systeme n''est pas compatible.',
      mbError, MB_OK
    );
    Result := False;
    Exit;
  end;
  // Verifier Windows 10 minimum
  if not (GetWindowsVersion >= $0A000000) then
  begin
    MsgBox(
      '{#AppName} requiert Windows 10 (build 19041+) minimum.',
      mbError, MB_OK
    );
    Result := False;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  // A la fin de la desinstallation, proposer de supprimer les donnees utilisateur
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{app}\data');
    if DirExists(DataDir) then
    begin
      if MsgBox(
        'Supprimer les donnees utilisateur ?' + #13#10 +
        '(encodings faciaux dans ' + DataDir + ')',
        mbConfirmation, MB_YESNO
      ) = IDYES then
        DelTree(DataDir, True, True, True);
    end;
  end;
end;
