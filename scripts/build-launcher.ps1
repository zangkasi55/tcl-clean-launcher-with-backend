param([switch]$SkipTests)
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$tools = Join-Path $env:LOCALAPPDATA 'TclTvBuild'
$sha = [System.Security.Cryptography.SHA256]::Create()
try {
    $projectHash = [BitConverter]::ToString($sha.ComputeHash(
        [System.Text.Encoding]::UTF8.GetBytes($project))).Replace('-', '').Substring(0, 12)
} finally { $sha.Dispose() }
$buildRoot = Join-Path $tools (Join-Path 'builds' $projectHash)
$appBuild = Join-Path $buildRoot 'app'
$projectCache = Join-Path $buildRoot 'project-cache'
$portableJdk = Join-Path $tools 'jdk17'
if (Test-Path -LiteralPath $portableJdk) {
    $env:JAVA_HOME = (Get-ChildItem -LiteralPath $portableJdk -Directory |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'bin\java.exe') } |
        Select-Object -First 1).FullName
}
if (-not $env:JAVA_HOME -or -not (Test-Path -LiteralPath (Join-Path $env:JAVA_HOME 'bin\java.exe'))) {
    throw 'Set JAVA_HOME to JDK 17 before building.'
}
if (-not $env:ANDROID_HOME) {
    $portableSdk = Join-Path $tools 'sdk'
    $env:ANDROID_HOME = if (Test-Path -LiteralPath $portableSdk) { $portableSdk } else {
        Join-Path $env:LOCALAPPDATA 'Android\Sdk'
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $env:ANDROID_HOME 'platforms\android-34\android.jar'))) {
    throw 'Install Android SDK platform 34 and build-tools 34.0.0; set ANDROID_HOME.'
}
$signing = Join-Path $tools 'signing'
New-Item -ItemType Directory -Path $signing -Force | Out-Null
$keystore = Join-Path $signing 'tcl-tvhome.jks'
$passwordFile = Join-Path $signing 'keystore-password.txt'
if ((Test-Path -LiteralPath $keystore) -ne (Test-Path -LiteralPath $passwordFile)) {
    throw 'The private signing identity is incomplete. Restore both files; do not replace the key.'
}
if (-not (Test-Path -LiteralPath $keystore)) {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $secret = [Convert]::ToBase64String($bytes)
    [System.IO.File]::WriteAllText($passwordFile, $secret)
    $env:TVCONTROL_RELEASE_PASSWORD = $secret
    & (Join-Path $env:JAVA_HOME 'bin\keytool.exe') -genkeypair -keystore $keystore `
        -storetype JKS -alias tcl-tvhome -keyalg RSA -keysize 3072 -validity 3650 `
        -storepass:env TVCONTROL_RELEASE_PASSWORD -keypass:env TVCONTROL_RELEASE_PASSWORD `
        -dname 'CN=TCL Home Control' -noprompt
    if ($LASTEXITCODE -ne 0) { throw 'Private release signing identity creation failed.' }
}
$env:TVCONTROL_RELEASE_KEYSTORE = $keystore
$env:TVCONTROL_RELEASE_PASSWORD = [System.IO.File]::ReadAllText($passwordFile).Trim()
$tasks = @(':app:assembleRelease')
if (-not $SkipTests) { $tasks = @(':app:testReleaseUnitTest') + $tasks }
Push-Location (Join-Path $project 'launcher')
try {
    & '.\gradlew.bat' @tasks '-PcontrollerRelease=true' "-PcontrollerBuildDir=$appBuild" `
        --project-cache-dir $projectCache --no-daemon --max-workers=2 --console=plain
    if ($LASTEXITCODE -ne 0) { throw 'Android tests/build failed.' }
    $dist = Join-Path $project 'dist'
    New-Item -ItemType Directory -Path $dist -Force | Out-Null
    $apk = Join-Path $dist 'tvhome-release.apk'
    Copy-Item -LiteralPath (Join-Path $appBuild 'outputs\apk\release\app-release.apk') -Destination $apk
    if (-not $SkipTests) {
        $reports = @(Get-ChildItem -LiteralPath (Join-Path $appBuild 'test-results\testReleaseUnitTest') -Filter 'TEST-*.xml' -File)
        if ($reports.Count -eq 0) { throw 'Native test reports are missing.' }
        $counts = @{ tests = 0; failures = 0; errors = 0; skipped = 0 }
        foreach ($report in $reports) {
            [xml]$suite = Get-Content -LiteralPath $report.FullName -Raw
            foreach ($name in @('tests', 'failures', 'errors', 'skipped')) {
                $counts[$name] += [int]$suite.testsuite.GetAttribute($name)
            }
        }
        if ($counts.tests -eq 0 -or $counts.failures -ne 0 -or $counts.errors -ne 0) {
            throw 'Native test reports contain missing or failing tests.'
        }
        $counts | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $dist 'native-test-results.json') -Encoding UTF8
    }
    & (Join-Path $env:ANDROID_HOME 'build-tools\34.0.0\apksigner.bat') verify --print-certs $apk
    if ($LASTEXITCODE -ne 0) { throw 'APK signature verification failed.' }
    Get-FileHash -LiteralPath $apk -Algorithm SHA256
} finally {
    $env:TVCONTROL_RELEASE_PASSWORD = $null
    Pop-Location
}
