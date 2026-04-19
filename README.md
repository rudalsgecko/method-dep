# methoddep

C++/MSVC 프로젝트의 **메소드별 메타데이터**(시그니처·의존성·복잡도·호출 그래프·모크 위치)를 JSON으로 추출하는 도구. Sonnet-급 모델이 GoogleTest/GoogleMock unit test를 **최소 프롬프트 컨텍스트**로 작성하도록 돕는 사전정보 집약기.

## 왜 필요한가

```cpp
// FancyZones.cpp — 실제 프로젝트 예
bool Bar::doWork(Config const& cfg, Input* in) {
    if (!in) return false;
    if (!svc_.fetch(cfg.tag)) return false;
    svc_.commit(in->value + cfg.id);
    return true;
}
```

이 메소드 하나 테스트 작성을 LLM에게 시키려면:
- 클래스 헤더
- `IService` 인터페이스 (가상 메서드)
- `Config`, `Input` 구조체
- `MockIService`의 위치
- 연관 호출 그래프

…를 전부 프롬프트에 붙여야 함. 100KB 헤더 덩어리를 넣는 대신 **methoddep이 사실(fact)만 뽑은 ~30줄 JSON** 하나로 대체.

## 한눈에 보는 전체 흐름

```
   [C++ 소스]                                  ← 네 프로젝트
        │
        │ (Stage 1) methoddep run
        ▼
   [메소드당 JSON + index.json]                 ← 소스 재파싱 없이 재사용
        │
        │ (Stage 2) ralph_test_from_json.ps1
        │    ├── 프롬프트 조립 (템플릿 + JSON)
        │    ├── LLM CLI (Claude Code / opencode) → .cpp
        │    ├── CMake + GoogleTest 빌드
        │    ├── 메소드만 필터로 실행
        │    └── 커버리지로 실제 실행 검증
        ▼
   [테스트 .cpp + pass/fail/skip 상태]         ← 실패 메소드만 다음 iteration에 재시도
```

### Stage 1 — JSON 수확 (`methoddep run`)
C++ 소스 → 메소드별 JSON. libclang + tree-sitter + lizard로 signature·복잡도·의존성·호출 그래프 추출.

```bash
cd D:/proj/MyProject
methoddep init                                           # 최초 1회
# methoddep.toml 편집 (repo_root / scope_root / include_dirs)
methoddep run --config methoddep.toml --customer main
```
→ `out/main/methods/**/*.json` 생성.

소스가 바뀌면 이 단계만 다시 돌려 JSON 갱신. 시그니처가 바뀐 메소드는 `id` 해시가 달라져 자동으로 "새 메소드" 취급.

### Stage 2 — 테스트 자동 생성 + 검증 (`ralph_test_from_json.ps1`)
Stage 1이 만든 JSON을 하나씩 LLM에 먹이고 실제로 **빌드 + 실행 + 커버리지 검증**까지.

```powershell
pwsh scripts/ralph_test_from_json.ps1 `
    -MethoddepOut "out/main" `
    -TestRoot     "test-harness" `
    -SourceRoot   "D:/proj/MyProject" `
    -LlmCmd       "claude -p" `
    -MaxIterations 3
```
→ `test-harness/gen/**/*.cpp` 테스트 파일 + `.ralph-state.json` (per-method 상태).

재실행 시 이미 passed된 메소드는 skip, failed는 에러 피드백과 함께 재시도. 한 클래스만 빠르게 돌리려면 `-OnlyClass MyClass`.

**요약**: 소스 → [Stage 1] → JSON → [Stage 2] → 검증된 테스트 .cpp

아래 섹션에서 각 단계 세부 설정과 플래그를 설명.

## 설치

```bash
cd method-dep2
pip install -e .[dev]
methoddep doctor              # 외부 툴 체크
```

외부 의존성:
| 툴 | 필수 | 설치 |
|---|---|---|
| **LLVM libclang** | ✅ | `choco install llvm` 또는 pip `libclang` (번들) |
| **lizard** | ✅ | pip (자동) |
| **tree-sitter + tree-sitter-cpp** | ✅ | pip (자동) |
| **Universal Ctags** | ⚪ | `choco install universal-ctags` — 위치 교차검증용 |
| **MSBuild** | ⚪ | VS Build Tools — binlog 경로 쓸 때만 |

## Workflow — 새 프로젝트에 적용

### 0단계 — 도구 상태 점검
```bash
methoddep doctor
```

### 1단계 — `methoddep.toml` 스캐폴드
```bash
cd D:/proj/MyProject
methoddep init
```

### 2단계 — `methoddep.toml` 편집

**최소 설정** (단일 타겟 프로젝트):
```toml
[target]
repo_root  = "D:/proj/MyProject/src/modules/foo"
# scope_root: 이 경로 밖에서 선언된 타입(Win32 SDK, STL, 서드파티)은
# emit에서 전부 필터링됨. 생략 시 repo_root.
scope_root = "D:/proj/MyProject"

