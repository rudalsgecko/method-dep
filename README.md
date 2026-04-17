# method-dep

C++ 메서드 단위 테스트 자동 생성 워크플로우. tree-sitter 로 메서드를 추출하고, 의존성을 분석한 뒤 LLM(Claude / opencode)을 호출해 Google Test 파일을 생성·컴파일·실행하고 커버리지까지 검증한다.

## 요구사항

- Python 3.10+
- C++ 프로젝트의 `compile_commands.json` (CMake: `-DCMAKE_EXPORT_COMPILE_COMMANDS=ON`)
- LLM CLI: `claude` 또는 `opencode`
- 테스트 빌드/실행 도구: Google Test, (선택) OpenCppCoverage

## 설치

```bash
git clone <this-repo> method-dep
cd method-dep
pip install -e .
```

설치하면 `method-dep` 명령이 등록된다.

## 빠른 시작

```bash
# 1) 대상 C++ 프로젝트에 대한 설정 파일 생성
method-dep init /path/to/your-cpp-project

# 2) 메서드 스캔 → 의존성 분석 → 테스트 생성 (전체 파이프라인)
method-dep run

# 또는 단계별 실행
method-dep scan       # 메서드 추출
method-dep analyze    # 의존성/모킹 후보 분석 + 컨텍스트 MD 생성
method-dep generate   # LLM 호출 루프 (생성 → 컴파일 → 실행 → 커버리지)
```

## 명령어

| 명령 | 설명 |
| --- | --- |
| `init <project_path>` | `method-dep.yaml` 설정 파일 생성 |
| `scan` | 소스 파일에서 테스트 가능한 메서드 추출 |
| `analyze` | 의존성 그래프와 모킹 후보 분석, 메서드별 컨텍스트 MD 생성 |
| `generate` | 미완료 메서드에 대해 LLM 호출 루프 실행 |
| `run` | scan + analyze + generate 일괄 실행 |
| `status` | 메서드별 생성/컴파일/통과/커버리지 현황 출력 |
| `reset [--method NAME]` | 상태 초기화 (특정 메서드 또는 전체) |

공통 옵션: `-c <config.yaml>`, `-p <project_path>`, `--llm claude|opencode`, `--max-attempts N`

## 설정 파일

`method-dep init` 실행 시 `method-dep.yaml` 이 생성된다. 주요 항목은 `config.example.yaml` 참고.

```yaml
project_path: "/path/to/cpp-project"
compile_commands: "build/compile_commands.json"
output_dir: ".method-dep"
test_framework: "gtest"
llm_tool: "claude"          # claude | opencode
max_attempts: 3
coverage_tool: "OpenCppCoverage"
coverage_threshold: 60.0
skip_external_deps: true
exclude_patterns:
  - "test/*"
  - "third_party/*"
  - "build/*"
```

## 출력 구조

대상 프로젝트의 `output_dir` (기본 `.method-dep/`) 아래에 생성된다.

```
.method-dep/
├─ methods.json          # 메서드 트래킹 상태
├─ context/              # 메서드별 컨텍스트 MD (LLM 입력)
└─ generated_tests/      # LLM 이 생성한 *.cpp 테스트 파일
```

## 동작 흐름

1. **scan**: tree-sitter-cpp 로 메서드 추출, getter/setter/생성자/소멸자 등 trivial 메서드 필터링
2. **analyze**: `compile_commands.json` 기반 심볼 테이블 구축 → 메서드별 의존 타입/함수 수집 → 모킹 후보 식별 → 컨텍스트 MD 저장
3. **generate**: 미완료 메서드마다 LLM 호출 → 생성된 테스트 컴파일 → 실행 → 커버리지 측정. 실패 시 에러 메시지를 피드백해 `max_attempts` 회까지 재생성

## 트러블슈팅

- `compile_commands.json not found` → `cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON ..` 로 생성
- LLM 호출 실패 → `claude` / `opencode` CLI 가 `PATH` 에 있는지, 설정의 `claude_command` / `opencode_command` 확인
- 커버리지 측정 안 됨 → Windows 는 OpenCppCoverage, Linux/Mac 은 별도 설정 필요. `coverage_tool` 항목 참고
- 특정 메서드만 다시 생성 → `method-dep reset --method <부분 이름>` 후 `generate`
