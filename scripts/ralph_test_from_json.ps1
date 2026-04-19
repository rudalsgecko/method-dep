#requires -Version 5.1
<#
.SYNOPSIS
    Ralph-style loop: iterate every methoddep per-method JSON and drive an LLM
    CLI through generate -> build -> run -> coverage-verify, retrying on error.

.DESCRIPTION
    For each JSON under <MethoddepOut>/methods/**/*.json this script:
      1. Builds an LLM prompt via scripts/lib/build_prompt.py.
      2. Pipes the prompt to <LlmCmd> on stdin, captures stdout.
      3. Extracts the first ```cpp fenced block from the LLM output and
         writes it to <TestRoot>/gen/<namespace>/<class>/<id>.cpp.
      4. Configures and builds the gtest project at <TestRoot>.
      5. Runs the resulting binary with --gtest_filter=<method_bare_name>*.
      6. Invokes scripts/lib/check_coverage.py; if the target method body
         never executed, the attempt is marked failed.
    On any failure the error tail is fed back into the NEXT prompt so the LLM
    can self-correct. State lives in <TestRoot>/.ralph-state.json via the
    atomic merge_state.py helper.

.PARAMETER DryRun
    Skip the LLM entirely; use a static GoogleTest skeleton so the rest of
    the pipeline (paths, build, coverage) can be exercised offline.

.EXAMPLE
    pwsh scripts/ralph_test_from_json.ps1 `
        -MethoddepOut D:/proj/PowerToys-methoddep-out/fancyzones `
        -TestRoot     D:/proj/PowerToys-methoddep-tests `
        -SourceRoot   D:/proj/PowerToys `
        -LlmCmd       'claude -p' `
        -MaxIterations 3 `
        -OnlyClass    'FancyZones'
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string] $MethoddepOut,
    [Parameter(Mandatory=$true)][string] $TestRoot,
    [Parameter(Mandatory=$true)][string] $SourceRoot,
    [string]   $LlmCmd          = 'claude -p',
    [int]      $MaxIterations   = 3,
    [string]   $OnlyClass       = '',
    [string]   $OnlyNamespace   = '',
    [int]      $Parallel        = 1,
    [switch]   $DryRun,
    [switch]   $SkipBuild,
    [switch]   $SkipCoverage,
    [switch]   $NoConfigure,
    [string]   $Python          = 'python',
    [string]   $CMake           = 'cmake',
    [string]   $BuildConfig     = 'Debug',
    [string]   $CoverageTool    = 'auto',  # auto | opencppcoverage | llvm | gcov | none
    [string]   $PromptTemplate  = ''       # path to custom prompt .md; empty = scripts/templates/prompts/default.md
)

$ErrorActionPreference = 'Stop'
$InformationPreference = 'Continue'

# ------------------- Path / environment bootstrap -------------------

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$LibDir    = Join-Path $ScriptDir 'lib'
$TmplDir   = Join-Path $ScriptDir 'templates/test_project'

function Resolve-Existing([string] $Path) {
    if (-not $Path) { return $null }
    try {
        return (Resolve-Path -LiteralPath $Path -ErrorAction Stop).ProviderPath
    } catch {
        # Not yet present; return normalised absolute path.
        return [System.IO.Path]::GetFullPath($Path)
    }
}

$MethoddepOut = Resolve-Existing $MethoddepOut
$TestRoot     = Resolve-Existing $TestRoot
$SourceRoot   = Resolve-Existing $SourceRoot

if (-not (Test-Path -LiteralPath $MethoddepOut)) {
    throw "MethoddepOut does not exist: $MethoddepOut"
}
if (-not (Test-Path -LiteralPath $LibDir)) {
    throw "missing lib dir: $LibDir"
}

# Create TestRoot scaffolding if absent: copy template into place.
$GenDir     = Join-Path $TestRoot 'gen'
$BuildDir   = Join-Path $TestRoot 'build'
$CovDir     = Join-Path $TestRoot 'coverage'
$LogsDir    = Join-Path $TestRoot 'logs'
$PromptsDir = Join-Path $TestRoot 'prompts'
$StatePath  = Join-Path $TestRoot '.ralph-state.json'

foreach ($d in @($TestRoot, $GenDir, $BuildDir, $CovDir, $LogsDir, $PromptsDir)) {
    if (-not (Test-Path -LiteralPath $d)) {
        [void] (New-Item -ItemType Directory -Path $d -Force)
    }
}

# Copy template files once (non-destructive: preserve user edits).
foreach ($name in @('CMakeLists.txt','main.cpp')) {
    $src = Join-Path $TmplDir $name
    $dst = Join-Path $TestRoot $name
    if (-not (Test-Path -LiteralPath $dst)) {
        Copy-Item -LiteralPath $src -Destination $dst
    }
}

# ------------------- Helpers -------------------

function Write-Log([string] $Msg, [string] $Level = 'info') {
    $ts = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss')
    $line = "[$ts][$Level] $Msg"
    Write-Information $line
    Add-Content -LiteralPath (Join-Path $LogsDir 'ralph.log') -Value $line -Encoding UTF8
}

function To-Posix([string] $p) {
    return ($p -replace '\\','/')
}

function Get-MethodId([string] $JsonPath) {
    return [System.IO.Path]::GetFileNameWithoutExtension($JsonPath)
}

function Get-RelPathSegments([string] $FullJson, [string] $MethodsRoot) {
    $rel = $FullJson.Substring($MethodsRoot.Length).TrimStart('\','/')
    $segments = $rel -split '[\\/]+' | Where-Object { $_ -ne '' }
    if ($segments.Count -eq 0) { return @() }
    # Drop the basename (the sha1.json file itself).
    return $segments[0..($segments.Count - 2)]
}

function Read-MethodMetadata([string] $JsonPath) {
    $raw = Get-Content -LiteralPath $JsonPath -Raw -Encoding UTF8
    return ($raw | ConvertFrom-Json)
}

function Get-BareName($Meta) {
    $qn = $null
    if ($Meta.method -and $Meta.method.qualified_name) { $qn = $Meta.method.qualified_name }
    if (-not $qn) { return 'Unknown' }
    return ($qn -split '::')[-1]
}

function Get-ClassBareName($Meta) {
    $cls = ''
    if ($Meta.method -and $Meta.method.class) { $cls = $Meta.method.class }
    if (-not $cls) { return '' }
    return ($cls -split '::')[-1]
}

function Sanitize-CppIdentifier([string]$Name) {
    if (-not $Name) { return 'Global' }
    $s = $Name -replace '<[^>]*>', ''
    $s = $s -replace '[^A-Za-z0-9_]', '_'
    if (-not $s) { return 'Global' }
    return $s
}

function Get-LastLines([string] $Text, [int] $Count = 100) {
    if (-not $Text) { return '' }
    $lines = $Text -split "`r?`n"
    if ($lines.Count -le $Count) { return ($lines -join "`n") }
    $tail = $lines[($lines.Count - $Count)..($lines.Count - 1)]
    return ("... ({0} earlier lines truncated)`n" -f ($lines.Count - $Count)) + ($tail -join "`n")
}

