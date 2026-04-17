; Inno Setup script — OceanShare Windows installer
; Gerado automaticamente pelo workflow do GitHub Actions

[Setup]
AppName=OceanShare
AppVersion=5.0.0
AppPublisher=OceanShare
AppId={{9B7C3E5D-1F2A-4B8C-9E3D-OCEANSHARE5000}
DefaultDirName={autopf}\OceanShare
DefaultGroupName=OceanShare
OutputDir=installer-output
OutputBaseFilename=OceanShare-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\OceanShare.exe
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "dist\OceanShare.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\OceanShare"; Filename: "{app}\OceanShare.exe"
Name: "{group}\Desinstalar OceanShare"; Filename: "{uninstallexe}"
Name: "{autodesktop}\OceanShare"; Filename: "{app}\OceanShare.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na area de trabalho"; GroupDescription: "Atalhos adicionais:"

[Run]
Filename: "{app}\OceanShare.exe"; Description: "Abrir OceanShare"; Flags: nowait postinstall skipifsilent
