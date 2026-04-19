# scripts/ — Ralph-style LLM test generation loop

This directory contains a ralph-style loop that reads the per-method JSON
produced by `methoddep run` and drives an LLM CLI through:

```
generate prompt -> LLM -> extract cpp block -> cmake --build -> run gtest
                          -> check coverage of definition line -> retry on failure
```

Two entry points (same behaviour):

| Platform   | Script                          |
|------------|---------------------------------|
| Windows    | `ralph_test_from_json.ps1`      |
| POSIX/bash | `ralph_test_from_json.sh`       |

Shared helpers live in [`lib/`](lib/) and a minimal gtest/gmock CMake template
lives in [`templates/test_project/`](templates/test_project/).

## Prerequisites

- Python 3.11+ (`python --version`).
- CMake 3.20+ (for the first real build — not required for `--dry-run`).
- A C++20 compiler (MSVC 19.3+, clang-cl 16+, or GCC 13+ for the POSIX path).
- Optional coverage tool, auto-detected:
  - **OpenCppCoverage** on Windows (`choco install OpenCppCoverage`). Emits
    Cobertura XML.
  - **llvm-profdata + llvm-cov** when you build with clang / clang-cl.
  - **gcov** when you build with GCC.
  - If none are present the coverage gate becomes a warning, not a failure
    (the loop logs it and continues).
- An LLM CLI that reads the prompt from **stdin** and writes the response to
  **stdout**. Examples: `claude -p`, `opencode run --stdin`,
  `curl -s -X POST ... -d @-`.

## Quickstart (Windows)

```powershell
pwsh scripts/ralph_test_from_json.ps1 `
    -MethoddepOut "D:/proj/PowerToys-methoddep-out/fancyzones" `
    -TestRoot     "D:/proj/PowerToys-methoddep-tests" `
    -SourceRoot   "D:/proj/PowerToys" `
    -LlmCmd       "claude -p" `
    -MaxIterations 3 `
    -OnlyClass    "FancyZones"
```

## Quickstart (POSIX / git-bash)

```bash
bash scripts/ralph_test_from_json.sh \
    --methoddep-out D:/proj/PowerToys-methoddep-out/fancyzones \
    --test-root     D:/proj/PowerToys-methoddep-tests \
    --source-root   D:/proj/PowerToys \
    --llm-cmd       "claude -p" \
    --max-iterations 3 \
    --only-class    FancyZones
```

## What ends up where

```
<TestRoot>/
├── CMakeLists.txt            # copied from templates/test_project, not overwritten
├── main.cpp
├── gen/<namespace>/<class>/<sha1>.cpp   # generated gtest files, one per method
├── prompts/<sha1>.prompt.txt            # exact prompt fed to the LLM
├── prompts/<sha1>.response.txt          # raw LLM output (before extraction)
├── prompts/<sha1>.prev-error.txt        # last-attempt error passed back in
├── build/                               # cmake build tree
├── coverage/<sha1>/                     # OpenCppCoverage / llvm-cov reports
├── logs/                                # cmake + llm stderr
└── .ralph-state.json                    # machine-readable status per method
```

### `.ralph-state.json` schema

```jsonc
{
  "version": 1,
  "methods": {
    "13e2fef68fe16f37c16e52af5ba46c8d5d25188b": {
      "status":          "passed | failed | gave_up | pending",
      "attempts":        2,
      "last_error":      "build stderr tail / coverage miss / extractor miss",
      "last_test_path":  "gen/_global_/WorkAreaConfiguration/13e2fef....cpp",
      "last_updated":    "2026-04-19T12:34:56+00:00"
    }
  }
}
```

All writes go through `lib/merge_state.py`, which takes an exclusive file
lock before reading-and-rewriting. Concurrent jobs cannot lose updates.

## Filtering

Both scripts accept filter flags that read the method metadata and skip
non-matching JSONs up front:

| PowerShell             | bash                    | effect                              |
|------------------------|-------------------------|-------------------------------------|
| `-OnlyClass NAME`      | `--only-class NAME`     | keep methods whose class bare-name equals NAME |
| `-OnlyNamespace NAME`  | `--only-namespace NAME` | substring match on the method namespace |

Filters compose (AND).

## Dry-run mode

`-DryRun` / `--dry-run` skips the LLM entirely. Instead of calling the CLI the
loop writes a hardcoded GoogleTest skeleton that compiles against a trivial
fixture. This is the right way to exercise the generate -> build -> run
-> coverage plumbing on a new machine before hooking up a real model.

A typical first-run smoke test:

