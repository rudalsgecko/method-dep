#!/usr/bin/env bash
# ralph_test_from_json.sh — POSIX-compatible port of ralph_test_from_json.ps1.
#
# For each methoddep per-method JSON found under $METHODDEP_OUT/methods/**/*.json:
#   1. build a prompt with scripts/lib/build_prompt.py
#   2. pipe it through $LLM_CMD on stdin, capture stdout
#   3. extract the first ```cpp ... ``` block, write to
#      $TEST_ROOT/gen/<rel-path>/<method_id>.cpp
#   4. cmake --build the test project
#   5. run test_runner --gtest_filter=*<method_bare_name>*
#   6. scripts/lib/check_coverage.py on the result
#   7. persist status via scripts/lib/merge_state.py (atomic, lock-safe)
#
# State lives at $TEST_ROOT/.ralph-state.json. The script is idempotent —
# methods marked passed are skipped on subsequent runs.

set -u
# We intentionally do NOT `set -e` — we want to continue past per-method errors.

# ---------------- Defaults & CLI parsing ----------------

METHODDEP_OUT=""
TEST_ROOT=""
SOURCE_ROOT=""
LLM_CMD="claude -p"
MAX_ITER=3
ONLY_CLASS=""
ONLY_NS=""
DRY_RUN=0
SKIP_BUILD=0
SKIP_COVERAGE=0
NO_CONFIGURE=0
PYTHON="${PYTHON:-python3}"
CMAKE_BIN="${CMAKE:-cmake}"
BUILD_CONFIG="${BUILD_CONFIG:-Debug}"
COVERAGE_TOOL="auto"

usage() {
    cat <<'USAGE'
usage: ralph_test_from_json.sh --methoddep-out DIR --test-root DIR --source-root DIR [options]

required:
  --methoddep-out DIR     directory produced by `methoddep run` (contains methods/)
  --test-root DIR         where generated tests + build + state live
  --source-root DIR       project source root (so #include paths resolve)

options:
  --llm-cmd STR           command that reads prompt on stdin (default: "claude -p")
  --max-iterations N      retry budget per method (default 3)
  --only-class NAME       filter methods by class bare name
  --only-namespace NAME   filter methods by namespace (substring match)
  --dry-run               skip LLM; emit a static skeleton (for pipeline smoke-test)
  --skip-build            do not run cmake --build (implies skip test+coverage)
  --skip-coverage         run tests but do not enforce coverage gate
  --no-configure          assume cmake is already configured under <test-root>/build
  --python BIN            python interpreter (default: python3 / $PYTHON)
  --cmake BIN             cmake binary (default: cmake / $CMAKE)
  --build-config CFG      Debug|Release|etc. (default Debug)
  --coverage-tool TOOL    auto|opencppcoverage|llvm|gcov|none (default auto)
  --prompt-template PATH  path to a prompt .md; default: scripts/templates/prompts/default.md
  -h, --help
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        --methoddep-out)  METHODDEP_OUT="$2"; shift 2 ;;
        --test-root)      TEST_ROOT="$2"; shift 2 ;;
        --source-root)    SOURCE_ROOT="$2"; shift 2 ;;
        --llm-cmd)        LLM_CMD="$2"; shift 2 ;;
        --max-iterations) MAX_ITER="$2"; shift 2 ;;
        --only-class)     ONLY_CLASS="$2"; shift 2 ;;
        --only-namespace) ONLY_NS="$2"; shift 2 ;;
        --dry-run)        DRY_RUN=1; shift ;;
        --skip-build)     SKIP_BUILD=1; shift ;;
        --skip-coverage)  SKIP_COVERAGE=1; shift ;;
        --no-configure)   NO_CONFIGURE=1; shift ;;
        --python)         PYTHON="$2"; shift 2 ;;
        --cmake)          CMAKE_BIN="$2"; shift 2 ;;
        --build-config)   BUILD_CONFIG="$2"; shift 2 ;;
        --coverage-tool)  COVERAGE_TOOL="$2"; shift 2 ;;
        --prompt-template) PROMPT_TEMPLATE="$2"; shift 2 ;;
        --parallel)       shift 2 ;;  # no-op: forward-compat flag, ignored in bash
        -h|--help)        usage; exit 0 ;;
        *)                echo "unknown option: $1" >&2; usage; exit 64 ;;
    esac
