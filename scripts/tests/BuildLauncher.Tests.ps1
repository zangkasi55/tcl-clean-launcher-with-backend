$script:BuildScriptUnderTest = Join-Path (Split-Path -Parent $PSScriptRoot) 'build-launcher.ps1'

function New-BuildFixture([hashtable]$Options) {
    $project = Join-Path $script:FixtureRoot 'project'
    $local = Join-Path $script:FixtureRoot 'local'
    $tools = Join-Path $local 'TclTvBuild'
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $hash = [BitConverter]::ToString($sha.ComputeHash(
            [System.Text.Encoding]::UTF8.GetBytes($project))).Replace('-', '').Substring(0, 12)
    } finally { $sha.Dispose() }
    $app = Join-Path $tools "builds\$hash\app"
    $signing = Join-Path $tools 'signing'
    $sdk = Join-Path $tools 'sdk'
    $directories = @(
        "$project\scripts", "$project\launcher", "$project\dist", $signing,
        "$tools\jdk17\fixture\bin", "$sdk\platforms\android-34",
        "$sdk\build-tools\34.0.0", "$app\outputs\apk\release"
    )
    foreach ($directory in $directories) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }
    Copy-Item -LiteralPath $script:BuildScriptUnderTest -Destination "$project\scripts\build-launcher.ps1"
    [System.IO.File]::WriteAllBytes("$tools\jdk17\fixture\bin\java.exe", [byte[]]@())
    [System.IO.File]::WriteAllBytes("$sdk\platforms\android-34\android.jar", [byte[]]@())
    if (-not $Options.MissingKeys) {
        [System.IO.File]::WriteAllText("$signing\tcl-tvhome.jks", 'synthetic test key placeholder')
        [System.IO.File]::WriteAllText("$signing\keystore-password.txt", 'synthetic-test-password')
    }
    $size = if ($Options.ContainsKey('Size')) { $Options.Size } else { 256 }
    $candidate = "$app\outputs\apk\release\app-release.apk"
    [System.IO.File]::WriteAllBytes($candidate, (New-Object byte[] $size))
    $accepted = "$project\dist\tvhome-release.apk"
    if (-not $Options.NewDestination) {
        [System.IO.File]::WriteAllText($accepted, 'previous accepted APK fixture')
    }
    [System.IO.File]::WriteAllText("$project\dist\native-test-results.json", '{"tests":999}')
    if (-not $Options.MissingReports) {
        $reports = "$app\test-results\testReleaseUnitTest"
        New-Item -ItemType Directory -Path $reports -Force | Out-Null
        $xml = if ($Options.ContainsKey('Xml')) { $Options.Xml } else {
            '<testsuite name="fixture" tests="2" failures="0" errors="0" skipped="0"/>'
        }
        [System.IO.File]::WriteAllText("$reports\TEST-fixture.xml", $xml)
    }
    $gradleExit = if ($Options.GradleFailure) { 1 } else { 0 }
    [System.IO.File]::WriteAllText("$project\launcher\gradlew.bat", @"
@echo off
> "%~dp0gradle-arguments.txt" echo %*
exit /b $gradleExit
"@)
    $candidateExit = if ($Options.InvalidCandidateSignature) { 1 } else { 0 }
    $acceptedExit = if ($Options.InvalidAcceptedSignature) { 1 } else { 0 }
    $candidateDigest = 'a' * 64
    $acceptedDigest = if ($Options.DifferentSigner) { 'b' * 64 } else { $candidateDigest }
    [System.IO.File]::WriteAllText("$sdk\build-tools\34.0.0\apksigner.bat", @"
@echo off
if "%~nx3"=="app-release.apk" (
  echo Signer #1 certificate SHA-256 digest: $candidateDigest
  exit /b $candidateExit
)
echo Signer #1 certificate SHA-256 digest: $acceptedDigest
exit /b $acceptedExit
"@)
    $env:LOCALAPPDATA = $local
    $env:JAVA_HOME = ''
    $env:ANDROID_HOME = ''
    return @{
        Script = "$project\scripts\build-launcher.ps1"
        Project = $project
        Accepted = $accepted
        CandidateHash = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash
        AcceptedHash = if (Test-Path -LiteralPath $accepted) {
            (Get-FileHash -LiteralPath $accepted -Algorithm SHA256).Hash
        } else { $null }
    }
}

Describe 'Native release acceptance preserves the previous APK' {
    BeforeEach {
        $script:OriginalEnvironment = @{}
        foreach ($name in @('LOCALAPPDATA', 'JAVA_HOME', 'ANDROID_HOME',
                'TVCONTROL_RELEASE_KEYSTORE', 'TVCONTROL_RELEASE_PASSWORD')) {
            $script:OriginalEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        }
        $script:FixtureRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
            'tcl-build-gates-test-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $script:FixtureRoot | Out-Null
    }

    AfterEach {
        foreach ($name in $script:OriginalEnvironment.Keys) {
            [Environment]::SetEnvironmentVariable($name, $script:OriginalEnvironment[$name], 'Process')
        }
        Remove-Item -LiteralPath $script:FixtureRoot -Recurse -Force
    }

    $cases = @(
        @{ Name = 'accepts a verified matching-signer build'; Options = @{} },
        @{ Name = 'accepts exactly 409600 bytes'; Options = @{ Size = 409600 } },
        @{ Name = 'accepts a first release without a previous APK'; Options = @{ NewDestination = $true } },
        @{ Name = 'rejects an empty APK'; Options = @{ Size = 0 }; ExpectedError = 'outside the allowed' },
        @{ Name = 'rejects 409601 bytes'; Options = @{ Size = 409601 }; ExpectedError = 'outside the allowed' },
        @{ Name = 'rejects failing tests'; Options = @{ Xml = '<testsuite tests="2" failures="1" errors="0" skipped="0"/>' }; ExpectedError = 'failing or skipped tests' },
        @{ Name = 'rejects test errors'; Options = @{ Xml = '<testsuite tests="2" failures="0" errors="1" skipped="0"/>' }; ExpectedError = 'failing or skipped tests' },
        @{ Name = 'rejects skipped tests'; Options = @{ Xml = '<testsuite tests="2" failures="0" errors="0" skipped="1"/>' }; ExpectedError = 'failing or skipped tests' },
        @{ Name = 'rejects zero executed tests'; Options = @{ Xml = '<testsuite tests="0" failures="0" errors="0" skipped="0"/>' }; ExpectedError = 'failing or skipped tests' },
        @{ Name = 'rejects missing test reports'; Options = @{ MissingReports = $true }; ExpectedError = 'reports are missing' },
        @{ Name = 'rejects missing report attributes'; Options = @{ Xml = '<testsuite tests="2"/>' }; ExpectedError = 'Malformed native test report' },
        @{ Name = 'rejects a malformed report root'; Options = @{ Xml = '<testsuites/>' }; ExpectedError = 'Malformed native test report' },
        @{ Name = 'rejects negative report counts'; Options = @{ Xml = '<testsuite tests="2" failures="-1" errors="0" skipped="0"/>' }; ExpectedError = 'Malformed native test report' },
        @{ Name = 'rejects lint or Gradle failure'; Options = @{ GradleFailure = $true }; ExpectedError = 'tests/lint/build failed' },
        @{ Name = 'rejects a bad candidate signature'; Options = @{ InvalidCandidateSignature = $true }; ExpectedError = 'signature verification failed' },
        @{ Name = 'rejects an invalid accepted signature'; Options = @{ InvalidAcceptedSignature = $true }; ExpectedError = 'signature verification failed' },
        @{ Name = 'rejects a different signing key'; Options = @{ DifferentSigner = $true }; ExpectedError = 'signer differs' },
        @{ Name = 'refuses to replace a lost signing identity'; Options = @{ MissingKeys = $true }; ExpectedError = 'Restore the original private signing identity' },
        @{ Name = 'removes stale test totals when tests are explicitly skipped'; Options = @{ SkipTests = $true; MissingReports = $true } }
    )

    It '<Name>' -TestCases $cases {
        param($Name, $Options, $ExpectedError)
        $fixture = New-BuildFixture $Options
        $failure = $null
        try {
            if ($Options.SkipTests) {
                & $fixture.Script -SkipTests | Out-Null
            } else {
                & $fixture.Script | Out-Null
            }
        } catch { $failure = $_ }

        if ($ExpectedError) {
            $failure | Should Not BeNullOrEmpty
            $failure.Exception.Message | Should Match ([regex]::Escape($ExpectedError))
            (Get-FileHash -LiteralPath $fixture.Accepted -Algorithm SHA256).Hash |
                Should Be $fixture.AcceptedHash
            Get-Content -LiteralPath "$($fixture.Project)\dist\native-test-results.json" -Raw |
                Should Match '"tests":999'
        } else {
            $failure | Should BeNullOrEmpty
            (Get-FileHash -LiteralPath $fixture.Accepted -Algorithm SHA256).Hash |
                Should Be $fixture.CandidateHash
            $results = "$($fixture.Project)\dist\native-test-results.json"
            if ($Options.SkipTests) {
                Test-Path -LiteralPath $results | Should Be $false
            } else {
                (Get-Content -LiteralPath $results -Raw | ConvertFrom-Json).tests | Should Be 2
            }
        }
        @(Get-ChildItem -LiteralPath "$($fixture.Project)\dist" -Filter 'tvhome-release.*.tmp').Count |
            Should Be 0
        $arguments = "$($fixture.Project)\launcher\gradle-arguments.txt"
        if (Test-Path -LiteralPath $arguments) {
            $actual = Get-Content -LiteralPath $arguments -Raw
            $actual | Should Match ':app:lintRelease'
            $actual | Should Match ':app:assembleRelease'
            if (-not $Options.SkipTests) { $actual | Should Match ':app:testReleaseUnitTest' }
        }
    }
}
