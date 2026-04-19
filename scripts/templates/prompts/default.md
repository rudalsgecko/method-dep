You are writing a GoogleTest + GoogleMock unit test for a single C++ method.
Your sole output MUST be a single complete .cpp file wrapped in a ```cpp fenced block.

## Target
- qualified_name: `{{QUALIFIED_NAME}}`
- signature: `{{SIGNATURE}}`
- return_type: `{{RETURN_TYPE}}`
- parameters: {{PARAMETERS_JSON}}
- specifiers: {{SPECIFIERS_JSON}}
- cyclomatic: {{CYCLOMATIC}}, nloc: {{NLOC}}
- definition: `{{DEF_PATH}}:{{DEF_LINE}}`
- suggested fixture name: `{{FIXTURE_NAME}}`

## Method metadata (methoddep JSON, verbatim)
```json
{{METHOD_JSON}}
```

## Source context
```cpp
{{SOURCE_SNIPPET}}
```

## Dependencies (include these headers; mock interface types)
{{DEPENDENCIES}}

## Call graph (targets this method touches)
{{CALL_GRAPH}}

## Specifier hints
{{SPECIFIER_HINTS}}

{{PREVIOUS_TEST_BLOCK}}

{{PREVIOUS_ERROR_BLOCK}}

## Requirements
1. Output ONLY a single complete .cpp file inside one ```cpp ... ``` block.
2. Use GoogleTest + GoogleMock. Mock any dependency that is an abstract interface via `MOCK_METHOD`.
3. Produce at least {{MIN_TESTS}} `TEST_F` cases, covering each branch of the method.
4. `#include` the dependency headers exactly as shown above (relative paths).
5. Test fixture MUST be named `{{FIXTURE_NAME}}` (derived from class bare-name).
6. EVERY `TEST_F` must invoke `{{QUALIFIED_NAME}}` directly — we verify success via coverage of
   `{{DEF_PATH}}` line {{DEF_LINE}}.
7. No filesystem I/O, no network I/O, no external fixture files.
8. If the target method requires non-trivial construction, build the smallest fake/mock
   graph needed; otherwise construct objects directly.
9. Compile-ready: `#include <gtest/gtest.h>` and `#include <gmock/gmock.h>` explicitly.

Respond with the .cpp file only.