done

if [ -z "$METHODDEP_OUT" ] || [ -z "$TEST_ROOT" ] || [ -z "$SOURCE_ROOT" ]; then
    usage; exit 64
fi

# ---------------- Layout ----------------

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LIB_DIR="$SCRIPT_DIR/lib"
TMPL_DIR="$SCRIPT_DIR/templates/test_project"

abspath() {
    # Portable absolute path (no readlink -f on mac/bsd).
    local p="$1"
    if [ -d "$p" ]; then ( cd "$p" && pwd ); return; fi
    local d
    d="$(dirname "$p")"
    if [ -d "$d" ]; then echo "$(cd "$d" && pwd)/$(basename "$p")"; return; fi
    echo "$p"
}

METHODDEP_OUT="$(abspath "$METHODDEP_OUT")"
TEST_ROOT="$(abspath "$TEST_ROOT")"
SOURCE_ROOT="$(abspath "$SOURCE_ROOT")"

METHODS_ROOT="$METHODDEP_OUT/methods"
GEN_DIR="$TEST_ROOT/gen"
BUILD_DIR="$TEST_ROOT/build"
COV_DIR="$TEST_ROOT/coverage"
LOGS_DIR="$TEST_ROOT/logs"
PROMPTS_DIR="$TEST_ROOT/prompts"
STATE_PATH="$TEST_ROOT/.ralph-state.json"

[ -d "$METHODS_ROOT" ] || { echo "methods/ not found under $METHODDEP_OUT" >&2; exit 2; }
mkdir -p "$TEST_ROOT" "$GEN_DIR" "$BUILD_DIR" "$COV_DIR" "$LOGS_DIR" "$PROMPTS_DIR"

# Copy template files once (preserve user edits).
for name in CMakeLists.txt main.cpp; do
    if [ ! -f "$TEST_ROOT/$name" ]; then
        cp "$TMPL_DIR/$name" "$TEST_ROOT/$name"
    fi
done

log() {
    local level="${2:-info}"
    local line
    line="$(date '+%Y-%m-%dT%H:%M:%S')|$level|$1"
    echo "$line"
    echo "$line" >> "$LOGS_DIR/ralph.log"
}

to_posix() { echo "$1" | tr '\\' '/'; }

# ---------------- Helpers (python one-liners keep the logic tight) ----------------

method_id_of() { basename "$1" .json; }

# Return the tail of a string, last N lines. Reads stdin, prints tail to stdout.
tail_n() {
    local n="$1"
    awk -v n="$n" 'BEGIN{count=0} {lines[NR]=$0; count=NR} END{ start = (count>n)?count-n+1:1; if(count>n) printf "... (%d earlier lines truncated)\n", count-n; for(i=start;i<=count;i++) print lines[i]}'
}

# Read method metadata as shell-friendly KEY=VALUE pairs via python.
# Exposed vars: MD_QUALIFIED, MD_CLASS, MD_NS, MD_BARE, MD_DEF_PATH, MD_DEF_LINE, MD_NLOC
read_meta() {
    local json="$1"
    # shellcheck disable=SC2016
    local script='
import json, sys
with open(sys.argv[1], "r", encoding="utf-8") as f:
    d = json.load(f)
m = d.get("method") or {}
loc = (d.get("location") or {}).get("definition") or {}
cplx = d.get("complexity") or {}
qn = m.get("qualified_name") or ""
cls = m.get("class") or ""
ns = m.get("namespace") or ""
bare = qn.rsplit("::", 1)[-1] if qn else ""
print("MD_QUALIFIED=%s" % json.dumps(qn))
print("MD_CLASS=%s" % json.dumps(cls))
print("MD_NS=%s" % json.dumps(ns))
print("MD_BARE=%s" % json.dumps(bare))
print("MD_DEF_PATH=%s" % json.dumps(loc.get("path") or ""))
print("MD_DEF_LINE=%d" % int(loc.get("line") or 0))
print("MD_NLOC=%d" % int(cplx.get("nloc") or 30))
'
    "$PYTHON" -c "$script" "$json"
}