function Extract-CppBlock([string] $Text) {
    if ([string]::IsNullOrEmpty($Text)) { return $null }
    # Match ALL fenced blocks with optional language tag.
    $allMatches = [regex]::Matches($Text, '(?s)```(?<lang>[A-Za-z+]*)\s*\r?\n(?<body>.*?)```')
    $candidates = @()
    foreach ($m in $allMatches) {
        $lang = $m.Groups['lang'].Value.ToLower()
        $body = $m.Groups['body'].Value
        $hasCppMarker = ($body -match '#include' -or $body -match 'TEST_F' -or $body -match 'TEST\s*\(' -or $body -match 'namespace')
        if ($lang -in @('cpp','c++','cxx') -or $hasCppMarker) {
            $candidates += ,@{ Body = $body; Size = $body.Length }
        }
    }
    if ($candidates.Count -eq 0) { return $null }
    # Largest block wins — most complete code is usually the biggest.
    $best = $candidates | Sort-Object -Property Size -Descending | Select-Object -First 1
    return $best.Body.Trim()
}

function DryRun-Cpp($Meta, [string] $MethodId) {
    $cls    = Get-ClassBareName $Meta
    if (-not $cls) { $cls = 'Target' }
    $bare   = Get-BareName $Meta
    $fixture = "$(Sanitize-CppIdentifier $cls)Test"
    @"
// DryRun placeholder test for $($Meta.method.qualified_name)
// method_id=$MethodId
#include <gtest/gtest.h>
#include <gmock/gmock.h>

TEST($fixture, DryRun_$bare) {
    // Dry-run stub: the real LLM was not invoked.
    EXPECT_TRUE(true);
}

TEST($fixture, DryRun_${bare}_Second) {
    EXPECT_NE(1, 2);
}
"@
}

