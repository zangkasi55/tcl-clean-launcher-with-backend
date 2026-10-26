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
$dist = Join-Path $project 'dist'
$apk = Join-Path $dist 'tvhome-release.apk'
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
    if (Test-Path -LiteralPath $apk) {
        throw 'Restore the original private signing identity before updating an accepted APK.'
    }
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
function Get-ApkSigner([string]$Path) {
    $output = @(& (Join-Path $env:ANDROID_HOME 'build-tools\34.0.0\apksigner.bat') verify --print-certs $Path)
    if ($LASTEXITCODE -ne 0) { throw 'APK signature verification failed.' }
    $digests = @(foreach ($line in $output) {
        if ($line -match '^Signer #\d+ certificate SHA-256 digest:\s*([0-9a-fA-F]{64})\s*$') {
            $Matches[1].ToLowerInvariant()
        }
    })
    if ($digests.Count -ne 1) { throw 'Expected exactly one verified APK signer.' }
    return $digests[0]
}
$tasks = @(':app:lintRelease', ':app:assembleRelease')
if (-not $SkipTests) { $tasks = @(':app:testReleaseUnitTest') + $tasks }
Push-Location (Join-Path $project 'launcher')
try {
    & '.\gradlew.bat' @tasks '-PcontrollerRelease=true' "-PcontrollerBuildDir=$appBuild" `
        --project-cache-dir $projectCache --no-daemon --max-workers=2 --console=plain
    if ($LASTEXITCODE -ne 0) { throw 'Android tests/lint/build failed.' }
    $candidate = Join-Path $appBuild 'outputs\apk\release\app-release.apk'
    $size = (Get-Item -LiteralPath $candidate).Length
    if ($size -le 0 -or $size -gt 409600) {
        throw "APK size $size bytes is outside the allowed 1-409600 byte range."
    }
    $counts = $null
    if (-not $SkipTests) {
        $reportDirectory = Join-Path $appBuild 'test-results\testReleaseUnitTest'
        if (-not (Test-Path -LiteralPath $reportDirectory)) { throw 'Native test reports are missing.' }
        $reports = @(Get-ChildItem -LiteralPath $reportDirectory -Filter 'TEST-*.xml' -File)
        if ($reports.Count -eq 0) { throw 'Native test reports are missing.' }
        $counts = @{ tests = 0; failures = 0; errors = 0; skipped = 0 }
        foreach ($report in $reports) {
            [xml]$suite = Get-Content -LiteralPath $report.FullName -Raw
            # Gradle's name attribute shadows the XML Name property in PowerShell.
            if ($suite.DocumentElement.get_Name() -ne 'testsuite') {
                throw "Malformed native test report: $($report.Name)"
            }
            foreach ($name in @('tests', 'failures', 'errors', 'skipped')) {
                $raw = $suite.testsuite.GetAttribute($name)
                $number = 0
                if ($raw -notmatch '^\d+$' -or -not [int]::TryParse($raw, [ref]$number)) {
                    throw "Malformed native test report: $($report.Name)"
                }
                $counts[$name] += $number
            }
        }
        if ($counts.tests -eq 0 -or $counts.failures -ne 0 -or $counts.errors -ne 0 -or $counts.skipped -ne 0) {
            throw 'Native test reports contain missing, failing or skipped tests.'
        }
    }
    $candidateSigner = Get-ApkSigner $candidate
    if ((Test-Path -LiteralPath $apk) -and $candidateSigner -ne (Get-ApkSigner $apk)) {
        throw 'APK signer differs from the accepted release; restore the original signing identity.'
    }
    New-Item -ItemType Directory -Path $dist -Force | Out-Null
    $pending = Join-Path $dist ("tvhome-release." + [guid]::NewGuid().ToString('N') + ".tmp")
    try {
        Copy-Item -LiteralPath $candidate -Destination $pending
        if ((Get-FileHash -LiteralPath $candidate).Hash -ne (Get-FileHash -LiteralPath $pending).Hash) {
            throw 'Staged APK checksum verification failed.'
        }
        if (Test-Path -LiteralPath $apk) {
            # Windows PowerShell converts $null string arguments to an empty path.
            [System.IO.File]::Replace($pending, $apk, [System.Management.Automation.Language.NullString]::Value)
        } else {
            [System.IO.File]::Move($pending, $apk)
        }
    } finally {
        if (Test-Path -LiteralPath $pending) { Remove-Item -LiteralPath $pending }
    }
    $resultsPath = Join-Path $dist 'native-test-results.json'
    if ($null -ne $counts) {
        $counts | ConvertTo-Json | Set-Content -LiteralPath $resultsPath -Encoding UTF8
    } else {
        if (Test-Path -LiteralPath $resultsPath) { Remove-Item -LiteralPath $resultsPath }
        Write-Warning 'Native tests were explicitly skipped; this is not a tested release.'
    }
    Write-Output "Verified APK size: $size bytes (maximum 409600)."
    Get-FileHash -LiteralPath $apk -Algorithm SHA256
} finally {
    $env:TVCONTROL_RELEASE_PASSWORD = $null
    Pop-Location
}