sanitize_cpp_identifier() {
    local s
    s=$(printf '%s' "$1" | "$PYTHON" -c 'import sys,re; s=sys.stdin.read(); s=re.sub(r"<[^>]*>","",s); s=re.sub(r"[^A-Za-z0-9_]","_",s); print(s or "Global", end="")')
    [ -z "$s" ] && s="Global"
    echo "$s"
}

dryrun_cpp() {
    local cls_bare="$1"
    local method_bare="$2"
    local method_id="$3"
    local qualified="$4"
    local sanitized_cls
    sanitized_cls="$(sanitize_cpp_identifier "${cls_bare:-Target}")"
    local fixture="${sanitized_cls}Test"
    cat <<EOF
// DryRun placeholder test for ${qualified}
// method_id=${method_id}
#include <gtest/gtest.h>
#include <gmock/gmock.h>

TEST(${fixture}, DryRun_${method_bare}) {
    // Dry-run stub: the real LLM was not invoked.
    EXPECT_TRUE(true);
}

TEST(${fixture}, DryRun_${method_bare}_Second) {
    EXPECT_NE(1, 2);
}
EOF
}

extract_cpp_block() {
    # Arg 1: path to file containing the raw LLM output. Writes the largest
    # matching cpp fenced block body to stdout. Exits 0/1 on match/no-match.
    "$PYTHON" -c "
import re, sys
text = open(sys.argv[1], 'r', encoding='utf-8').read()
candidates = []
for m in re.finditer(r'\`\`\`(?P<lang>[A-Za-z+]*)\s*\r?\n(?P<body>.*?)\`\`\`', text, re.DOTALL):
    lang = m.group('lang').lower()
    body = m.group('body')
    has_cpp_marker = ('#include' in body or 'TEST_F' in body or 'TEST(' in body or 'namespace' in body)
    if lang in ('cpp', 'c++', 'cxx') or has_cpp_marker:
        candidates.append(body)
if not candidates:
    sys.exit(1)
best = max(candidates, key=len)
sys.stdout.write(best.strip())
sys.exit(0)
" "$1"
}

patch_state() {
    # args: method_id, JSON patch string
    local mid="$1"
    local patch="$2"
    local tmp
    tmp="$(mktemp)"
    printf '%s' "$patch" > "$tmp"
    "$PYTHON" "$LIB_DIR/merge_state.py" --state "$STATE_PATH" --method-id "$mid" --patch "@$tmp" >/dev/null
    local rc=$?
    rm -f "$tmp"
    return $rc
}

get_entry_field() {
    # args: method_id, field
    "$PYTHON" - <<PY
import json, sys
try:
    d = json.load(open(r"$STATE_PATH", encoding="utf-8-sig"))
except Exception:
    sys.exit(0)
e = (d.get("methods") or {}).get("$1") or {}
v = e.get("$2")
if v is None:
    sys.exit(0)
print(v)
PY
}

match_filter() {
    # args: class, namespace  — return 0 to keep, 1 to drop.
    local cls="$1" ns="$2"
    if [ -n "$ONLY_CLASS" ]; then
        local bare="${cls##*::}"
        if [ "$cls" != "$ONLY_CLASS" ] && [ "$bare" != "$ONLY_CLASS" ]; then
            return 1
        fi
    fi
    if [ -n "$ONLY_NS" ]; then
        case "$ns" in
            *"$ONLY_NS"*) ;;
            *) return 1 ;;
        esac
    fi
    return 0
}

