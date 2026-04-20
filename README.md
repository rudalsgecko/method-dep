# methoddep

C++/MSVC 프로젝트의 **메소드별 메타데이터**(시그니처·의존성·복잡도·호출 그래프·모크 위치)를 JSON으로 추출하고, 그 JSON을 먹여 GoogleTest/GoogleMock unit test를 자동 생성·검증하는 파이프라인.

> 왜 이렇게 설계됐는지, 내부 레이어(L0~L3), JSON 스키마, 설정 키 레퍼런스, 디버깅 가이드는 [`ARCHITECTURE.md`](ARCHITECTURE.md)에 있다. 이 문서는 **어떻게 쓰는가**에 집중한다.

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
scaffold는 `[build_intel] enabled = true`가 **기본으로 켜져** 있다. 아래 2단계에서 binlog만 만들어주면 `include_dirs`/`defines`/`PCH`가 자동 주입된다.

### 2단계 — binlog 생성 + `methoddep.toml`에 경로만 지정 (권장 경로)

평소 빌드 커맨드에 `/bl:` 한 줄 추가:
```bash
msbuild MyProject.sln /bl:artifacts/msbuild.binlog
```

그 다음 `methoddep.toml`에서 **두 값만** 채우면 끝:
```toml
[target]
repo_root  = "D:/proj/MyProject/src/modules/foo"
scope_root = "D:/proj/MyProject"
solution   = "MyProject.sln"                 # binlog 재생성 시 msbuild에 넘길 파일

[workspace]
strategy = "in-place"

[customers.main]
branches = []

[build_intel]
enabled = true
mode    = "cached-only"                      # 기본. methoddep은 빌드 안 함
binlog  = "artifacts/msbuild.binlog"

[test]
mock_dirs          = ["tests/mocks"]
mock_name_patterns = ["Mock{Class}", "{Class}Mock", "Fake{Class}"]

[output]
dir = "D:/proj/MyProject-methoddep-out"
```

→ `run` 시점에 binlog에서 `/I` `/D` `/Yu`가 **자동 추출**되어 libclang에 TU별로 주입된다. 수동 `[analysis] include_dirs`/`defines` 관리 불필요.

### 2b단계 — binlog 없이 쓸 때만 (fallback)

CMake/ninja 프로젝트이거나 msbuild가 없으면 `[analysis]`에 수동으로 채운다:

```toml
[analysis]
include_dirs = [
    "include",                                # repo_root 상대경로
    "D:/proj/MyProject/src/common",           # 또는 절대경로
]
defines      = ["_WIN32", "UNICODE", "_MSC_VER=1939"]
clang_flags  = ["-fms-extensions", "-fms-compatibility",
                "-std=c++20", "-target", "x86_64-pc-windows-msvc"]
```
이 경우 `[build_intel] enabled = false`로 꺼도 되고, 켠 채로 둬도 binlog가 없으면 경고만 나오고 수동 값으로 폴백된다.

### 3단계 — 실행
```bash
methoddep run --config methoddep.toml --customer main
```

출력:
```
customer=main strategy=in-place methods=342 index=...\main\index.json
  warn: ... (있으면)
```

### 4단계 — LLM에게 먹이기 (수동)

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

### 5단계 — 자동 생성 루프 (`scripts/ralph_test_from_json.ps1`)

JSON을 하나씩 수동으로 붙일 필요 없이, **전체 메소드 집합을 자동으로 순회하면서 테스트 생성 → 빌드 → 실행 → 커버리지 검증까지 돌리는 ralph-style loop** 제공.

```powershell
pwsh scripts/ralph_test_from_json.ps1 `
    -MethoddepOut "D:/proj/MyProject-methoddep-out/main" `
    -TestRoot     "D:/proj/MyProject-methoddep-tests" `
    -SourceRoot   "D:/proj/MyProject" `
    -LlmCmd       "claude -p" `
    -MaxIterations 3 `
    -OnlyClass    "MyClass"      # 선택: 특정 클래스만
```

bash 포팅도 있음 (`ralph_test_from_json.sh`, git-bash/Linux). 자세한 사용법·트러블슈팅·플래그 전체: [`scripts/README.md`](scripts/README.md).

## 프롬프트 커스터마이징 — 프로젝트 고유 지식 주입

`ralph_test_from_json.*`과 `scripts/lib/build_prompt.py`는 `--prompt-template <path>` (bash) / `-PromptTemplate <path>` (pwsh) 플래그로 **네 자신의 프롬프트 `.md`로 기본 템플릿을 교체**할 수 있다. 기본값은 `scripts/templates/prompts/default.md`.

여기가 **프로젝트에만 해당하는 사실**을 LLM에게 상시 공급하는 자리다. 예:

1. **빌드 방법** — 이 프로젝트에서 실제로 돌아가는 빌드 커맨드, 자주 쓰는 `cmake` 프리셋, 링크해야 할 라이브러리. 테스트 빌드가 깨질 때 LLM이 올바른 플래그로 재시도하도록.
2. **어떤 Claude skill을 호출할지** — 예: `/oh-my-claudecode:executor`, `/oh-my-claudecode:verifier` 같은 에이전트나 `ultrathink` 같은 키워드 트리거. 루프에서 `-LlmCmd "claude -p"`로 Claude Code CLI를 쓸 때 특정 skill을 활용하게 유도.
3. **프로젝트 고유의 Mocking 기법** — 예: "모든 Win32 HWND는 `MockWindowHandle`을 통해 주입한다", "ShellCOM 의존성은 `ScopedComInit` fixture로 감싼다", "이 리포지터리의 `MockIService`는 `NiceMock` 래퍼 `NiceMockIService`를 쓰는 게 관례" 등 코드베이스 규칙.

`default.md`의 `{{METHOD_JSON}}`, `{{SOURCE_SNIPPET}}`, `{{DEPENDENCIES}}`, `{{CALL_GRAPH}}`, `{{FIXTURE_NAME}}`, `{{PREVIOUS_TEST_BLOCK}}`, `{{PREVIOUS_ERROR_BLOCK}}` 등 placeholder는 그대로 보존한 채 위 내용을 덧붙이면 된다 (인식되는 placeholder 전체 목록은 `scripts/lib/build_prompt.py`의 `placeholders` 딕셔너리 참조).

```powershell
pwsh scripts/ralph_test_from_json.ps1 `
    -MethoddepOut   ... `
    -PromptTemplate "D:/proj/MyProject/prompts/methoddep.md" `
    -LlmCmd         "claude -p"
```

프로젝트 루트에 `prompts/methoddep.md`를 커밋해두면 팀 전체가 같은 규칙으로 테스트를 받는다.

## CLI 커맨드
```bash
methoddep init                                 # methoddep.toml 스캐폴드
methoddep doctor                                # 외부 툴 진단
methoddep run --config <toml> --customer <c>   # 전 파이프라인 실행
methoddep verify-fixtures --fixture-root <d>   # @methoddep:expect 주석으로 커버리지 게이트
```

## 테스트 실행
```bash
pytest                        # unit + integration (~100 tests)
pytest tests/unit/ -q         # unit만
```

## 더 읽을거리
- 설계·내부 구조·설정 키 레퍼런스·디버깅 가이드: [`ARCHITECTURE.md`](ARCHITECTURE.md)
- 자동 테스트 생성 루프의 내부 동작과 플래그: [`scripts/README.md`](scripts/README.md)
