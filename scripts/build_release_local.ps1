param(
    [string]$Tag = "local",
    [ValidateSet("x64", "arm64")]
    [string]$Architecture = "x64",
    [string]$OutputDir = "build",
    [string]$ReleaseDir = "release"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$cacheRoot = Join-Path $repoRoot ".nuitka-cache"
$buildDir = Join-Path $repoRoot $OutputDir
$releaseDirPath = Join-Path $repoRoot $ReleaseDir
$jobs = [Math]::Max([int]$env:NUMBER_OF_PROCESSORS, 1)

$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
$env:PIP_NO_PYTHON_VERSION_WARNING = "1"
$env:NUITKA_CACHE_DIR = $cacheRoot
$env:NUITKA_CACHE_DIR_DOWNLOADS = Join-Path $cacheRoot "downloads"
$env:NUITKA_CACHE_DIR_BYTECODE = Join-Path $cacheRoot "bytecode"
$env:NUITKA_CACHE_DIR_DLL_DEPENDENCIES = Join-Path $cacheRoot "dll-dependencies"
$env:NUITKA_CACHE_DIR_CLCACHE = Join-Path $cacheRoot "clcache"

function Get-PythonMachine {
    return (python -c "import platform; print(platform.machine().lower())").Trim()
}

function Test-PythonArchitectureMatch {
    param(
        [string]$TargetArchitecture,
        [string]$PythonMachine
    )

    $expectedMachines = @{
        x64 = @("amd64", "x86_64")
        arm64 = @("arm64", "aarch64")
    }

    return $expectedMachines[$TargetArchitecture] -contains $PythonMachine
}

function Get-VsDevCmdPath {
    $vsWhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path $vsWhere)) {
        throw "vswhere.exe was not found."
    }

    $installPath = & $vsWhere -latest -products * -property installationPath
    if (-not $installPath) {
        throw "Visual Studio 2022 with VC tools was not found."
    }

    $vsDevCmd = Join-Path $installPath.Trim() "Common7\Tools\VsDevCmd.bat"
    if (-not (Test-Path $vsDevCmd)) {
        throw "VsDevCmd.bat was not found: $vsDevCmd"
    }

    return $vsDevCmd
}

function Get-PlatformLabel {
    param([string]$TargetArchitecture)

    switch ($TargetArchitecture) {
        "arm64" { return "Windows ARM64" }
        default { return "Windows x64" }
    }
}

function Get-VsArchitecture {
    param([string]$TargetArchitecture)

    switch ($TargetArchitecture) {
        "arm64" { return "arm64" }
        default { return "amd64" }
    }
}

function Write-CmdScript {
    param(
        [string]$Path,
        [string[]]$Lines
    )

    $content = ($Lines -join "`r`n") + "`r`n"
    [System.IO.File]::WriteAllText($Path, $content, [System.Text.Encoding]::ASCII)
}

Push-Location $repoRoot
try {
    $pythonMachine = Get-PythonMachine
    if (-not (Test-PythonArchitectureMatch -TargetArchitecture $Architecture -PythonMachine $pythonMachine)) {
        throw "Python machine '$pythonMachine' does not match target architecture '$Architecture'."
    }

    $vsDevCmd = Get-VsDevCmdPath
    $vsArchitecture = Get-VsArchitecture -TargetArchitecture $Architecture
    $cmdPath = Join-Path $env:TEMP "chaoxing-build-$Architecture.cmd"

    $cmdLines = @(
        "@echo off",
        "call `"$vsDevCmd`" -no_logo -arch=$vsArchitecture -host_arch=$vsArchitecture",
        "if errorlevel 1 exit /b %errorlevel%",
        "python -m nuitka --standalone --assume-yes-for-downloads --experimental=force-dependencies-pefile --enable-plugin=pyqt6 --plugin-no-detection --windows-console-mode=disable --lto=no --jobs=$jobs --progress-bar=none --include-module=websockets.asyncio.server --include-module=websockets.server --include-data-dir=resource=resource --output-dir=""$buildDir"" --output-filename=ChaoxingDesktop.exe desktop_app.py",
        "if errorlevel 1 exit /b %errorlevel%"
    )

    Write-CmdScript -Path $cmdPath -Lines $cmdLines

    try {
        & cmd.exe /d /s /c """$cmdPath"""
        if ($LASTEXITCODE -ne 0) {
            throw "Nuitka build failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        Remove-Item $cmdPath -Force -ErrorAction SilentlyContinue
    }

    python scripts/prepare_release.py `
        --build-dir $buildDir `
        --release-dir $releaseDirPath `
        --tag $Tag `
        --ref-name "local" `
        --sha ((git rev-parse HEAD).Trim()) `
        --platform-label (Get-PlatformLabel -TargetArchitecture $Architecture) `
        --artifact-name "chaoxing-evolved-windows-$Architecture-$Tag"
}
finally {
    Pop-Location
}
