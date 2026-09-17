; The Windows installer, built by Inno Setup around PyInstaller's one-folder output.
;
; Installs for the current person only, into their own AppData, so it needs no
; administrator password and changes nothing for anyone else on the computer.
; Not signed: Windows SmartScreen warns about it once, and the release notes say
; what to press. Uninstalling removes the app and nothing else — never the record
; folder, and never the reader's downloaded files, which the release notes say
; how to remove.
;
;   iscc /DAppVersion=1.0.0 /DSourceDir=dist\Health Record /DOutputDir=out packaging\installer.iss

#define AppName "Health Record"

[Setup]
AppId={{6B0A2B8E-3E52-4E7B-9C1B-7C0E4D2B9A51}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=The health record project
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=Health-Record-{#AppVersion}-windows-x64-setup
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
UninstallDisplayName={#AppName}

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{userprograms}\{#AppName}"; Filename: "{app}\{#AppName}.exe"

[Run]
Filename: "{app}\{#AppName}.exe"; Description: "Open {#AppName} now"; Flags: nowait postinstall skipifsilent