function Invoke-LlmCli([string] $Prompt, [string] $PromptFile, [string] $OutFile) {
    # Run the LLM CLI with the prompt on stdin; write the full stdout to OutFile.
    if (-not $LlmCmd) { throw 'LlmCmd must be non-empty' }
    Write-Log "invoking LLM: $LlmCmd" 'debug'

    # Use cmd.exe for robust stdin piping on Windows.
    $tmpErr = [System.IO.Path]::GetTempFileName()
    try {
        # cmd /c reads <PromptFile and redirects stdout to $OutFile.
        $cmdLine = "$LlmCmd < `"$PromptFile`" > `"$OutFile`" 2> `"$tmpErr`""
        $proc = Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c', $cmdLine) `
            -NoNewWindow -Wait -PassThru
        if ($proc.ExitCode -ne 0) {
            $errText = (Get-Content -LiteralPath $tmpErr -Raw -ErrorAction SilentlyContinue)
            throw "LLM CLI failed (exit=$($proc.ExitCode)): $errText"
        }
    } finally {
        Remove-Item -LiteralPath $tmpErr -ErrorAction SilentlyContinue
    }
}

function Load-State() {
    if (-not (Test-Path -LiteralPath $StatePath)) {
        return @{ version = 1; methods = @{} }
    }
    try {
        $obj = Get-Content -LiteralPath $StatePath -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        Write-Log "state file corrupt, resetting: $_" 'warn'
        return @{ version = 1; methods = @{} }
    }
    return $obj
}