resolve_cov_tool() {
    if [ "$COVERAGE_TOOL" != "auto" ]; then echo "$COVERAGE_TOOL"; return; fi
    if command -v OpenCppCoverage >/dev/null 2>&1; then echo "opencppcoverage"; return; fi
    if command -v llvm-profdata >/dev/null 2>&1 && command -v llvm-cov >/dev/null 2>&1; then echo "llvm"; return; fi
    if command -v gcov >/dev/null 2>&1; then echo "gcov"; return; fi
    echo "none"
}

find_test_binary() {
    local c
    for c in "$BUILD_DIR/$BUILD_CONFIG/test_runner.exe" \
             "$BUILD_DIR/test_runner.exe" \
             "$BUILD_DIR/test_runner" \
             "$BUILD_DIR/$BUILD_CONFIG/test_runner"; do
        if [ -x "$c" ] || [ -f "$c" ]; then echo "$c"; return; fi
    done
    find "$BUILD_DIR" -maxdepth 3 -name 'test_runner*' -type f 2>/dev/null | head -1
}

ensure_cmake_configured() {
    if [ "$NO_CONFIGURE" -eq 1 ]; then return 0; fi
    if [ -f "$BUILD_DIR/CMakeCache.txt" ]; then return 0; fi
    log "configuring cmake in $BUILD_DIR"
    "$CMAKE_BIN" -S "$TEST_ROOT" -B "$BUILD_DIR" \
        -DMETHODDEP_SOURCE_ROOT="$SOURCE_ROOT" \
        -DMETHODDEP_COVERAGE=ON \
        > "$LOGS_DIR/cmake-configure.out" 2> "$LOGS_DIR/cmake-configure.err"
    local rc=$?
    if [ $rc -ne 0 ]; then
        log "cmake configure failed (rc=$rc); see $LOGS_DIR/cmake-configure.err" warn
    fi
    return $rc
}

invoke_build() {
    "$CMAKE_BIN" --build "$BUILD_DIR" --config "$BUILD_CONFIG" \
        > "$LOGS_DIR/cmake-build.out" 2> "$LOGS_DIR/cmake-build.err"
    return $?
}

# ---------------- Main loop ----------------

ensure_cmake_configured || true

# Collect every JSON file (portable, handles spaces in paths).
TOTAL=0
FILTERED=0
PASSED=0; FAILED=0; GAVE_UP=0; SKIPPED=0