[workspace]
strategy = "in-place"           # 단일 변종 — 워크스페이스 복제 없음

[customers.main]
branches = []

[analysis]
include_dirs = [
    "include",                                # repo_root 상대경로
    "D:/proj/MyProject/src/common",           # 또는 절대경로
]
defines      = ["_WIN32", "UNICODE", "_MSC_VER=1939"]
clang_flags  = ["-fms-extensions", "-fms-compatibility",
                "-std=c++20", "-target", "x86_64-pc-windows-msvc"]

[test]
mock_dirs          = ["tests/mocks"]
mock_name_patterns = ["Mock{Class}", "{Class}Mock", "Fake{Class}"]

[output]
dir = "D:/proj/MyProject-methoddep-out"
```

### 3단계 (선택) — binlog로 설정 자동 주입

평소 빌드 커맨드에 `/bl:` 한 줄 추가:
```bash
msbuild MyProject.sln /bl:artifacts/msbuild.binlog
```

그리고 config에 추가:
```toml
[build_intel]
enabled = true
mode    = "cached-only"            # 기본. methoddep은 빌드 안 함
binlog  = "artifacts/msbuild.binlog"
```

→ 다음 실행부터 `/I` `/D` `/Yu` 가 binlog에서 **자동 추출** 되어 libclang에 주입. 수동 `include_dirs`/`defines` 유지 불필요.

### 4단계 — 실행
```bash
methoddep run --config methoddep.toml --customer main
```

출력:
```
customer=main strategy=in-place methods=342 index=...\main\index.json
  warn: ... (있으면)
```

### 5단계 — LLM에게 먹이기

단일 메소드 테스트 작성:
```
# 해당 메소드 JSON 복사
cat out/main/methods/foo/Bar/a1b2c3....json

# Sonnet에게:
"다음 메소드의 GoogleTest + GoogleMock unit test를 작성해줘. 이 JSON에 담긴 정보만 사용.
<JSON 내용 붙여넣기>"
```

클래스 전체 메소드:
```bash
jq '.by_class."foo::Bar"' out/main/index.json
# → ["a1b2c3...", "d4e5f6...", ...]
# 각 ID 파일을 concat해서 프롬프트
```

### 6단계 (선택) — 자동 생성 루프 (`scripts/ralph_test_from_json.ps1`)

JSON을 하나씩 수동으로 복사해서 LLM에 붙일 필요 없이, **전체 메소드 집합을 자동으로 순회하면서 테스트 생성 → 빌드 → 실행 → 커버리지 검증까지 돌리는 ralph-style loop** 제공. 실패한 메소드는 에러 tail을 다음 iteration 프롬프트에 포함해서 재시도.

```powershell
pwsh scripts/ralph_test_from_json.ps1 `
    -MethoddepOut "D:/proj/MyProject-methoddep-out/main" `
    -TestRoot     "D:/proj/MyProject-methoddep-tests" `
    -SourceRoot   "D:/proj/MyProject" `
    -LlmCmd       "claude -p" `
    -MaxIterations 3 `
    -OnlyClass    "MyClass"      # 선택: 특정 클래스만