function Save-StateEntry([string] $MethodId, [hashtable] $Patch) {
    $json = ($Patch | ConvertTo-Json -Depth 10 -Compress)
    # merge_state.py expects a JSON object payload; write it to a temp file to
    # dodge shell quoting issues with embedded errors. We emit UTF-8 WITHOUT a
    # BOM (PowerShell 5.1's `Set-Content -Encoding UTF8` writes a BOM, which
    # trips json.loads).
    $tmp = [System.IO.Path]::GetTempFileName()
    try {
        [System.IO.File]::WriteAllText($tmp, $json, (New-Object System.Text.UTF8Encoding($false)))
        & $Python (Join-Path $LibDir 'merge_state.py') `
            --state $StatePath `
            --method-id $MethodId `
            --patch "@$tmp" | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "merge_state.py exited $LASTEXITCODE"
        }
    } finally {
        Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue
    }
}

function Ensure-CMakeConfigured() {
    if ($NoConfigure) { return }
    $cacheFile = Join-Path $BuildDir 'CMakeCache.txt'
    if (Test-Path -LiteralPath $cacheFile) { return }
    Write-Log "configuring CMake in $BuildDir" 'info'
    $args = @(
        '-S', $TestRoot,
        '-B', $BuildDir,
        "-DMETHODDEP_SOURCE_ROOT=$SourceRoot",
        '-DMETHODDEP_COVERAGE=ON'
    )
    $proc = Start-Process -FilePath $CMake -ArgumentList $args -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput (Join-Path $LogsDir 'cmake-configure.out') `
        -RedirectStandardError  (Join-Path $LogsDir 'cmake-configure.err')
    if ($proc.ExitCode -ne 0) {
        $err = Get-Content -LiteralPath (Join-Path $LogsDir 'cmake-configure.err') -Raw -ErrorAction SilentlyContinue
        throw "cmake configure failed: $err"
    }
}

function Invoke-Build() {
    if ($SkipBuild) {
        Write-Log 'build skipped via -SkipBuild' 'info'
        return @{ ok = $true; log = '' }
    }
    $buildOut = Join-Path $LogsDir 'cmake-build.out'
    $buildErr = Join-Path $LogsDir 'cmake-build.err'
    $args = @('--build', $BuildDir, '--config', $BuildConfig)
    $proc = Start-Process -FilePath $CMake -ArgumentList $args -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput $buildOut `
        -RedirectStandardError  $buildErr
    $outText = (Get-Content -LiteralPath $buildOut -Raw -ErrorAction SilentlyContinue)
    $errText = (Get-Content -LiteralPath $buildErr -Raw -ErrorAction SilentlyContinue)
    return @{
        ok   = ($proc.ExitCode -eq 0)
        log  = ($outText + "`n--- stderr ---`n" + $errText)
    }
}

function Find-TestBinary() {
    $candidates = @(
        (Join-Path $BuildDir ($BuildConfig + '/test_runner.exe')),
        (Join-Path $BuildDir 'test_runner.exe'),
        (Join-Path $BuildDir 'test_runner'),
        (Join-Path $BuildDir ($BuildConfig + '/test_runner'))
    )
    foreach ($c in $candidates) {
        if (Test-Path -LiteralPath $c) { return $c }
    }
    # Last-ditch recursive scan.
    $hit = Get-ChildItem -LiteralPath $BuildDir -Recurse -Filter 'test_runner*' -ErrorAction SilentlyContinue |
           Where-Object { $_.Extension -in @('.exe','') } |
           Select-Object -First 1
    if ($hit) { return $hit.FullName }
    return $null
}

function Resolve-CoverageTool() {
    if ($CoverageTool -ne 'auto') { return $CoverageTool }
    $hasOCC = (Get-Command 'OpenCppCoverage' -ErrorAction SilentlyContinue) -ne $null
    if ($hasOCC) { return 'opencppcoverage' }
    $hasLlvmProf = (Get-Command 'llvm-profdata' -ErrorAction SilentlyContinue) -ne $null
    $hasLlvmCov  = (Get-Command 'llvm-cov' -ErrorAction SilentlyContinue) -ne $null
    if ($hasLlvmProf -and $hasLlvmCov) { return 'llvm' }
    $hasGcov = (Get-Command 'gcov' -ErrorAction SilentlyContinue) -ne $null
    if ($hasGcov) { return 'gcov' }
    return 'none'
}

function Invoke-TestAndCoverage([string] $MethodBareName, [string] $MethodCovDir, [string] $SourceRel) {
    $bin = Find-TestBinary
    if (-not $bin) {
        return @{ ok = $false; stage = 'test-binary'; log = 'test_runner binary not produced' }
    }

    if (-not (Test-Path -LiteralPath $MethodCovDir)) {
        [void] (New-Item -ItemType Directory -Force -Path $MethodCovDir)
    }

    $tool = Resolve-CoverageTool
    Write-Log "using coverage tool: $tool" 'debug'

    $filter = "--gtest_filter=*$(Sanitize-CppIdentifier $MethodBareName)*"
    $testOut = Join-Path $MethodCovDir 'test.out'
    $testErr = Join-Path $MethodCovDir 'test.err'

    switch ($tool) {
        'opencppcoverage' {
            $xmlOut  = Join-Path $MethodCovDir 'cov.xml'
            # Restrict modules to user source root to keep XML small.
            $srcArg  = '--sources=' + (To-Posix $SourceRoot)
            $exclude = @('--excluded_sources=*\build\*','--excluded_sources=*\gen\*','--excluded_sources=*\gtest*')
            $args = @('--quiet', $srcArg) + $exclude + @(
                '--export_type=cobertura:' + $xmlOut,
                '--',
                $bin,
                $filter
            )
            $proc = Start-Process -FilePath 'OpenCppCoverage' -ArgumentList $args `
                -NoNewWindow -Wait -PassThru `
                -RedirectStandardOutput $testOut -RedirectStandardError $testErr
            if ($proc.ExitCode -ne 0) {
                $t = Get-Content -LiteralPath $testOut -Raw -ErrorAction SilentlyContinue
                return @{ ok=$false; stage='test'; log=($t + "`n" + (Get-Content -LiteralPath $testErr -Raw -ErrorAction SilentlyContinue)) }
            }
        }
        'llvm' {
            $env:LLVM_PROFILE_FILE = (Join-Path $MethodCovDir 'default.profraw')
            $proc = Start-Process -FilePath $bin -ArgumentList @($filter) `
                -NoNewWindow -Wait -PassThru `
                -RedirectStandardOutput $testOut -RedirectStandardError $testErr
            Remove-Item env:LLVM_PROFILE_FILE -ErrorAction SilentlyContinue
            if ($proc.ExitCode -ne 0) {
                return @{ ok=$false; stage='test'; log=(Get-Content -LiteralPath $testOut -Raw -ErrorAction SilentlyContinue) }
            }
            # Merge profraw and export JSON coverage.
            $profdata = Join-Path $MethodCovDir 'merged.profdata'
            & llvm-profdata merge -sparse (Join-Path $MethodCovDir 'default.profraw') -o $profdata
            $jsonOut = Join-Path $MethodCovDir 'cov.json'
            & llvm-cov export --format=text --instr-profile=$profdata $bin > $jsonOut
        }
        default {
            # No coverage tool: just run the binary directly.
            $proc = Start-Process -FilePath $bin -ArgumentList @($filter) `
                -NoNewWindow -Wait -PassThru `
                -RedirectStandardOutput $testOut -RedirectStandardError $testErr
            if ($proc.ExitCode -ne 0) {
                return @{ ok=$false; stage='test'; log=(Get-Content -LiteralPath $testOut -Raw -ErrorAction SilentlyContinue) }
            }
        }
    }

    return @{ ok=$true; stage='test'; tool=$tool; log=(Get-Content -LiteralPath $testOut -Raw -ErrorAction SilentlyContinue) }
}

