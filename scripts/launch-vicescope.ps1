[CmdletBinding()]
param(
    [string] $FFmpegBin = '',
    [switch] $SmokeTest
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$executable = Join-Path $projectRoot 'dist\ViceScopeClipStudio.exe'
$runtimeRoot = Join-Path $projectRoot '.vicescope-runtime'
$tempDir = Join-Path $runtimeRoot 'temp'
$cacheDir = Join-Path $runtimeRoot 'cache'

if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "ViceScope executable not found: $executable. Build it as described in docs/build-and-package.md."
}
if (-not $FFmpegBin) {
    $FFmpegBin = Join-Path $projectRoot '.venv\ffmpeg-dist\ffmpeg-9.0.2-essentials_build\bin'
}
if (-not [IO.Path]::IsPathRooted($FFmpegBin)) {
    throw 'FFmpegBin must be an absolute path to the folder containing ffmpeg.exe and ffprobe.exe.'
}
foreach ($tool in @('ffmpeg.exe', 'ffprobe.exe')) {
    if (-not (Test-Path -LiteralPath (Join-Path $FFmpegBin $tool) -PathType Leaf)) {
        throw "$tool not found in $FFmpegBin. Pass -FFmpegBin with the correct folder."
    }
}

foreach ($directory in @($tempDir, $cacheDir)) {
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
        New-Item -ItemType Directory -Path $directory -ErrorAction Stop | Out-Null
    }
}

$names = @('TEMP', 'TMP', 'VICESCOPE_FFMPEG_BIN', 'VICESCOPE_CACHE_DIR', 'QT_QPA_PLATFORM')
$previous = @{}
foreach ($name in $names) { $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    $env:TEMP = $tempDir
    $env:TMP = $tempDir
    $env:VICESCOPE_FFMPEG_BIN = $FFmpegBin
    $env:VICESCOPE_CACHE_DIR = $cacheDir
    if ($SmokeTest) {
        $env:QT_QPA_PLATFORM = 'offscreen'
        & $executable --smoke-test
    } else {
        & $executable
    }
    if ($LASTEXITCODE -ne 0) { throw "ViceScope exited with code $LASTEXITCODE." }
} finally {
    foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process') }
}