```

bash 포팅도 있음 (`ralph_test_from_json.sh`, git-bash/Linux).

동작:
1. methoddep JSON 순회 → 프롬프트 조립 (JSON + 소스 스니펫 ±20줄 + 이전 실패 tail)
2. `-LlmCmd` stdin으로 파이프 (Claude Code, opencode, 기타 CLI 호환)
3. 응답에서 가장 큰 ```cpp 블록 추출 → `gen/<ns>/<class>/<id>.cpp`
4. CMake + GoogleTest 빌드 → 실패 시 에러 tail을 다음 iteration 프롬프트로 피드백
5. `--gtest_filter`로 해당 메소드만 실행
6. **OpenCppCoverage / llvm-cov / gcov**로 메소드 정의 라인이 실제 실행됐는지 검증
7. `.ralph-state.json`에 status/attempts/last_error 저장 → 재실행 시 통과한 것은 skip, 실패한 것만 재시도

주요 플래그:
- `-DryRun` — LLM 호출 없이 skeleton으로 플로우만 검증
- `-SkipBuild`, `-SkipCoverage` — 부분 검증
- `-CoverageTool opencppcoverage|llvm|gcov|none` — 수동 지정
- `-MaxIterations N` — 메소드당 재시도 상한

자세한 사용법·트러블슈팅: [`scripts/README.md`](scripts/README.md)

## 결과 JSON 형태

`out/main/methods/_global_/WorkAreaConfiguration/13e2fef....json` 예시:

```jsonc
{
  "method": {
    "qualified_name": "WorkAreaConfiguration::GetWorkAreaFromWindow",
    "class": "WorkAreaConfiguration",
    "signature": "WorkArea *const WorkAreaConfiguration::GetWorkAreaFromWindow(HWND)",
    "return_type": "WorkArea *const",
    "parameters": [{"name": "window", "type": "HWND"}],
    "specifiers": ["const"]
  },
  "location": {
    "customer": "main",
    "definition": {"line": 38, "path": "FancyZonesLib/WorkAreaConfiguration.cpp"}
  },
  "complexity": {"cyclomatic": 2, "nloc": 13, "parameter_count": 1},
  "dependencies": {
    "classes": [{
      "qualified_name": "WorkAreaConfiguration",
      "header": "FancyZonesLib/WorkAreaConfiguration.h:7",
      "used_as": ["call_target"],
      "used_methods": ["GetWorkArea"]
    }]
  },
  "call_graph": [
    {"call_site_line": 40, "target": "WorkAreaConfiguration::GetWorkArea"},
    {"call_site_line": 50, "in_branch": true, "target": "WorkAreaConfiguration::GetWorkArea"}
  ]
}
```

필드는 해당 메소드에 실제로 존재하는 것만 포함 (빈 배열/기본값 자동 제거). 토큰 효율.

## 설정 레퍼런스

### `[target]`
| 키 | 설명 |
|---|---|
| `repo_root` | cpp 파일 스캔 뿌리 |
| `scope_root` | 관심사 루트 (이 밖은 external → dep 필터링). 생략 시 repo_root |
| `solution` | binlog 경로 활성화 시 msbuild에 줄 `.sln`/`.slnx`/`.vcxproj` |
| `is_git` | `auto` / `true` / `false`. strategy=auto일 때 worktree 가능 여부 판정 |

### `[workspace]`
| strategy | 용도 |
|---|---|
| `in-place` | repo를 직접 분석, 복제 없음. 단일 변종 프로젝트 |
| `copy-tree` | 고객사별로 파일 복사. 고객사 변종(`src/acme/`, `src/globex/`) 있을 때 |
| `symlink-tree` | 복사 대신 심볼릭 링크 (Windows는 개발자 모드 필요) |
| `git-worktree-sparse` | git repo에서 `worktree + sparse-checkout`. 가장 빠름 |
| `auto` | git이면 worktree, 아니면 symlink, 실패시 copy (자동 폴백) |

### `[customers.*]`
각 커스터머를 섹션으로 정의. 이름 하나는 필수. 예:
```toml
[customers.acme]
branches = ["main"]
extra_paths = []
```

