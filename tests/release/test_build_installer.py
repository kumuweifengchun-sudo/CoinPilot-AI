"""Exercise the Windows release entry point without building or installing an app."""

from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "scripts/release/build_installer.ps1"
POWERSHELL = shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or not POWERSHELL, reason="Windows PowerShell 5.1 required"
)


def run_ps(script, *arguments):
    return subprocess.run(
        [POWERSHELL, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(script), *arguments],
        capture_output=True, text=True, errors="replace", timeout=30,
    )


def test_release_scripts_parse_in_windows_powershell(tmp_path):
    script = tmp_path / "parse.ps1"
    script.write_text("""
param([string]$Directory)
$ErrorActionPreference = 'Stop'
Get-ChildItem -LiteralPath $Directory -Filter *.ps1 | ForEach-Object {
    $tokens = $null; $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile(
        $_.FullName, [ref]$tokens, [ref]$errors)
    if ($errors) { throw ($errors | Out-String) }
}
""", encoding="utf-8-sig")
    result = run_ps(script, str(BUILDER.parent))
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(("current", "bump", "explicit", "expected"), [
    ("0.1.0", "patch", "", "0.1.1"),
    ("1.2.3", "minor", "", "1.3.0"),
    ("1.2.3", "major", "", "2.0.0"),
    ("1.2.3.4", "patch", "", "1.2.4.0"),
    ("1.2.3.4", "none", "", "1.2.3.4"),
    ("1.2.3", "patch", "2.0.0", "2.0.0"),
    ("1.2.3", "patch", "1.2.3.1", "1.2.3.1"),
    ("1.2.65535", "patch", "", None),
    ("1.2.3", "patch", "65536.0.0", None),
    ("1.2.3", "patch", "1.2.3.0", None),
    ("1.2.3", "patch", "1.2.2", None),
])
def test_release_version_rules(tmp_path, current, bump, explicit, expected):
    script = tmp_path / "version.ps1"
    script.write_text("""
param([string]$Builder, [string]$Current, [string]$Bump, [string]$Explicit)
$ErrorActionPreference = 'Stop'
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Builder, [ref]$null, [ref]$null)
$definition = $ast.Find({ param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'Get-ReleaseVersion'
}, $false)
. ([scriptblock]::Create($definition.Extent.Text))
Get-ReleaseVersion -Current $Current -Increment $Bump -Explicit $Explicit
""", encoding="utf-8-sig")
    arguments = [str(BUILDER), current, bump]
    if explicit:
        arguments.append(explicit)
    result = run_ps(script, *arguments)
    if expected is None:
        assert result.returncode != 0
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == expected


@pytest.mark.parametrize("failure", ["version", "build"])
def test_failed_release_restores_metadata_and_releases_lock(tmp_path, failure):
    # A copied repository and fake executables keep failure tests away from real artifacts.
    release = tmp_path / "scripts/release"
    release.mkdir(parents=True)
    shutil.copyfile(BUILDER, release / BUILDER.name)
    (release / "test_installer.ps1").write_text("param($IsccPath)\n", encoding="ascii")
    original = {
        "pyproject.toml": b'[project]\r\nversion = "1.2.3"\r\n# keep local edits\r\n',
        "uv.lock": b'version = 1\r\n# keep exact original bytes\r\n',
    }
    for name, data in original.items():
        (tmp_path / name).write_bytes(data)
    (tmp_path / "compiler.ps1").write_text(
        "'Inno Setup 6 Command-Line Compiler'\n", encoding="ascii"
    )
    (tmp_path / "fake-uv.ps1").write_text("""
$global:LASTEXITCODE = 0
if ($args[0] -eq 'version') {
    Set-Content pyproject.toml 'changed project'
    Set-Content uv.lock 'changed lock'
    if ($env:RELEASE_TEST_FAILURE -eq 'version') { $global:LASTEXITCODE = 12 }
} elseif ($args -contains '-c') {
    '{"version":"1.2.3","bits":64,"python":[3,13]}'
} elseif ($args -contains 'pyinstaller') {
    $global:LASTEXITCODE = 23
}
""", encoding="ascii")
    runner = tmp_path / "run.ps1"
    runner.write_text("""
param([string]$Failure)
$env:RELEASE_TEST_FAILURE = $Failure
function Get-Command {
    param($Name, $ErrorAction)
    if ($Name -ne 'uv') { throw 'Unexpected command lookup' }
    @{ Source = (Join-Path $PSScriptRoot 'fake-uv.ps1') }
}
try {
    & (Join-Path $PSScriptRoot 'scripts/release/build_installer.ps1') `
        -IsccPath (Join-Path $PSScriptRoot 'compiler.ps1')
    throw 'Expected build failure'
} catch {
    if ($_.Exception.Message -notmatch 'failed \\(exit (12|23)\\)') { throw }
}
$lock = [IO.File]::Open((Join-Path $PSScriptRoot 'build/release.lock'),
    [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
$lock.Dispose()
""", encoding="utf-8-sig")
    result = run_ps(runner, failure)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Release version: 1.2.3 -> 1.2.4" in result.stdout
    assert "have been restored" in result.stdout
    for name, data in original.items():
        assert (tmp_path / name).read_bytes() == data
    assert not (tmp_path / "dist").exists()
