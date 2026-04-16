# Unit Test Generation Prompt Template

This template is used internally by method-dep when calling the LLM.
You can customize it by modifying `src/method_dep/llm.py`.

## Variables

- `{method_name}` — qualified method name (e.g., `MyClass::doSomething`)
- `{context}` — full context markdown (method source + dependencies + mocks)
- `{test_output_path}` — where the test file will be saved

## Prompt Structure

1. **Role**: C++ unit test expert
2. **Task**: Write Google Test file for the specified method
3. **Context**: Method implementation, type dependencies, mock candidates
4. **Requirements**:
   - Google Test + Google Mock
   - Cover normal, edge, and error cases
   - Proper mocking for dependencies
   - Descriptive test names
   - Self-contained compilation
   - Target >60% coverage
5. **Output**: Pure C++ code (no markdown fences)