### `[analysis]`
| 키 | 설명 |
|---|---|
| `include_dirs` | libclang `-I`로 들어갈 경로 리스트 (상대/절대) |
| `defines` | libclang `-D`로 들어갈 매크로 |
| `clang_flags` | 추가 flag. MSVC 프로젝트는 `-fms-extensions -fms-compatibility` 필수 |
| `pch_autodetect` | binlog의 `/Yu` 발견 시 `-include pch.h` 자동 주입 |
| `skip_generated` | `moc_*.cpp`, `.rc` 등 생성 소스 skip |

### `[build_intel]` — MSBuild binlog 통합
| 키 | 설명 |
|---|---|
| `enabled` | `true`면 L0 레이어 활성화 |
| `mode` | `cached-only`(기본, 빌드 안 함) / `build-once` / `always-build` |
| `binlog` | .binlog 파일 경로 (상대/절대) |
| `max_age_h` | cached/always 모드에서 stale 판정 시간 |

### `[test]`
| 키 | 설명 |
|---|---|
| `framework` | 현재 `gtest`만 지원 |
| `mock_dirs` | mock 헤더 스캔 디렉터리 |
| `mock_name_patterns` | `Mock{Class}` 같은 패턴. 파싱해서 실제 `: public Target` 상속 검증 |

## CLI 커맨드
```bash
methoddep init                                 # methoddep.toml 스캐폴드
methoddep doctor                                # 외부 툴 진단
methoddep run --config <toml> --customer <c>   # 전 파이프라인 실행
methoddep verify-fixtures --fixture-root <d>   # @methoddep:expect 주석으로 커버리지 게이트
```

## 사용 패턴별 가이드

### A. 단일 타겟 프로젝트 (대부분)
```toml
[workspace] strategy = "in-place"
[customers.main]
```

### B. 고객사별 cpp 분기 (`src/acme/`, `src/globex/`)
```toml
[workspace] strategy = "copy-tree"   # 또는 "git-worktree-sparse"
[customers.acme]
[customers.globex]
```
→ 출력도 자동 분리: `out/acme/methods/...` vs `out/globex/methods/...`

### C. 큰 프로젝트의 일부 모듈만 관심
```toml
[target]
repo_root  = "D:/proj/BigRepo/src/modules/my_module"   # 분석 대상만
scope_root = "D:/proj/BigRepo"                          # 관심 범위는 전체 프로젝트

[analysis]
include_dirs = [
    "include",                                          # 모듈 내부
    "D:/proj/BigRepo/src/common",                       # 상위 공통 헤더
]
```

### D. CMake + ninja 프로젝트
현재 `compile_commands.json` 직접 지원은 없음 — 필요한 `include_dirs`/`defines`를 수동 지정. (v0.2에 추가 예정)

## 동작 레이어

```
L0  MSBuild binlog  →  include/defines/PCH 자동 추출 (선택)
L1  libclang        →  AST 기반: signature, deps, call_graph, exception_spec
L2  tree-sitter     →  L1 실패 TU용 fallback: signature + 파라미터 타입명
L3  ctags           →  L1/L2 위치 교차검증 (ctags 설치시)
     lizard         →  cyclomatic complexity
     mocks resolver →  상속 검증 기반 mock 탐색 + gmock_skeleton 생성
```

## 디버깅

**"0 methods 파싱됨"** (run 출력에 `warn: libclang: no methods parsed from X.cpp`):
- libclang이 헤더를 못 찾음 → `include_dirs`에 누락된 경로 추가
- MSVC 특수 매크로 → `clang_flags`에 `-fms-extensions -fms-compatibility` 추가

**`dep_classes` 비어있음**:
- `scope_root`가 너무 좁음. 관심 헤더가 scope 안인지 확인.

**파일 수 폭주** (header-only 템플릿 다수):
- 커스터머별 중복 emit이 의도 (index.json의 `by_class`로 조회)

**Win32 API가 dep에 새고 있음**:
- `scope_root`를 프로젝트 루트에 정확히 맞춰야 함

## 테스트 실행
```bash
pytest                        # unit + integration (~100 tests)
pytest tests/unit/ -q         # unit만
```

## 설계 문서
상세 설계 근거와 결정 배경: `C:\Users\이경민\.claude\plans\crispy-roaming-leaf.md`

자동 테스트 생성 워크플로우: `scripts/README.md` 참고
