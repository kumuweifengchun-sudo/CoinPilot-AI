#requires -Version 5.1
[CmdletBinding()]
param([switch]$TypeOnly)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$previousEncoding = $env:PYTHONIOENCODING
$previousQtPlatform = $env:QT_QPA_PLATFORM

Push-Location $projectRoot
try {
    Get-Command uv -ErrorAction Stop | Out-Null
    $env:PYTHONIOENCODING = 'utf-8'
    $env:QT_QPA_PLATFORM = 'offscreen'

    Write-Host '检查源码、测试和脚本的类型定义……'
    & uv run --locked --group dev basedpyright
    if ($LASTEXITCODE -ne 0) {
        throw "类型检查失败（退出码 $LASTEXITCODE），停止后续验证。"
    }

    if (-not $TypeOnly) {
        Write-Host '运行完整回归测试……'
        & uv run --locked --group dev pytest -q
        if ($LASTEXITCODE -ne 0) {
            throw "回归测试失败（退出码 $LASTEXITCODE）。"
        }
    }
} finally {
    $env:PYTHONIOENCODING = $previousEncoding
    $env:QT_QPA_PLATFORM = $previousQtPlatform
    Pop-Location
}
