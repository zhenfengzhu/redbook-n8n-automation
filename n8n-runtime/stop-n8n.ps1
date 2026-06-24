$ErrorActionPreference = "Stop"

$connections = Get-NetTCPConnection -LocalPort 5678 -ErrorAction SilentlyContinue
$processIds = $connections | Select-Object -ExpandProperty OwningProcess -Unique

if (-not $processIds) {
  Write-Host "n8n is not listening on port 5678."
  exit 0
}

foreach ($processId in $processIds) {
  Write-Host "Stopping n8n process $processId"
  Stop-Process -Id $processId -Force
}
