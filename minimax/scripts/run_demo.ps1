# Run the complete SDD context flow: import -> test -> context -> MiniMax -> test -> state -> resume.
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = $root
if (-not $env:MINIMAX_API_KEY) {
    throw 'MINIMAX_API_KEY must be set before running the demo.'
}
if (-not $env:MINIMAX_BASE_URL) {
    $env:MINIMAX_BASE_URL = 'https://api.minimax.io/v1'
}
if (-not $env:MINIMAX_MODEL) {
    $env:MINIMAX_MODEL = 'MiniMax-M3.1-Flash'
}

Set-Location $root
python -m sdd_context demo @args
exit $LASTEXITCODE
