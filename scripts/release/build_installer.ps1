#requires -Version 5.1
[CmdletBinding(DefaultParameterSetName = 'Bump')]
param(
    [string]$IsccPath,
    [Parameter(ParameterSetName = 'Bump')]
    [ValidateSet('patch', 'minor', 'major', 'none')]
    [string]$Bump = 'patch',
    [Parameter(Mandatory = $true, ParameterSetName = 'Version')]
    [ValidatePattern('^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*))?$')]
    [string]$Version
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$previousPythonEncoding = $env:PYTHONIOENCODING
$env:PYTHONIOENCODING = 'utf-8'
$releaseLock = $null
$versionBackup = @{}
$versionChanged = $false

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Executable failed (exit $LASTEXITCODE)."
    }
}

function Find-Iscc {
    if ($IsccPath) {
        $candidate = $IsccPath.TrimEnd('\', '/')
        if (Test-Path -LiteralPath $candidate -PathType Container) {
            $candidate = Join-Path $candidate 'ISCC.exe'
        }
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            throw "Inno Setup compiler not found: $candidate"
        }
        return (Resolve-Path -LiteralPath $candidate -ErrorAction Stop).Path
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

function Get-ReleaseVersion {
    param([string]$Current, [string]$Increment, [string]$Explicit)
    if ($Current -notmatch '^[0-9]+\.[0-9]+\.[0-9]+(\.[0-9]+)?$') {
        throw 'Installer version must have 3 or 4 numeric components.'
    }
    $parts = @($Current.Split('.') | ForEach-Object { [long]$_ })
    if ($Explicit) {
        $parts = @($Explicit.Split('.') | ForEach-Object { [long]$_ })
    } elseif ($Increment -ne 'none') {
        $index = @{ major = 0; minor = 1; patch = 2 }[$Increment]
        $parts[$index]++
        for ($i = $index + 1; $i -lt $parts.Count; $i++) { $parts[$i] = 0 }
    }
    if (@($parts | Where-Object { $_ -gt 65535 }).Count) {
        throw 'Windows version components must not exceed 65535.'
    }
    $target = $parts -join '.'
    $currentComparable = if ($Current.Split('.').Count -eq 3) { "$Current.0" } else { $Current }
    $targetComparable = if ($parts.Count -eq 3) { "$target.0" } else { $target }
    if (($Explicit -or $Increment -ne 'none') -and
        [version]$targetComparable -le [version]$currentComparable) {
        throw 'The release version must be greater than the current version. Use -Bump none to rebuild.'
    }
    return $target
}

Push-Location $projectRoot
try {
    New-Item -ItemType Directory -Path (Join-Path $projectRoot 'build') -Force | Out-Null
    try {
        $releaseLock = [IO.File]::Open((Join-Path $projectRoot 'build\release.lock'),
            [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    } catch {
        throw "Cannot acquire the release lock. Another release build may be running. $($_.Exception.Message)"
    }
    $uv = (Get-Command uv -ErrorAction Stop).Source
    $compiler = Find-Iscc
    # ISCC writes help to stderr; PowerShell 5.1 wraps redirected stderr as errors.
    try {
        $ErrorActionPreference = 'Continue'
        $banner = (& $compiler '/?' 2>&1 | Out-String)
    } finally {
        $ErrorActionPreference = 'Stop'
    }
    if ($banner -notmatch 'Inno Setup 6 Command-Line Compiler') {
        throw 'This build requires the Inno Setup 6 command-line compiler.'
    }
    & (Join-Path $PSScriptRoot 'test_installer.ps1') -IsccPath $compiler
    Invoke-Checked $uv @('sync', '--locked', '--group', 'build')
    # Use Python single quotes so Windows PowerShell 5.1 preserves native arguments.
    $metadataCode = @'
import json, struct, sys; from coinpilot_ai.core.version import VERSION; print(json.dumps({'version': VERSION, 'bits': struct.calcsize('P')*8, 'python': list(sys.version_info[:2])}))
'@
    $metadata = & $uv run --locked --group build python -c $metadataCode
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read Python / project metadata.' }
    $metadata = $metadata | ConvertFrom-Json
    if ($metadata.bits -ne 64 -or ($metadata.python -join '.') -ne '3.13') {
        throw 'The installer requires 64-bit Python 3.13.'
    }
    $releaseVersion = Get-ReleaseVersion -Current $metadata.version -Increment $Bump -Explicit $Version
    if ($releaseVersion -ne $metadata.version) {
        foreach ($name in @('pyproject.toml', 'uv.lock')) {
            $path = Join-Path $projectRoot $name
            $versionBackup[$path] = [IO.File]::ReadAllBytes($path)
        }
        $versionChanged = $true
        Write-Host "Release version: $($metadata.version) -> $releaseVersion"
        Invoke-Checked $uv @('version', $releaseVersion, '--no-sync', '--offline')
        Invoke-Checked $uv @('lock', '--check', '--offline')
    } else {
        Write-Host "Rebuilding version: $releaseVersion"
    }
    $windowsVersion = if (($releaseVersion.Split('.')).Count -eq 3) { "$releaseVersion.0" } else { $releaseVersion }

    # 每次构建使用独立暂存目录；失败时不会把上一轮 EXE 或 Setup 当作成功产物。
    $runId = [Guid]::NewGuid().ToString('N')
    $stage = Join-Path $projectRoot "build\installer-$runId"
    $stageDist = Join-Path $stage 'dist'
    $bundle = Join-Path $stageDist 'coinpilot-ai'
    New-Item -ItemType Directory -Path $stage -Force | Out-Null
    Invoke-Checked $uv @('run', '--locked', '--group', 'build', 'pyinstaller', '--clean', '--noconfirm',
        '--distpath', $stageDist, '--workpath', (Join-Path $stage 'pyinstaller'), 'packaging\windows\coinpilot-ai.spec')

    $required = @('coinpilot-ai.exe', '_internal\python313.dll', '_internal\pyproject.toml',
        '_internal\coinpilot_ai\assets\app_icon\app.ico',
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
    if ($exeVersion.FileVersion -ne $windowsVersion -or $exeVersion.ProductVersion -ne $releaseVersion) {
        throw 'EXE version does not match pyproject.toml.'
    }
    Invoke-Checked $uv @('run', '--locked', '--group', 'build', 'python', 'scripts/qa/smoke_test.py',
        '--exe', (Join-Path $bundle 'coinpilot-ai.exe'))
    $setupStage = Join-Path $stage 'setup'
    Invoke-Checked $compiler @("/DAppVersion=$releaseVersion", "/DBuildDir=$bundle", "/DOutputDir=$setupStage",
        (Join-Path $projectRoot 'packaging\windows\installer\coinpilot-ai.iss'))
    $setupName = "CoinPilotAI-Setup-$releaseVersion-x64.exe"
    $setup = Join-Path $setupStage $setupName
    if (-not (Test-Path -LiteralPath $setup -PathType Leaf)) { throw 'Compiler did not produce the expected installer.' }
    $setupVersion = (Get-Item -LiteralPath $setup).VersionInfo
    # Inno Setup 的文本版本字段带固定宽度空格；文件版本按四段数值核对。
    $setupFileVersion = @($setupVersion.FileMajorPart, $setupVersion.FileMinorPart,
        $setupVersion.FileBuildPart, $setupVersion.FilePrivatePart) -join '.'
    if ($setupFileVersion -ne $windowsVersion -or $setupVersion.ProductVersion.Trim() -ne $releaseVersion) {
        throw 'Installer version does not match pyproject.toml.'
    }
    # Use .NET directly; Get-FileHash may be unavailable when PS 5.1 inherits PS 7 module paths.
    $sha256 = [Security.Cryptography.SHA256]::Create()
    $setupStream = $null
    try {
        $setupStream = [IO.File]::OpenRead($setup)
        $hash = [BitConverter]::ToString($sha256.ComputeHash($setupStream)).Replace('-', '').ToLowerInvariant()
    } finally {
        if ($setupStream) { $setupStream.Dispose() }
        $sha256.Dispose()
    }
    Set-Content -LiteralPath "$setup.sha256" -Value "$hash  $setupName" -Encoding ascii

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
    Copy-Item -LiteralPath "$setup.sha256" -Destination "$finalSetup.sha256" -Force
    Write-Host "Installer: $finalSetup"
    Write-Host "SHA256: $hash"
    Write-Host 'Build and startup checks passed. Installation lifecycle acceptance must also be completed in an isolated Windows user / VM.'
} catch {
    if ($versionChanged) {
        foreach ($path in $versionBackup.Keys) {
            [IO.File]::WriteAllBytes($path, $versionBackup[$path])
        }
        Write-Warning 'Build failed; pyproject.toml and uv.lock have been restored.'
    }
    throw
} finally {
    if ($releaseLock) { $releaseLock.Dispose() }
    $env:PYTHONIOENCODING = $previousPythonEncoding
    Pop-Location
}
