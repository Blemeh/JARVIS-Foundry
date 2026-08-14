param(
    [string]$VenvPath = "C:\Users\b\AppData\Local\Temp\jarvis-r3-venv",
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\.."))
)

$ErrorActionPreference = "Stop"
$env:R3_RECORD_EVIDENCE = "1"

$Py = Join-Path $VenvPath "Scripts\python.exe"
$R3 = Join-Path $RepoRoot "scripts\remaster\r3"
if (-not (Test-Path -LiteralPath $Py)) {
    throw "venv python not found: $Py (run make_venv.ps1 first)"
}

$Steps = @(
    @{ Name = "ruff-owned"; Command = @($Py, "-B", "$R3\external_gates.py", "--mode", "ruff-owned") },
    @{ Name = "ruff-baseline-record"; Command = @($Py, "-B", "$R3\external_gates.py", "--mode", "ruff-baseline") },
    @{ Name = "mypy-owned"; Command = @($Py, "-B", "$R3\typing_measure.py", "--mode", "owned") },
    @{ Name = "mypy-baseline-record"; Command = @($Py, "-B", "$R3\typing_measure.py", "--mode", "baseline") },
    @{ Name = "bandit"; Command = @($Py, "-B", "$R3\external_gates.py", "--mode", "bandit") },
    @{ Name = "pip-audit"; Command = @($Py, "-B", "$R3\external_gates.py", "--mode", "pip-audit") },
    @{ Name = "secret-scan"; Command = @($Py, "-B", "$R3\secret_scan.py") },
    @{ Name = "architecture-rules"; Command = @($Py, "-B", "$R3\architecture_rules.py") },
    @{ Name = "no-silent-failure"; Command = @($Py, "-B", "$R3\no_silent_failure.py") },
    @{ Name = "mutation"; Command = @($Py, "-B", "$R3\mutation_run.py", "--python", $Py) },
    @{ Name = "coverage"; Command = @($Py, "-B", "$R3\coverage_gate.py", "--python", $Py) },
    @{ Name = "focused-tests"; Command = @($Py, "-B", "$R3\external_gates.py", "--mode", "focused-tests") },
    @{ Name = "build-evidence"; Command = @($Py, "-B", "$R3\build_evidence.py", "--install-report", "$env:TEMP\jarvis-r3-install-report.json") },
    @{ Name = "artifact-confinement"; Command = @($Py, "-B", "$R3\artifact_confinement.py", "--python", $Py) }
)

$Failures = @()
foreach ($Step in $Steps) {
    Write-Host "== R3 gate: $($Step.Name)"
    & $Step.Command[0] $Step.Command[1..($Step.Command.Count - 1)]
    $Exit = $LASTEXITCODE
    Write-Host "== $($Step.Name) exit: $Exit"
    if ($Exit -ne 0) {
        $Failures += "$($Step.Name):$Exit"
    }
}

if ($Failures.Count -gt 0) {
    Write-Host "R3 quality FAILED steps: $($Failures -join ', ')"
    exit 1
}
Write-Host "R3 quality all steps completed with exit 0"
exit 0
