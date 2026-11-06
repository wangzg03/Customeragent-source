param([int]$Port = 8001)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    throw 'Create .venv and install requirements.txt first. See docs/OPERATIONS.md.'
}
& $taskPython -X utf8 -m uvicorn backend.app:app --host 127.0.0.1 --port $Port --workers 1
exit $LASTEXITCODE
