param(
    [Parameter(Mandatory = $true)]
    [string]$ConfigPath,

    [switch]$Once
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}

.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

if ($Once) {
    python worker.py --config $ConfigPath --once
} else {
    python worker.py --config $ConfigPath
}
