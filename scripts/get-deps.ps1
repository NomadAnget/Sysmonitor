# Download third-party dependencies for SysMonitor build.
# Usage:  .\scripts\get-deps.ps1

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$root = Split-Path -Parent $PSScriptRoot
$libs = Join-Path $root "libs"
$lhm_dir = Join-Path $libs "LHM"
$null = New-Item -ItemType Directory -Force -Path $libs, $lhm_dir

# ── PawnIO kernel driver installer ──────────────────────────────
$pawnio_url = "https://github.com/namazso/PawnIO.Setup/releases/download/2.2.0/PawnIO_setup.exe"
$pawnio_out = Join-Path $libs "PawnIO_setup.exe"

if (-not (Test-Path $pawnio_out)) {
    Write-Host "Downloading PawnIO_setup.exe 2.2.0 ..."
    Invoke-WebRequest -Uri $pawnio_url -OutFile $pawnio_out -UseBasicParsing
    Write-Host "  -> $((Get-Item $pawnio_out).Length / 1KB) KB"
} else {
    Write-Host "PawnIO_setup.exe already present, skipping."
}

# ── LibreHardwareMonitorLib (LHM) .NET Framework DLLs ──────────
# Use the full LibreHardwareMonitor.zip (net48/netstandard2.0) for
# pythonnet "netfx" CLR compatibility.  The .NET 10 zip lacks
# System.Memory etc. needed by .NET Framework.
$lhm_zip_url = "https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/download/v0.9.6/LibreHardwareMonitor.zip"
$lhm_zip = Join-Path $libs "lhm.zip"

$lhm_dlls = @(
    "LibreHardwareMonitorLib.dll"
    "System.Memory.dll"
    "System.Runtime.CompilerServices.Unsafe.dll"
    "System.Numerics.Vectors.dll"
)

$need_lhm = $false
foreach ($dll in $lhm_dlls) {
    if (-not (Test-Path (Join-Path $lhm_dir $dll))) {
        $need_lhm = $true
        break
    }
}

if ($need_lhm) {
    Write-Host "Downloading LibreHardwareMonitorLib v0.9.6 ..."
    Invoke-WebRequest -Uri $lhm_zip_url -OutFile $lhm_zip -UseBasicParsing

    Write-Host "Extracting LHM DLLs ..."
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead($lhm_zip)
    try {
        foreach ($dll in $lhm_dlls) {
            $entry = $zip.Entries | Where-Object { $_.Name -eq $dll } | Select-Object -First 1
            if ($entry) {
                $out = Join-Path $lhm_dir $dll
                [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $out, $true)
                Write-Host "  -> $dll"
            } else {
                Write-Warning "  DLL not found in zip: $dll"
            }
        }
    } finally {
        $zip.Dispose()
    }
    Remove-Item -Force $lhm_zip
} else {
    Write-Host "LHM DLLs already present, skipping."
}

Write-Host "All dependencies ready."
