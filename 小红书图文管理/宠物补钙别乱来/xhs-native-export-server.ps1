param(
  [int]$Port = 8765,
  [string]$OutputRoot = (Join-Path ([Environment]::GetFolderPath('Desktop')) 'XHS-Native-Exports')
)

$ErrorActionPreference = 'Stop'

function Write-JsonResponse {
  param(
    [System.Net.HttpListenerResponse]$Response,
    [object]$Payload,
    [int]$StatusCode = 200
  )

  $Response.StatusCode = $StatusCode
  $Response.ContentType = 'application/json; charset=utf-8'
  $Response.Headers['Access-Control-Allow-Origin'] = '*'
  $Response.Headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
  $Response.Headers['Access-Control-Allow-Headers'] = 'Content-Type'

  $json = $Payload | ConvertTo-Json -Depth 6
  $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
  $Response.OutputStream.Write($bytes, 0, $bytes.Length)
  $Response.Close()
}

function Find-Edge {
  $candidates = @(
    'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    'C:\Program Files\Microsoft\Edge\Application\msedge.exe'
  )

  foreach ($candidate in $candidates) {
    if (Test-Path -LiteralPath $candidate) {
      return $candidate
    }
  }

  throw 'Microsoft Edge was not found.'
}

function Convert-ToFileUrl {
  param([string]$Path)
  $resolved = (Resolve-Path -LiteralPath $Path).Path
  return 'file:///' + $resolved.Replace('\', '/')
}

function Resize-PngIfNeeded {
  param(
    [string]$Path,
    [int]$TargetWidth,
    [int]$TargetHeight
  )

  Add-Type -AssemblyName System.Drawing

  $image = [System.Drawing.Image]::FromFile($Path)
  try {
    if ($image.Width -eq $TargetWidth -and $image.Height -eq $TargetHeight) {
      return
    }

    $bitmap = [System.Drawing.Bitmap]::new($TargetWidth, $TargetHeight)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    try {
      $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
      $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
      $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
      $graphics.Clear([System.Drawing.Color]::FromArgb(255, 253, 248))
      $graphics.DrawImage($image, 0, 0, $TargetWidth, $TargetHeight)

      $tempPath = "$Path.tmp.png"
      if (Test-Path -LiteralPath $tempPath) {
        Remove-Item -LiteralPath $tempPath -Force
      }
      $bitmap.Save($tempPath, [System.Drawing.Imaging.ImageFormat]::Png)
    } finally {
      $graphics.Dispose()
      $bitmap.Dispose()
    }
  } finally {
    $image.Dispose()
  }

  Move-Item -LiteralPath $tempPath -Destination $Path -Force
}

if (-not (Test-Path -LiteralPath $OutputRoot)) {
  New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null
}

$edge = Find-Edge
$listener = [System.Net.HttpListener]::new()
$prefix = "http://127.0.0.1:$Port/"
$listener.Prefixes.Add($prefix)
$listener.Start()

Write-Host "XHS native export service is running: $prefix"
Write-Host "Keep this window open, then click the native export button in the HTML page."
Write-Host "Close this window to stop the service."

try {
  while ($listener.IsListening) {
    $context = $listener.GetContext()
    $request = $context.Request
    $response = $context.Response

    $response.Headers['Access-Control-Allow-Origin'] = '*'
    $response.Headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
    $response.Headers['Access-Control-Allow-Headers'] = 'Content-Type'

    if ($request.HttpMethod -eq 'OPTIONS') {
      $response.StatusCode = 204
      $response.Close()
      continue
    }

    if ($request.HttpMethod -ne 'POST' -or $request.Url.AbsolutePath -ne '/export') {
      Write-JsonResponse -Response $response -StatusCode 404 -Payload @{ ok = $false; error = 'Not found' }
      continue
    }

    try {
      $reader = [System.IO.StreamReader]::new($request.InputStream, [System.Text.Encoding]::UTF8)
      $body = $reader.ReadToEnd()
      $reader.Close()

      $payload = $body | ConvertFrom-Json
      if (-not $payload.html) {
        throw 'Missing HTML content.'
      }

      $pageCount = 6
      if ($null -ne $payload.pages) {
        $pageCount = [int]$payload.pages
      }
      if ($pageCount -lt 1 -or $pageCount -gt 20) {
        throw 'Invalid page count.'
      }

      $sourceWidth = 1080
      if ($null -ne $payload.sourceWidth) {
        $sourceWidth = [int]$payload.sourceWidth
      }
      if ($sourceWidth -lt 320) { $sourceWidth = 320 }
      if ($sourceWidth -gt 1200) { $sourceWidth = 1200 }
      $sourceHeight = [int][Math]::Round($sourceWidth * 4 / 3)
      $culture = [System.Globalization.CultureInfo]::InvariantCulture
      $scale = [string]::Format($culture, '{0:0.####}', (1080 / $sourceWidth))

      $timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
      $outputDir = Join-Path $OutputRoot "XHS-Native-Export-$timestamp"
      New-Item -ItemType Directory -Force -Path $outputDir | Out-Null

      $workspaceRoot = $PSScriptRoot
      $htmlPath = Join-Path $workspaceRoot 'xhs-native-export-current.html'
      $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
      [System.IO.File]::WriteAllText($htmlPath, [string]$payload.html, $utf8NoBom)

      $htmlUrl = Convert-ToFileUrl -Path $htmlPath
      $files = @()

      for ($i = 1; $i -le $pageCount; $i++) {
        $outPath = Join-Path $outputDir ("xhs-pet-calcium-balance-page{0}.png" -f $i)
        if (Test-Path -LiteralPath $outPath) {
          Remove-Item -LiteralPath $outPath -Force
        }

        $url = "$htmlUrl`?slide=$i&sourceWidth=$sourceWidth"
        $args = @(
          '--headless=new',
          '--disable-gpu',
          '--hide-scrollbars',
          "--force-device-scale-factor=$scale",
          "--window-size=$sourceWidth,$sourceHeight",
          "--screenshot=$outPath",
          $url
        )

        $process = Start-Process -FilePath $edge -ArgumentList $args -Wait -PassThru -WindowStyle Hidden
        if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $outPath)) {
          throw "Failed to export page $i."
        }

        Resize-PngIfNeeded -Path $outPath -TargetWidth 1080 -TargetHeight 1440

        $files += $outPath
      }

      Write-JsonResponse -Response $response -Payload @{
        ok = $true
        outputDir = $outputDir
        files = $files
      }
    } catch {
      Write-JsonResponse -Response $response -StatusCode 500 -Payload @{
        ok = $false
        error = $_.Exception.Message
      }
    }
  }
} finally {
  if ($listener.IsListening) {
    $listener.Stop()
  }
  $listener.Close()
}
