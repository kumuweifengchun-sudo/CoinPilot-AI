#requires -Version 5.1
[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$IsccPath)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$testRoot = Join-Path $projectRoot ('build\installer-tests\' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testRoot -Force | Out-Null
& $IsccPath '/Q' "/O$testRoot" (Join-Path $projectRoot 'packaging\windows\installer\tests\safety.iss')
if ($LASTEXITCODE -ne 0) { throw 'Installer safety test compilation failed.' }
$report = Join-Path $testRoot 'result.txt'
$log = Join-Path $testRoot 'setup.log'
# 自检在 InitializeSetup 返回 False，不安装程序；只使用随机临时注册表键。
$process = Start-Process -FilePath (Join-Path $testRoot 'installer-safety-tests.exe') -ArgumentList @(
    '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/REPORT=`"$report`"", "/LOG=`"$log`""
) -WindowStyle Hidden -Wait -PassThru
if (-not (Test-Path -LiteralPath $report -PathType Leaf)) {
    throw "Installer self-test did not produce a report (exit $($process.ExitCode)). See $log"
}
$result = Get-Content -LiteralPath $report -Raw
if (-not $result.StartsWith('PASS:')) { throw "Installer self-test failed: $result (see $log)" }
Write-Host $result
