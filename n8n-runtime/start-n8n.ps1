$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$WorkspaceRoot = Split-Path -Parent $Root
$NodeRoot = Join-Path $WorkspaceRoot "tools\node-v22.23.0-win-x64"
$DataDir = Join-Path $Root "data"

if (-not (Test-Path (Join-Path $NodeRoot "node.exe"))) {
  throw "Portable Node 22 not found at $NodeRoot. Run npm install with the workspace Node 22 setup first."
}

New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

$env:PATH = "$NodeRoot;$env:PATH"
$env:N8N_HOST = "localhost"
$env:N8N_PORT = "5678"
$env:N8N_PROTOCOL = "http"
$env:N8N_USER_FOLDER = $DataDir
$env:N8N_SECURE_COOKIE = "false"
$env:GENERIC_TIMEZONE = "Asia/Shanghai"
$env:TZ = "Asia/Shanghai"

Set-Location $Root
& (Join-Path $NodeRoot "npm.cmd") run start