process_one() {
    local json="$1"
    local mid
    mid="$(method_id_of "$json")"
    local status attempts last_error
    status="$(get_entry_field "$mid" status)"
    attempts="$(get_entry_field "$mid" attempts)"; attempts="${attempts:-0}"
    last_error="$(get_entry_field "$mid" last_error)"

    case "$status" in
        passed)  log "[skip passed] $mid"; return 3 ;;
        gave_up) log "[skip gave_up] $mid"; return 4 ;;
    esac

    if [ "$attempts" -ge "$MAX_ITER" ]; then
        patch_state "$mid" "{\"status\":\"gave_up\",\"attempts\":$attempts}"
        return 4
    fi

    # Extract metadata.
    eval "$(read_meta "$json")"
    match_filter "$MD_CLASS" "$MD_NS" || return 5

    # Derive gen file path.
    local rel_dir
    rel_dir="$(dirname "${json#$METHODS_ROOT/}")"
    [ "$rel_dir" = "." ] && rel_dir="_unknown_"
    local gen_file_dir="$GEN_DIR/$rel_dir"
    mkdir -p "$gen_file_dir"
    local gen_file="$gen_file_dir/$mid.cpp"
    local prompt_file="$PROMPTS_DIR/$mid.prompt.txt"
    local llm_out_file="$PROMPTS_DIR/$mid.response.txt"

    # 1. Build prompt.
    local extra_args=()
    [ -f "$gen_file" ] && extra_args+=(--previous-test "$gen_file")
    if [ -n "$last_error" ]; then
        local err_file="$PROMPTS_DIR/$mid.prev-error.txt"
        printf '%s' "$last_error" > "$err_file"
        extra_args+=(--previous-error "$err_file")
    fi
    if [ -n "${PROMPT_TEMPLATE:-}" ]; then
        extra_args+=(--prompt-template "$PROMPT_TEMPLATE")
    fi
    if ! "$PYTHON" "$LIB_DIR/build_prompt.py" \
            --method-json "$json" \
            --source-root "$SOURCE_ROOT" \
            "${extra_args[@]}" > "$prompt_file" 2> "$LOGS_DIR/build_prompt.err"; then
        patch_state "$mid" "{\"status\":\"failed\",\"attempts\":$((attempts+1)),\"last_error\":\"build_prompt.py failed\"}"
        return 2
    fi

    # 2. Invoke LLM (or dry-run).
    if [ "$DRY_RUN" -eq 1 ]; then
        {
            echo '```cpp'
            dryrun_cpp "${MD_CLASS##*::}" "$MD_BARE" "$mid" "$MD_QUALIFIED"
            echo '```'
        } > "$llm_out_file"
    else
        if ! bash -c "$LLM_CMD" < "$prompt_file" > "$llm_out_file" 2> "$LOGS_DIR/llm.$mid.err"; then
            local msg
            msg="$(tail_n 80 < "$LOGS_DIR/llm.$mid.err" | "$PYTHON" -c 'import sys,json; sys.stdout.write(json.dumps(sys.stdin.read()))')"
            patch_state "$mid" "{\"status\":\"failed\",\"attempts\":$((attempts+1)),\"last_error\":$msg}"
            return 2
        fi
    fi

    # 3. Extract cpp block.
    if ! extract_cpp_block "$llm_out_file" > "$gen_file.tmp"; then
        patch_state "$mid" "{\"status\":\"failed\",\"attempts\":$((attempts+1)),\"last_error\":\"no cpp fenced block in LLM output\"}"
        rm -f "$gen_file.tmp"
        return 2
    fi
    mv "$gen_file.tmp" "$gen_file"
    log "wrote $gen_file"

    # 4. Build.
    if [ "$SKIP_BUILD" -eq 0 ]; then
        if ! invoke_build; then
            local msg
            msg="$( { cat "$LOGS_DIR/cmake-build.out"; echo '--- stderr ---'; cat "$LOGS_DIR/cmake-build.err"; } | tail_n 100 | "$PYTHON" -c 'import sys,json; sys.stdout.write(json.dumps(sys.stdin.read()))')"
            patch_state "$mid" "{\"status\":\"failed\",\"attempts\":$((attempts+1)),\"last_error\":$msg}"
            return 2
        fi
    fi

    # Skip-build short-circuit: treat as passed since there is nothing further to verify.
    if [ "$SKIP_BUILD" -eq 1 ]; then
        patch_state "$mid" "{\"status\":\"passed\",\"attempts\":$((attempts+1)),\"last_error\":null,\"last_test_path\":\"$(to_posix "$gen_file")\"}"
        return 0
    fi

    # 5. Run + coverage.
    local bin
    bin="$(find_test_binary)"
    if [ -z "$bin" ]; then
        patch_state "$mid" "{\"status\":\"failed\",\"attempts\":$((attempts+1)),\"last_error\":\"test_runner binary not produced\"}"
        return 2
    fi

    local method_cov="$COV_DIR/$mid"
    mkdir -p "$method_cov"
    local cov_tool
    cov_tool="$(resolve_cov_tool)"
    local test_out="$method_cov/test.out"
    local test_err="$method_cov/test.err"
    local _bare_sanitized
    _bare_sanitized="$(sanitize_cpp_identifier "$MD_BARE")"
    local filter="--gtest_filter=*${_bare_sanitized}*"

    local test_rc=0
    case "$cov_tool" in
        opencppcoverage)
            OpenCppCoverage --quiet \
                --sources="$(to_posix "$SOURCE_ROOT")" \
                --excluded_sources='*\build\*' \
                --excluded_sources='*\gen\*' \
                --excluded_sources='*\gtest*' \
                --export_type="cobertura:$method_cov/cov.xml" \
                -- "$bin" "$filter" > "$test_out" 2> "$test_err"
            test_rc=$?
            ;;
        llvm)
            LLVM_PROFILE_FILE="$method_cov/default.profraw" "$bin" "$filter" \
                > "$test_out" 2> "$test_err"
            test_rc=$?
            if [ $test_rc -eq 0 ]; then
                llvm-profdata merge -sparse "$method_cov/default.profraw" -o "$method_cov/merged.profdata" 2>>"$test_err"
                llvm-cov export --format=text --instr-profile="$method_cov/merged.profdata" "$bin" > "$method_cov/cov.json" 2>>"$test_err"
            fi
            ;;
        *)
            "$bin" "$filter" > "$test_out" 2> "$test_err"
            test_rc=$?
            ;;
    esac

    if [ $test_rc -ne 0 ]; then
        local msg
        msg="$(tail_n 80 < "$test_out" | "$PYTHON" -c 'import sys,json; sys.stdout.write(json.dumps(sys.stdin.read()))')"
        patch_state "$mid" "{\"status\":\"failed\",\"attempts\":$((attempts+1)),\"last_error\":$msg}"
        return 2
    fi

    # 6. Coverage gate.
    if [ "$SKIP_COVERAGE" -eq 0 ] && [ "$cov_tool" != "none" ] && [ -n "$MD_DEF_PATH" ]; then
        local abs_src="$SOURCE_ROOT/$MD_DEF_PATH"
        local line_after=$((MD_NLOC + 5)); [ $line_after -gt 200 ] && line_after=200
        "$PYTHON" "$LIB_DIR/check_coverage.py" \
            --coverage-dir "$method_cov" \
            --source-file "$(to_posix "$abs_src")" \
            --line "$MD_DEF_LINE" \
            --after "$line_after"
        local cov_rc=$?
        if [ $cov_rc -eq 1 ]; then
            patch_state "$mid" "{\"status\":\"failed\",\"attempts\":$((attempts+1)),\"last_error\":\"coverage: method body not executed (0 hits in definition range)\"}"
            return 2
        fi
        # rc 2 (tool missing) is non-blocking.
    fi

    patch_state "$mid" "{\"status\":\"passed\",\"attempts\":$((attempts+1)),\"last_error\":null,\"last_test_path\":\"$(to_posix "$gen_file")\"}"
    return 0
}

