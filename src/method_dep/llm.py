"""LLM CLI integration for test generation."""
from __future__ import annotations
import subprocess
import tempfile
import re
from pathlib import Path
from typing import Optional

from .config import Config


class LLMCaller:
    """Call opencode or claude code CLI to generate unit tests."""

    def __init__(self, config: Config):
        self.config = config
        self.tool = config.llm_tool  # "claude" or "opencode"

    def generate_test(self, context_md_path: Path, method_name: str, test_output_path: Path) -> bool:
        """Call LLM to generate a test file. Returns True if test file was created."""
        prompt = self._build_prompt(context_md_path, method_name, test_output_path)

        if self.tool == "claude":
            return self._call_claude(prompt, test_output_path)
        elif self.tool == "opencode":
            return self._call_opencode(prompt, test_output_path)
        else:
            print(f"  [ERROR] Unknown LLM tool: {self.tool}")
            return False

    def regenerate_test(
        self,
        context_md_path: Path,
        method_name: str,
        test_output_path: Path,
        error_message: str,
    ) -> bool:
        """Regenerate a test that failed compilation or execution."""
        prompt = self._build_fix_prompt(context_md_path, method_name, test_output_path, error_message)

        if self.tool == "claude":
            return self._call_claude(prompt, test_output_path)
        elif self.tool == "opencode":
            return self._call_opencode(prompt, test_output_path)
        return False

    def _build_prompt(self, context_md_path: Path, method_name: str, test_output_path: Path) -> str:
        context = context_md_path.read_text(encoding="utf-8")

        return f"""You are a C++ unit test expert. Generate a Google Test unit test file.

## Task
Write a comprehensive Google Test file for the method `{method_name}`.

## Context
The following document contains the method implementation, its dependencies, and mock candidates:

---
{context}
---

## Requirements
1. Use Google Test (gtest) and Google Mock (gmock) frameworks
2. Test all code paths: normal cases, edge cases, error cases
3. Use proper mocking for dependencies (use GMock MOCK_METHOD)
4. Each TEST or TEST_F should test exactly one behavior
5. Use descriptive test names: TEST(ClassName_MethodName, WhenCondition_ExpectBehavior)
6. Include all necessary #include directives
7. The test must compile and run independently
8. Aim for >60% code coverage of the method under test

## Output
Write ONLY the complete C++ test file content. No explanations, no markdown fences.
The file will be saved to: {test_output_path}

Begin the test file now:"""

    def _build_fix_prompt(
        self,
        context_md_path: Path,
        method_name: str,
        test_output_path: Path,
        error_message: str,
    ) -> str:
        context = context_md_path.read_text(encoding="utf-8")
        existing_test = ""
        if test_output_path.exists():
            existing_test = test_output_path.read_text(encoding="utf-8")

        return f"""You are a C++ unit test expert. Fix the failing test file.

## Method Context
{context}

## Current Test File
```cpp
{existing_test}
```

## Error
```
{error_message}
```

## Task
Fix the test file so it compiles and passes. Output ONLY the complete corrected C++ test file.
No explanations, no markdown fences.

Begin the corrected test file now:"""

    def _call_claude(self, prompt: str, test_output_path: Path) -> bool:
        """Call Claude Code CLI."""
        cmd = self.config.claude_command
        # Write prompt to temp file to avoid shell escaping issues
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(prompt)
            prompt_file = f.name

        try:
            result = subprocess.run(
                [
                    cmd, "-p", prompt,
                    "--output-format", "text",
                ],
                capture_output=True,
                text=True,
                timeout=300,
                cwd=str(self.config.project_root),
            )

            if result.returncode != 0:
                print(f"  [ERROR] Claude CLI failed: {result.stderr[:500]}")
                return False

            output = result.stdout.strip()
            return self._save_test_output(output, test_output_path)

        except FileNotFoundError:
            print(f"  [ERROR] Claude CLI not found: {cmd}")
            print("  Make sure 'claude' is installed and in PATH")
            return False
        except subprocess.TimeoutExpired:
            print("  [ERROR] Claude CLI timed out (300s)")
            return False
        finally:
            Path(prompt_file).unlink(missing_ok=True)

    def _call_opencode(self, prompt: str, test_output_path: Path) -> bool:
        """Call opencode CLI."""
        cmd = self.config.opencode_command

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(prompt)
            prompt_file = f.name

        try:
            result = subprocess.run(
                [cmd, "-p", prompt],
                capture_output=True,
                text=True,
                timeout=300,
                cwd=str(self.config.project_root),
            )

            if result.returncode != 0:
                print(f"  [ERROR] opencode failed: {result.stderr[:500]}")
                return False

            output = result.stdout.strip()
            return self._save_test_output(output, test_output_path)

        except FileNotFoundError:
            print(f"  [ERROR] opencode not found: {cmd}")
            return False
        except subprocess.TimeoutExpired:
            print("  [ERROR] opencode timed out (300s)")
            return False
        finally:
            Path(prompt_file).unlink(missing_ok=True)

    def _save_test_output(self, output: str, test_output_path: Path) -> bool:
        """Extract C++ code from LLM output and save to file."""
        # Try to extract code from markdown fences if present
        code = output
        fence_match = re.search(r'```(?:cpp|c\+\+)?\s*\n(.*?)```', output, re.DOTALL)
        if fence_match:
            code = fence_match.group(1)

        # Basic validation: should contain #include and TEST
        if "#include" not in code:
            print("  [WARN] LLM output doesn't look like valid C++ test code")
            # Still save it, might be usable
        if "TEST" not in code and "TEST_F" not in code:
            print("  [WARN] LLM output doesn't contain any TEST macros")

        test_output_path.parent.mkdir(parents=True, exist_ok=True)
        test_output_path.write_text(code.strip() + "\n", encoding="utf-8")
        return True
