$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonCandidates = @(
    (Join-Path $repoRoot ".venv\Scripts\python.exe"),
    (Join-Path $repoRoot ".venv\bin\python"),
    "python"
)

$pythonCommand = $null
foreach ($candidate in $pythonCandidates) {
    if ($candidate -eq "python") {
        $resolved = Get-Command python -ErrorAction SilentlyContinue
        if ($resolved) {
            $pythonCommand = $resolved.Source
            break
        }
        continue
    }

    if (Test-Path $candidate) {
        $pythonCommand = $candidate
        break
    }
}

if (-not $pythonCommand) {
    throw "No usable Python interpreter was found."
}

& $pythonCommand (Join-Path $repoRoot "cmd_pro.py") @args

