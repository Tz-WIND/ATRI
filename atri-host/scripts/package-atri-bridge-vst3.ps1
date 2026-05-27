param(
    [switch]$Release,
    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
$profile = if ($Release) { "release" } else { "debug" }

Push-Location $repoRoot
try {
    $buildArgs = @("build", "-p", "atri-bridge-vst3")
    if ($Release) {
        $buildArgs += "--release"
    }
    cargo @buildArgs

    $packageArgs = @("run", "-p", "atri-bridge-vst3", "--bin", "package_bridge", "--")
    if ($Release) {
        $packageArgs += "--release"
    }
    if ($OutputDir.Trim().Length -gt 0) {
        $packageArgs += @("--output-dir", $OutputDir)
    }
    cargo @packageArgs
} finally {
    Pop-Location
}
