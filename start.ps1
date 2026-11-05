param([ValidateRange(1024,65535)][int]$Port=8123)
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
uv sync --frozen
if ($LASTEXITCODE -ne 0) { throw 'Dependency setup failed' }
uv run --frozen uvicorn app.main:app --host 127.0.0.1 --port $Port