```powershell
pwsh scripts/ralph_test_from_json.ps1 `
    -DryRun `
    -MethoddepOut D:/proj/PowerToys-methoddep-out/fancyzones `
    -TestRoot     D:/tmp/rt `
    -SourceRoot   D:/proj/PowerToys `
    -MaxIterations 1 `
    -OnlyClass    WorkAreaConfiguration `
    -LlmCmd       "echo" `
    -SkipBuild -SkipCoverage -NoConfigure
```

Expected outcome: 6/6 methods pass, `.ralph-state.json` is created, and six
`.cpp` skeletons land under `D:/tmp/rt/gen/_global_/WorkAreaConfiguration/`.

## Prompt authoring

`lib/build_prompt.py` composes the prompt from:

1. The full methoddep per-method JSON (pretty-printed).
2. A 30-line source snippet around the definition line.
3. A flat bullet list of dependency headers (with `used_methods` where known).
4. The call-graph edges recorded by methoddep.
5. Specifier hints (`static`, `const`, `noexcept`, `override` / `virtual`).
6. The previous failing test (if any) and the last error tail.
7. Explicit output requirements: single ```cpp fenced block, fixture name,
   minimum number of `TEST_F`s (capped at max(2, cyclomatic)).

Run it standalone to inspect what gets sent:

```bash
python scripts/lib/build_prompt.py \
    --method-json D:/proj/PowerToys-methoddep-out/fancyzones/methods/_global_/WorkAreaConfiguration/13e2fef68fe16f37c16e52af5ba46c8d5d25188b.json \
    --source-root D:/proj/PowerToys
```

## Coverage verification

`lib/check_coverage.py` accepts any directory and auto-detects the report
format:

| Tool             | Output | Auto-detected via                         |
|------------------|--------|-------------------------------------------|
| OpenCppCoverage  | Cobertura XML (`*.xml`) | `class filename=` nodes           |
| llvm-cov export  | LLVM JSON (`*.json`)    | `data.files.segments[]`           |
| gcov (gcc/llvm)  | `<basename>.gcov` files | scan `<coverage-dir>` recursively |

Exit codes: `0` covered, `1` report found but zero hits on the target range,
`2` no report found (non-blocking — the loop logs a warning and treats the
method as passed for that iteration).

## CMake template

`templates/test_project/CMakeLists.txt` pulls GoogleTest v1.14 via
`FetchContent`, globs every `gen/**/*.cpp` into `test_runner`, and flips
coverage flags when `METHODDEP_COVERAGE=ON`:

- Clang / clang-cl: `-fprofile-instr-generate -fcoverage-mapping`.
- GCC: `--coverage`.
- MSVC: `/Od /Zi` only — the runtime step is then wrapped with
  OpenCppCoverage, which supplies the instrumentation.

Pass additional include directories and extra project sources via:

```
-DMETHODDEP_SOURCE_ROOT=D:/proj/PowerToys
-DMETHODDEP_EXTRA_INCLUDES="C:/sdk/include;D:/proj/PowerToys/src/common"
-DMETHODDEP_COMPILE_SOURCES="D:/proj/PowerToys/src/.../Foo.cpp"
```

## Parallelism

`-Parallel N` is accepted on the PowerShell entry point for forward
compatibility; v1 runs methods serially. The state file is already
lock-safe (see `lib/merge_state.py`), so flipping the loop to
`Start-Job`/`ForEach-Object -Parallel` only requires dispatching the
`Process-One` function — no changes needed to the writers.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `no cpp fenced block in LLM output` | Model returned plain text; tighten system prompt or raise temperature floor. The raw response is preserved under `prompts/<id>.response.txt` for inspection. |
| `build: <compiler errors>` | Generated test includes a header the build can't find. Add to `METHODDEP_EXTRA_INCLUDES` or supply `METHODDEP_COMPILE_SOURCES`. The build tail is fed back into the next prompt automatically. |
| `coverage: method body not executed` | Generated test passed but never called the target. Raise `MaxIterations`; the previous test + error are included in the retry prompt. |
| `warn: no coverage report found` | No OpenCppCoverage / llvm-cov / gcov on PATH. Install one or pass `-SkipCoverage` for now. |
| `merge_state.py exited 1` | The patch JSON was written with a BOM. The helper strips BOMs on read, but if you see this in custom wrappers, emit UTF-8 without BOM (`UTF8Encoding($false)` in .NET, `encoding='utf-8'` in Python). |

## Testing the scripts themselves

The pytest suite under `tests/` still drives methoddep proper and is
untouched by this directory. Run it with:

```bash
pytest                       # full suite
pytest tests/unit/ -q        # unit only
```

The ralph loop is a separate layer and does not ship with pytest coverage
at this time; the `--dry-run` path is the fastest smoke test.
