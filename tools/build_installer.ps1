#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$IsccPath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$projectRoot = Split-Path -Parent $PSScriptRoot
$previousPythonEncoding = $env:PYTHONIOENCODING
$env:PYTHONIOENCODING = 'utf-8'

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Executable failed (exit $LASTEXITCODE)."
    }
}

function Find-Iscc {
    if ($IsccPath) {
        if (Test-Path -LiteralPath $IsccPath -PathType Container) {
            $script:IsccPath = Join-Path $IsccPath 'ISCC.exe'
        }
        return (Resolve-Path -LiteralPath $IsccPath -ErrorAction Stop).Path
    }
    $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $candidates = @()
    # Inno Setup 的卸载登记可以定位 D: 等非默认安装位置。
    foreach ($key in @(
        'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1',
        'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1',
        'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1'
    )) {
        $entry = Get-ItemProperty -LiteralPath $key -Name InstallLocation -ErrorAction SilentlyContinue
        if ($entry -and $entry.InstallLocation) {
            $candidates += Join-Path $entry.InstallLocation 'ISCC.exe'
        }
    }
    foreach ($base in @(${env:ProgramFiles(x86)}, $env:ProgramFiles, "$env:LOCALAPPDATA\Programs")) {
        if ($base) { $candidates += Join-Path $base 'Inno Setup 6\ISCC.exe' }
    }
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    throw 'Install Inno Setup 6.5+ (6.x), or pass -IsccPath "D:\Inno Setup 6\ISCC.exe". Download: https://jrsoftware.org/isdl.php'
}

