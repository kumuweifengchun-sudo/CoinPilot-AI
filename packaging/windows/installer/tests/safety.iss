; 只执行 InitializeSetup 自检后退出；不安装文件、不创建快捷方式或真实启动项。
[Setup]
AppName=CoinPilot AI Installer Safety Tests
AppVersion=1.0
CreateAppDir=no
Uninstallable=no
PrivilegesRequired=lowest
OutputDir=..\..\build\installer-tests
OutputBaseFilename=installer-safety-tests

[Code]
#include "..\safety.iss"

procedure Require(Condition: Boolean; Message: string);
begin
  if not Condition then RaiseException(Message);
end;

procedure CheckCommand(Key, Exe, Command: string; ShouldDelete: Boolean);
var
  Value: string;
begin
  Require(RegWriteStringValue(HKCU, Key, 'CryptoWidget', Command), 'Cannot write test key');
  RemoveOwnedStartup(Key, Exe);
  if ShouldDelete then
    Require(not RegValueExists(HKCU, Key, 'CryptoWidget'), 'Owned startup survived: ' + Command + ' / parsed: ' + CommandExecutable(Command) + ' / expected: ' + Exe)
  else begin
    Require(RegQueryStringValue(HKCU, Key, 'CryptoWidget', Value), 'Foreign startup removed');
    Require(Value = Command, 'Foreign startup changed');
  end;
end;

function InitializeSetup: Boolean;
var
  Key, Exe, Probe, Value, Report, Token: string;
  Handle: THandle;
begin
  Result := False;
  Token := GetDateTimeString('yyyymmddhhnnsszzz', '-', ':');
  Key := 'Software\CoinPilotAIInstallerTests\' + Token;
  Probe := ExpandConstant('{tmp}\probe.exe');
  Report := ExpandConstant('{param:REPORT|}');
  if Report = '' then exit;
  try
    try
      Exe := 'C:\测试 目录\coinpilot-ai.exe';
      Require(RegWriteStringValue(HKCU, Key, 'OtherApp', 'unchanged'), 'Cannot prepare test key');
      RemoveOwnedStartup(Key, Exe);
      Require(not RegValueExists(HKCU, Key, 'CryptoWidget'), 'Missing startup created');
      CheckCommand(Key, Exe, '"' + Exe + '" --config "C:\数据 文件.json"', True);
      CheckCommand(Key, Exe, '  "' + Lowercase(Exe) + '"' + #9 + '--cache-dir "C:\缓存"', True);
      CheckCommand(Key, 'C:\Widget\coinpilot-ai.exe', 'C:\Widget\coinpilot-ai.exe --config c:\config.json', True);
      CheckCommand(Key, Exe, '"C:\其他目录\coinpilot-ai.exe"', False);
      CheckCommand(Key, Exe, '"C:\Python\pythonw.exe" "C:\src\coinpilot-ai.py"', False);
      CheckCommand(Key, Exe, '"' + Exe + '.backup"', False);
      CheckCommand(Key, Exe, '"' + Exe, False);
      CheckCommand(Key, Exe, 'coinpilot-ai.exe', False);
      CheckCommand(Key, Exe, '', False);
      Require(RegQueryStringValue(HKCU, Key, 'OtherApp', Value) and (Value = 'unchanged'), 'Other app changed');
      Require(CanReplaceFile(Probe), 'Missing file blocked');
      Require(SaveStringToFile(Probe, 'test', False), 'Cannot prepare probe');
      Require(CanReplaceFile(Probe), 'Writable file blocked');
      Handle := CreateFileW(Probe, $80000000, 0, 0, 3, $80, 0);
      Require(Handle <> $FFFFFFFF, 'Cannot lock probe');
      try
        Require(not CanReplaceFile(Probe), 'Locked file accepted');
      finally
        CloseHandle(Handle);
      end;
      Require(CanReplaceFile(Probe), 'Released file still blocked');
      Require(not CanReplaceFile(ExpandConstant('{srcexe}')), 'Running EXE accepted');
      Require(SaveStringToFile(Report, 'PASS: startup ownership, quoting, foreign entries, exclusive locks, running EXE', False), 'Cannot save report');
    except
      SaveStringToFile(Report, 'FAIL: ' + GetExceptionMessage, False);
    end;
  finally
    RegDeleteKeyIncludingSubkeys(HKCU, Key);
    RegDeleteKeyIfEmpty(HKCU, 'Software\CoinPilotAIInstallerTests');
    DeleteFile(Probe);
  end;
end;