# ------------------- Enumerate method JSONs -------------------

$MethodsRoot = Join-Path $MethoddepOut 'methods'
if (-not (Test-Path -LiteralPath $MethodsRoot)) {
    throw "methods/ directory not found under $MethoddepOut"
}

$allJsons = Get-ChildItem -LiteralPath $MethodsRoot -Recurse -Filter '*.json' -File
Write-Log ("discovered {0} method JSON files" -f $allJsons.Count)

# Optional filters.
if ($OnlyClass -or $OnlyNamespace) {
    $allJsons = $allJsons | Where-Object {
        try {
            $meta = Read-MethodMetadata $_.FullName
        } catch { return $false }
        $cls = ''
        $ns  = ''
        if ($meta.method) {
            if ($meta.method.class) { $cls = $meta.method.class }
            if ($meta.method.namespace) { $ns = $meta.method.namespace }
        }
        $classMatches = -not $OnlyClass     -or ($cls -eq $OnlyClass)     -or ($cls -like "*::$OnlyClass") -or (($cls -split '::')[-1] -eq $OnlyClass)
        $nsMatches    = -not $OnlyNamespace -or ($ns  -eq $OnlyNamespace) -or ($ns -like "*$OnlyNamespace*")
        return $classMatches -and $nsMatches
    }
    Write-Log ("post-filter: {0} JSONs remain" -f $allJsons.Count)
}

# ------------------- Main loop -------------------

# Ensure CMake is configured up front (once).
try { Ensure-CMakeConfigured } catch { Write-Log ("cmake configure failed (continuing without builds): " + $_.Exception.Message) 'warn' }

$stats = @{ pending = 0; passed = 0; failed = 0; gave_up = 0; skipped = 0 }

