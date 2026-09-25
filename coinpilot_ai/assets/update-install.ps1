#requires -Version 5.1
param(
    [Parameter(Mandatory=$true)][int]$ParentPid,
    [Parameter(Mandatory=$true)][string]$Installer,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-fA-F0-9]{64}$')][string]$ExpectedHash,
    [Parameter(Mandatory=$true)][string]$InstallDir,
    [Parameter(Mandatory=$true)][string]$ReadyFile,
    [Parameter(Mandatory=$true)][string]$ErrorFile
)
$ErrorActionPreference = 'Stop'
$stream = $null
$parentProcess = $null
try {
    $parentProcess = Get-Process -Id $ParentPid -ErrorAction SilentlyContinue
    # 持有只读共享句柄，校验完成后到启动安装器期间禁止文件被替换。
    $stream = [IO.File]::Open($Installer, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $actual = [BitConverter]::ToString($sha.ComputeHash([IO.Stream]$stream)).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
    if ($actual -ne $ExpectedHash.ToLowerInvariant()) { throw 'Installer checksum mismatch.' }
    if (-not [IO.Directory]::Exists($InstallDir)) { throw 'Installation directory does not exist.' }
    [IO.File]::WriteAllText($ReadyFile, 'ready')
    if ($parentProcess -and -not $parentProcess.WaitForExit(120000)) { throw 'Application did not exit; installation cancelled.' }
    # 安装向导需要用户查看和操作，因此显示窗口；不静默关闭任何程序。
    $setup = Start-Process -FilePath $Installer -ArgumentList @('/SP-', '/NORESTART', ('/DIR="' + $InstallDir + '"')) -WindowStyle Normal -PassThru -Wait
    if ($setup.ExitCode -ne 0) { throw ('Installer ended with code ' + $setup.ExitCode) }
} catch {
    [IO.File]::WriteAllText($ErrorFile, $_.Exception.Message)
    if (Test-Path -LiteralPath $ReadyFile) {
        Add-Type -AssemblyName System.Windows.Forms
        [Windows.Forms.MessageBox]::Show('更新安装未完成，个人数据已保留。请重新启动 CoinPilot AI 后重试。', 'CoinPilot AI · 软件更新') | Out-Null
    }
    exit 1
} finally {
    if ($stream) { $stream.Dispose() }
    if ($parentProcess) { $parentProcess.Dispose() }
}