# Collect and iterate.
while IFS= read -r -d '' json; do
    TOTAL=$((TOTAL+1))
    # Quick filter read to bail before the heavy lifting.
    if [ -n "$ONLY_CLASS" ] || [ -n "$ONLY_NS" ]; then
        eval "$(read_meta "$json")"
        match_filter "$MD_CLASS" "$MD_NS" || continue
    fi
    FILTERED=$((FILTERED+1))
    process_one "$json"
    case $? in
        0) PASSED=$((PASSED+1)) ;;
        2) FAILED=$((FAILED+1)) ;;
        3) PASSED=$((PASSED+1)) ;;   # already passed on earlier run
        4) GAVE_UP=$((GAVE_UP+1)) ;;
        5) SKIPPED=$((SKIPPED+1)) ;;
        *) FAILED=$((FAILED+1)) ;;
    esac
done < <(find "$METHODS_ROOT" -type f -name '*.json' -print0)

log "discovered $TOTAL method JSONs (filtered to $FILTERED)"
echo ""
echo "==== summary ===="
echo "passed : $PASSED"
echo "failed : $FAILED"
echo "gave_up: $GAVE_UP"
echo "skipped: $SKIPPED"
echo "state  : $STATE_PATH"

if [ $GAVE_UP -gt 0 ]; then exit 2; fi
if [ $FAILED  -gt 0 ]; then exit 1; fi
exit 0