function Process-One([System.IO.FileInfo] $Json) {
    $methodId = Get-MethodId $Json.FullName
    $state = Load-State
    $entry = $null
    if ($state.methods -and $state.methods.$methodId) {
        $entry = $state.methods.$methodId
    }
    if (-not $entry) { $entry = @{ status='pending'; attempts=0; last_error=$null } }

    if ($entry.status -eq 'passed') {
        Write-Log "[skip passed] $methodId"
        return 'passed'
    }
    if ($entry.status -eq 'gave_up') {
        Write-Log "[skip gave_up] $methodId (already exhausted)"
        return 'gave_up'
    }
    if ([int]($entry.attempts) -ge $MaxIterations) {
        Save-StateEntry -MethodId $methodId -Patch @{ status='gave_up'; attempts=[int]$entry.attempts; last_error=$entry.last_error }
        return 'gave_up'
    }

    $meta = Read-MethodMetadata $Json.FullName
    $cls  = Get-ClassBareName $meta
    $bare = Get-BareName $meta
    $ns   = ''
    if ($meta.method -and $meta.method.namespace) { $ns = $meta.method.namespace }

    # Build gen file path: gen/<ns>/<cls>/<methodId>.cpp (ns/cls optional segments).
    $segments = Get-RelPathSegments $Json.FullName $MethodsRoot
    $relDir = if ($segments.Count) { ($segments -join '/') } else { '_unknown_' }
    $genFileDir = Join-Path $GenDir $relDir
    if (-not (Test-Path -LiteralPath $genFileDir)) { [void] (New-Item -ItemType Directory -Force -Path $genFileDir) }
    $genFile = Join-Path $genFileDir "$methodId.cpp"
    $promptFile = Join-Path $PromptsDir "$methodId.prompt.txt"
    $llmOutFile = Join-Path $PromptsDir "$methodId.response.txt"

    # 1. Build prompt.
    $buildArgs = @(
        (Join-Path $LibDir 'build_prompt.py'),
        '--method-json', $Json.FullName,
        '--source-root', $SourceRoot
    )
    if (Test-Path -LiteralPath $genFile) {
        $buildArgs += @('--previous-test', $genFile)
    }
    if ($entry.last_error) {
        # Pass the error via a temp file to avoid command-line length limits.
        $errFile = Join-Path $PromptsDir "$methodId.prev-error.txt"
        [System.IO.File]::WriteAllText($errFile, [string] $entry.last_error, (New-Object System.Text.UTF8Encoding($false)))
        $buildArgs += @('--previous-error', $errFile)
    }
    if ($PromptTemplate) {
        $buildArgs += @('--prompt-template', $PromptTemplate)
    }

    & $Python @buildArgs > $promptFile 2> (Join-Path $LogsDir 'build_prompt.err')
    if ($LASTEXITCODE -ne 0) {
        Save-StateEntry -MethodId $methodId -Patch @{ status='failed'; attempts=[int]$entry.attempts + 1; last_error='build_prompt.py failed' }
        return 'failed'
    }

    # 2. Invoke the LLM (or dry-run).
    if ($DryRun) {
        $skeleton = DryRun-Cpp $meta $methodId
        Set-Content -LiteralPath $llmOutFile -Value ("``````cpp`n" + $skeleton + "`n``````") -Encoding UTF8
    } else {
        try {
            Invoke-LlmCli -Prompt '' -PromptFile $promptFile -OutFile $llmOutFile
        } catch {
            Save-StateEntry -MethodId $methodId -Patch @{ status='failed'; attempts=[int]$entry.attempts + 1; last_error=("llm-invoke: " + $_.Exception.Message); last_test_path=(To-Posix $genFile) }
            return 'failed'
        }
    }

    # 3. Extract the fenced cpp block.
    $raw = Get-Content -LiteralPath $llmOutFile -Raw -Encoding UTF8
    $cpp = Extract-CppBlock $raw
    if (-not $cpp) {
        Save-StateEntry -MethodId $methodId -Patch @{ status='failed'; attempts=[int]$entry.attempts + 1; last_error='no ```cpp fenced block found in LLM output'; last_test_path=(To-Posix $genFile) }
        return 'failed'
    }
    # UTF-8 no BOM.
    [System.IO.File]::WriteAllText($genFile, $cpp, (New-Object System.Text.UTF8Encoding($false)))
    Write-Log "wrote $genFile ($($cpp.Length) bytes)"

    # 4. Build.
    $buildResult = Invoke-Build
    if (-not $buildResult.ok) {
        $tail = Get-LastLines $buildResult.log 100
        Save-StateEntry -MethodId $methodId -Patch @{ status='failed'; attempts=[int]$entry.attempts + 1; last_error=('build: ' + $tail); last_test_path=(To-Posix $genFile) }
        return 'failed'
    }

    # If the caller opted out of both build and coverage, there is nothing more
    # we can meaningfully verify — mark the generation as passed so the loop
    # doesn't regenerate on the next pass.
    if ($SkipBuild) {
        Save-StateEntry -MethodId $methodId -Patch @{ status='passed'; attempts=[int]$entry.attempts + 1; last_error=$null; last_test_path=(To-Posix $genFile) }
        return 'passed'
    }

    # 5. Run + coverage.
    $methodCov = Join-Path $CovDir $methodId
    $src = ''
    if ($meta.location -and $meta.location.definition -and $meta.location.definition.path) { $src = $meta.location.definition.path }
    $testRes = Invoke-TestAndCoverage -MethodBareName $bare -MethodCovDir $methodCov -SourceRel $src
    if (-not $testRes.ok) {
        $tail = Get-LastLines $testRes.log 80
        Save-StateEntry -MethodId $methodId -Patch @{ status='failed'; attempts=[int]$entry.attempts + 1; last_error=("test: " + $tail); last_test_path=(To-Posix $genFile) }
        return 'failed'
    }

    # 6. Coverage assertion (optional).
    if (-not $SkipCoverage -and $testRes.tool -and $testRes.tool -ne 'none' -and $src) {
        $absSrc = (Join-Path $SourceRoot $src)
        $line = 1
        if ($meta.location -and $meta.location.definition -and $meta.location.definition.line) { $line = [int] $meta.location.definition.line }
        $nloc = 30
        if ($meta.complexity -and $meta.complexity.nloc) { $nloc = [int] $meta.complexity.nloc }
        $covArgs = @(
            (Join-Path $LibDir 'check_coverage.py'),
            '--coverage-dir', $methodCov,
            '--source-file', (To-Posix $absSrc),
            '--line', $line,
            '--after', [Math]::Min($nloc + 5, 200)
        )
        & $Python @covArgs
        $covExit = $LASTEXITCODE
        if ($covExit -eq 1) {
            Save-StateEntry -MethodId $methodId -Patch @{ status='failed'; attempts=[int]$entry.attempts + 1; last_error='coverage: method body not executed (0 hits in definition range)'; last_test_path=(To-Posix $genFile) }
            return 'failed'
        }
        # exit 0 (covered) or 2 (tool missing -> non-blocking) -> continue.
    }

    Save-StateEntry -MethodId $methodId -Patch @{ status='passed'; attempts=[int]$entry.attempts + 1; last_error=$null; last_test_path=(To-Posix $genFile) }
    return 'passed'
}

# ------------------- Dispatch -------------------

foreach ($json in $allJsons) {
    try {
        $result = Process-One $json
    } catch {
        Write-Log ("uncaught exception on $($json.Name): " + $_.Exception.Message) 'error'
        $result = 'failed'
    }
    switch ($result) {
        'passed'  { $stats.passed++ }
        'failed'  { $stats.failed++ }
        'gave_up' { $stats.gave_up++ }
        'pending' { $stats.pending++ }
        default   { $stats.skipped++ }
    }
}

# ------------------- Summary -------------------

Write-Information ''
Write-Information ('==== summary ====')
Write-Information ("passed : {0}" -f $stats.passed)
Write-Information ("failed : {0}" -f $stats.failed)
Write-Information ("gave_up: {0}" -f $stats.gave_up)
Write-Information ("skipped: {0}" -f $stats.skipped)
Write-Information ("state  : {0}" -f $StatePath)

if ($stats.gave_up -gt 0) { exit 2 }
if ($stats.failed  -gt 0) { exit 1 }
exit 0