Push-Location $projectRoot
try {
    $uv = (Get-Command uv -ErrorAction Stop).Source
    $compiler = Find-Iscc
    $banner = (& $compiler '/?' 2>&1 | Out-String)
    if ($banner -notmatch 'Inno Setup 6 Command-Line Compiler') {
        throw 'This build requires the Inno Setup 6 command-line compiler.'
    }
    & (Join-Path $PSScriptRoot 'test_installer.ps1') -IsccPath $compiler
    Invoke-Checked $uv @('sync', '--locked', '--group', 'build')
    $metadata = & $uv run --locked --group build python -c 'import json, struct, sys, tomllib; from pathlib import Path; print(json.dumps({"version": tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]["version"], "bits": struct.calcsize("P")*8, "python": list(sys.version_info[:2])}))'
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read Python / project metadata.' }
    $metadata = $metadata | ConvertFrom-Json
    if ($metadata.bits -ne 64 -or ($metadata.python -join '.') -ne '3.13') {
        throw 'The installer requires 64-bit Python 3.13.'
    }
    $version = $metadata.version
    if ($version -notmatch '^\d+\.\d+\.\d+(\.\d+)?$') { throw 'Installer version must have 3 or 4 numeric components.' }
    $windowsVersion = if (($version.Split('.')).Count -eq 3) { "$version.0" } else { $version }

    # 每次构建使用独立暂存目录；失败时不会把上一轮 EXE 或 Setup 当作成功产物。
    $runId = [Guid]::NewGuid().ToString('N')
    $stage = Join-Path $projectRoot "build\installer-$runId"
    $stageDist = Join-Path $stage 'dist'
    $bundle = Join-Path $stageDist 'coinpilot-ai'
    New-Item -ItemType Directory -Path $stage -Force | Out-Null
    Invoke-Checked $uv @('run', '--locked', '--group', 'build', 'pyinstaller', '--clean', '--noconfirm',
        '--distpath', $stageDist, '--workpath', (Join-Path $stage 'pyinstaller'), 'coinpilot-ai.spec')

    $required = @('coinpilot-ai.exe', '_internal\python313.dll', '_internal\pyproject.toml',
        '_internal\coinpilot_ai\assets\app_icon\app.ico',
        '_internal\coinpilot_ai\assets\app_icon\16.ico',
        '_internal\coinpilot_ai\assets\app_icon\32.ico',
        '_internal\coinpilot_ai\assets\app_icon\64.ico',
        '_internal\coinpilot_ai\assets\app_icon\128.ico',
        '_internal\coinpilot_ai\assets\app_icon\256.ico',
        '_internal\coinpilot_ai\assets\update-install.ps1',
        '_internal\coinpilot_ai\assets\fonts\InterVariable.ttf',
        '_internal\coinpilot_ai\assets\fonts\InterVariable-Italic.ttf',
        '_internal\coinpilot_ai\assets\fonts\OFL.txt',
        '_internal\coinpilot_ai\assets\lucide\LICENSE',
        '_internal\PyQt6\Qt6\translations\qtbase_zh_CN.qm',
        '_internal\PyQt6\Qt6\plugins\platforms\qwindows.dll',
        '_internal\licenses\CoinPilotAI\LICENSE', '_internal\licenses\Python\LICENSE.txt',
        '_internal\licenses\PyQt6\LICENSE', '_internal\licenses\PyQt6-Qt6\LICENSE',
        '_internal\licenses\PyQt6-sip\LICENSE')
    foreach ($relative in $required) {
        if (-not (Test-Path -LiteralPath (Join-Path $bundle $relative) -PathType Leaf)) {
            throw "Missing distribution file: $relative"
        }
    }
    if (-not (Get-ChildItem -LiteralPath "$bundle\_internal\coinpilot_ai\assets" -Recurse -Filter *.svg)) {
        throw 'No SVG resources in bundle.'
    }
    $exeVersion = (Get-Item -LiteralPath (Join-Path $bundle 'coinpilot-ai.exe')).VersionInfo
    if ($exeVersion.FileVersion -ne $windowsVersion -or $exeVersion.ProductVersion -ne $version) {
        throw 'EXE version does not match pyproject.toml.'
    }
    Invoke-Checked $uv @('run', '--locked', '--group', 'build', 'python', 'tools/smoke_test.py',
        '--exe', (Join-Path $bundle 'coinpilot-ai.exe'))
    $setupStage = Join-Path $stage 'setup'
    Invoke-Checked $compiler @("/DAppVersion=$version", "/DBuildDir=$bundle", "/DOutputDir=$setupStage",
        (Join-Path $projectRoot 'installer\coinpilot-ai.iss'))
    $setupName = "CoinPilotAI-Setup-$version-x64.exe"
    $setup = Join-Path $setupStage $setupName
    if (-not (Test-Path -LiteralPath $setup -PathType Leaf)) { throw 'Compiler did not produce the expected installer.' }
    $setupVersion = (Get-Item -LiteralPath $setup).VersionInfo
    # Inno Setup 的文本版本字段带固定宽度空格；文件版本按四段数值核对。
    $setupFileVersion = @($setupVersion.FileMajorPart, $setupVersion.FileMinorPart,
        $setupVersion.FileBuildPart, $setupVersion.FilePrivatePart) -join '.'
    if ($setupFileVersion -ne $windowsVersion -or $setupVersion.ProductVersion.Trim() -ne $version) {
        throw 'Installer version does not match pyproject.toml.'
    }

    # 发布已成功验证的产物；只移除仓库 dist 下固定的中间产物目录。
    $distRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot 'dist'))
    $finalBundle = [IO.Path]::GetFullPath((Join-Path $distRoot 'coinpilot-ai'))
    if ($finalBundle -ne (Join-Path $distRoot 'coinpilot-ai') -or
        -not $finalBundle.StartsWith($distRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Unsafe distribution path.'
    }
    if (Test-Path -LiteralPath $finalBundle) {
        if ((Get-Item -LiteralPath $finalBundle).Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw 'Refusing to replace a linked distribution directory.'
        }
        Remove-Item -LiteralPath $finalBundle -Recurse -Force
    }
    New-Item -ItemType Directory -Path (Join-Path $distRoot 'installer') -Force | Out-Null
    Move-Item -LiteralPath $bundle -Destination $finalBundle
    $finalSetup = Join-Path $distRoot "installer\$setupName"
    Copy-Item -LiteralPath $setup -Destination $finalSetup -Force
    $hash = (Get-FileHash -LiteralPath $finalSetup -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath "$finalSetup.sha256" -Value "$hash  $setupName" -Encoding ascii
    Write-Host "Installer: $finalSetup"
    Write-Host "SHA256: $hash"
    Write-Host 'Build and startup checks passed. Installation lifecycle acceptance must also be completed in an isolated Windows user / VM.'
} finally {
    $env:PYTHONIOENCODING = $previousPythonEncoding
    Pop-Location
}
