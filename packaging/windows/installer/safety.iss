{ 安装器与不执行安装的隔离自检共用；Inno Setup 6 的脚本宿主为 32 位。 }
function CreateFileW(FileName: string; Access, ShareMode: LongWord;
  Security: LongWord; Creation, Attributes: LongWord; Template: LongWord): THandle;
  external 'CreateFileW@kernel32.dll stdcall';
function CloseHandle(Handle: THandle): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';

function CanReplaceFile(Exe: string): Boolean;
var
  Handle: THandle;
begin
  Result := True;
  if not FileExists(Exe) then exit;
  { 只探测，不写入；运行中的 EXE 不能以独占写权限打开。 }
  Handle := CreateFileW(Exe, $C0000000, 0, 0, 3, $80, 0);
  Result := Handle <> $FFFFFFFF;
  if Result then CloseHandle(Handle);
end;

function CommandExecutable(Command: string): string;
var
  I: Integer;
begin
  Result := '';
  Command := Trim(Command);
  if Command = '' then exit;
  if Command[1] = '"' then begin
    { 按 Unicode 字符定位引号，避免 ANSI Pos 在中文路径上返回字节偏移。 }
    I := 2;
    while I <= Length(Command) do begin
      if Command[I] = '"' then begin
        Result := Copy(Command, 2, I - 2);
        exit;
      end;
      I := I + 1;
    end;
  end else begin
    I := 1;
    while I <= Length(Command) do begin
      if (Command[I] = ' ') or (Command[I] = #9) then break;
      I := I + 1;
    end;
    Result := Copy(Command, 1, I - 1);
  end;
end;

procedure RemoveOwnedStartup(Key, InstalledExe: string);
var
  Command, Exe: string;
begin
  { 与应用共用改名前的注册表值名，更名升级不会产生重复启动项。 }
  if not RegQueryStringValue(HKCU, Key, 'CryptoWidget', Command) then exit;
  Exe := CommandExecutable(Command);
  { startup.py 写入绝对路径；未知、相对路径和其他位置的启动项均保留。 }
  if (Exe <> '') and (CompareText(Exe, InstalledExe) = 0) then
    if not RegDeleteValue(HKCU, Key, 'CryptoWidget') then
      Log('无法删除本安装对应的开机启动项，请在 Windows 启动应用设置中手动处理。');
end;
