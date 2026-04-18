param(
    [string]$Tag = "local",
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

Push-Location $repoRoot
try {
    python -m nuitka `
        --standalone `
        --assume-yes-for-downloads `
        --experimental=force-dependencies-pefile `
        --enable-plugin=pyqt5 `
        --plugin-no-detection `
        --windows-console-mode=disable `
        --lto=no `
        --jobs=$jobs `
        --progress-bar=none `
        --include-module=websockets.asyncio.server `
        --include-module=websockets.server `
        --include-data-dir=resource=resource `
        --output-dir=$buildDir `
        --output-filename=ChaoxingDesktop.exe `
        desktop_app.py

    python scripts/prepare_release.py `
        --build-dir $buildDir `
        --release-dir $releaseDirPath `
        --tag $Tag `
        --ref-name "local" `
        --sha ((git rev-parse HEAD).Trim())
}
finally {
    Pop-Location
}
