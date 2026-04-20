# methoddep — 설계와 내부 구조

루트 `README.md`가 **"어떻게 쓰는가"** 에 집중한다면, 이 문서는
**"왜 이렇게 동작하는가"** 를 담는다. 새 컨트리뷰터·디버깅·세부 설정 튜닝용.

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
C++ 소스 → 메소드별 JSON. libclang + tree-sitter + lizard로 signature·복잡도·의존성·호출 그래프 추출. 소스가 바뀌면 이 단계만 다시 돌려 JSON 갱신. 시그니처가 바뀐 메소드는 `id` 해시가 달라져 자동으로 "새 메소드" 취급.

### Stage 2 — 테스트 자동 생성 + 검증 (`ralph_test_from_json.ps1`)
Stage 1이 만든 JSON을 하나씩 LLM에 먹이고 실제로 **빌드 + 실행 + 커버리지 검증**까지. 재실행 시 이미 passed된 메소드는 skip, failed는 에러 피드백과 함께 재시도. 자세한 동작·플래그는 [`scripts/README.md`](scripts/README.md).

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

## 동작 레이어

```
L0  MSBuild binlog  →  include/defines/PCH 자동 추출 (선택)
L1  libclang        →  AST 기반: signature, deps, call_graph, exception_spec
L2  tree-sitter     →  L1 실패 TU용 fallback: signature + 파라미터 타입명
L3  ctags           →  L1/L2 위치 교차검증 (ctags 설치시)
     lizard         →  cyclomatic complexity
     mocks resolver →  상속 검증 기반 mock 탐색 + gmock_skeleton 생성
```

L0는 `[build_intel].enabled`가 켜졌을 때만 가동. pipeline에서 `_gather_build_intel` → `parse_binlog_xml` (StructuredLogger.Cli 있으면) 또는 `parse_msbuild_text_log` (`.log` fallback) 순. TU별 `include_dirs`/`defines`/`pch_header`는 파일별로 분리 병합되어 libclang에 주입된다 (`pipeline.py:_merge_includes`, `tu_by_source`).

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

> **Tip**: `[build_intel]`이 켜져 있고 binlog가 있으면 `include_dirs`/`defines`는 대부분 자동으로 채워진다. 수동 항목은 binlog에 누락된 것(예: vendored include)만 보완용으로 쓰는 걸 권장.

### `[build_intel]` — MSBuild binlog 통합
| 키 | 설명 |
|---|---|
| `enabled` | `true`면 L0 레이어 활성화 (scaffold 기본값: true) |
| `mode` | `cached-only`(기본, 빌드 안 함) / `build-once` / `always-build` |
| `binlog` | .binlog 파일 경로 (상대/절대) |
| `max_age_h` | cached/always 모드에서 stale 판정 시간 |

### `[test]`
| 키 | 설명 |
|---|---|
| `framework` | 현재 `gtest`만 지원 |
| `mock_dirs` | mock 헤더 스캔 디렉터리 |
| `mock_name_patterns` | `Mock{Class}` 같은 패턴. 파싱해서 실제 `: public Target` 상속 검증 |

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

## 디버깅

**"0 methods 파싱됨"** (run 출력에 `warn: libclang: no methods parsed from X.cpp`):
- libclang이 헤더를 못 찾음 → `include_dirs`에 누락된 경로 추가 (또는 binlog가 그 TU를 커버하는지 확인)
- MSVC 특수 매크로 → `clang_flags`에 `-fms-extensions -fms-compatibility` 추가

**`dep_classes` 비어있음**:
- `scope_root`가 너무 좁음. 관심 헤더가 scope 안인지 확인.

**파일 수 폭주** (header-only 템플릿 다수):
- 커스터머별 중복 emit이 의도 (index.json의 `by_class`로 조회)

**Win32 API가 dep에 새고 있음**:
- `scope_root`를 프로젝트 루트에 정확히 맞춰야 함

**`build_intel: binlog not found`** (cached-only 모드):
- pipeline은 스스로 빌드하지 않음. `msbuild <sln> /bl:artifacts/msbuild.binlog`를 먼저 돌리거나 mode를 `build-once`로 바꿀 것.

## 설계 문서
상세 설계 근거와 결정 배경: `C:\Users\이경민\.claude\plans\crispy-roaming-leaf.md`

자동 테스트 생성 워크플로우 내부 구조: [`scripts/README.md`](scripts/README.md).
