; 通过 scripts/release/build_installer.ps1 构建；AppId 发布后不得更换。
#if (Ver < EncodeVer(6, 5, 0)) || (Ver >= EncodeVer(7, 0, 0))
  #error Inno Setup 6.5 or newer within major version 6 is required
#endif
#ifndef AppVersion
  #error AppVersion must be supplied by scripts/release/build_installer.ps1
#endif
#ifndef BuildDir
  #define BuildDir SourcePath + "..\..\..\dist\coinpilot-ai"
#endif
#ifndef OutputDir
  #define OutputDir SourcePath + "..\..\..\dist\installer"
#endif

[Setup]
AppId={{98E845CA-82E2-4A5E-9A90-CBB7CD3F8CB8}
AppName=CoinPilot AI
AppVersion={#AppVersion}
VersionInfoVersion={#AppVersion}
VersionInfoProductVersion={#AppVersion}
AppPublisher=CoinPilot AI
DefaultDirName={localappdata}\Programs\CoinPilotAI
DefaultGroupName=CoinPilot AI
DisableProgramGroupPage=yes
DisableDirPage=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
UsePreviousAppDir=yes
UsePreviousTasks=yes
UninstallDisplayName=CoinPilot AI
UninstallDisplayIcon={app}\coinpilot-ai.exe
SetupIconFile=..\..\..\coinpilot_ai\assets\app_icon\app.ico
WizardStyle=modern
DisableWelcomePage=no
LicenseFile=..\..\..\LICENSE
InfoBeforeFile=before-install.txt
OutputDir={#OutputDir}
OutputBaseFilename=CoinPilotAI-Setup-{#AppVersion}-x64
Compression=lzma2
SolidCompression=yes
CloseApplications=yes
RestartApplications=no
AlwaysRestart=no
Uninstallable=yes

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Default.isl,languages\ChineseSimplified.isl"

[Messages]
ConfirmUninstall=确定卸载 %1 吗？%n%n卸载将删除程序及快捷方式，保留个人配置、数据库、图标缓存和 Windows 凭据。需要清理这些数据时，请另行手动处理。
ApplicationsFound=以下应用正在使用需要更新的文件。请先从系统托盘选择“退出”，再继续安装。

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："

[Files]
Source: "{#BuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; 升级时清理旧版本捆绑的闲置资源，否则精简后的包覆盖安装仍保留旧体积。
; 仅作用于安装器管理的内部资源，不触碰配置、数据库或用户图标缓存。
Type: files; Name: "{app}\_internal\PyQt6\Qt6\bin\opengl32sw.dll"
Type: files; Name: "{app}\_internal\PyQt6\Qt6\bin\Qt6Pdf.dll"
Type: files; Name: "{app}\_internal\PyQt6\Qt6\plugins\imageformats\qpdf.dll"
Type: files; Name: "{app}\_internal\PyQt6\Qt6\plugins\imageformats\qicns.dll"
Type: files; Name: "{app}\_internal\PyQt6\Qt6\plugins\imageformats\qtga.dll"
Type: files; Name: "{app}\_internal\PyQt6\Qt6\plugins\imageformats\qtiff.dll"
Type: files; Name: "{app}\_internal\PyQt6\Qt6\plugins\imageformats\qwbmp.dll"
Type: files; Name: "{app}\_internal\PyQt6\Qt6\plugins\generic\qtuiotouchplugin.dll"
Type: files; Name: "{app}\_internal\PyQt6\Qt6\plugins\tls\qopensslbackend.dll"
Type: files; Name: "{app}\_internal\PyQt6\Qt6\translations\*.qm"
Type: files; Name: "{app}\_internal\coinpilot_ai\assets\app_icon\project.png"
Type: files; Name: "{app}\_internal\coinpilot_ai\assets\app_icon\16.ico"
Type: files; Name: "{app}\_internal\coinpilot_ai\assets\app_icon\32.ico"
Type: files; Name: "{app}\_internal\coinpilot_ai\assets\app_icon\64.ico"
Type: files; Name: "{app}\_internal\coinpilot_ai\assets\app_icon\128.ico"
Type: files; Name: "{app}\_internal\coinpilot_ai\assets\app_icon\256.ico"
; 更名升级只清理旧入口与旧品牌快捷方式，用户数据仍保留。
Type: files; Name: "{app}\crypto-widget.exe"
Type: files; Name: "{userdesktop}\Crypto Widget.lnk"
Type: files; Name: "{userprograms}\Crypto Widget\Crypto Widget.lnk"
Type: dirifempty; Name: "{userprograms}\Crypto Widget"

[Icons]
Name: "{userprograms}\CoinPilot AI\CoinPilot AI"; Filename: "{app}\coinpilot-ai.exe"; WorkingDir: "{app}"
Name: "{userdesktop}\CoinPilot AI"; Filename: "{app}\coinpilot-ai.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\coinpilot-ai.exe"; Description: "启动 CoinPilot AI"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[Code]
const
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';
  ExitFirst = 'CoinPilot AI 正在运行或程序文件不可写。请从系统托盘选择“退出”（关闭窗口只会隐藏），然后重试。';

#include "safety.iss"

function CanReplaceApplication: Boolean;
begin
  Result := CanReplaceFile(ExpandConstant('{app}\coinpilot-ai.exe')) and
    CanReplaceFile(ExpandConstant('{app}\crypto-widget.exe'));
end;

function PrepareToInstall(var NeedsRestart: Boolean): string;
begin
  Result := '';
  NeedsRestart := False;
  { 静默升级同样受保护；绝不依赖自动关闭来结束托盘程序。 }
  if not CanReplaceApplication then Result := ExitFirst;
end;

function InitializeUninstall: Boolean;
begin
  Result := CanReplaceApplication;
  while not Result do begin
    if UninstallSilent then exit;
    if MsgBox(ExitFirst, mbError, MB_RETRYCANCEL) <> IDRETRY then exit;
    Result := CanReplaceApplication;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep <> usUninstall then exit;
  RemoveOwnedStartup(RunKey, ExpandConstant('{app}\coinpilot-ai.exe'));
  RemoveOwnedStartup(RunKey, ExpandConstant('{app}\crypto-widget.exe'));
end;
