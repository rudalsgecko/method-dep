"""Track test generation status per method in JSON."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Optional

from .models import MethodInfo, MethodTestStatus


class TestTracker:
    """Manage methods.json - tracks test creation status for each method."""

    def __init__(self, json_path: Path):
        self.json_path = json_path
        self._methods: dict[str, MethodTestStatus] = {}
        if json_path.exists():
            self._load()

    def _load(self) -> None:
        data = json.loads(self.json_path.read_text(encoding="utf-8"))
        for entry in data.get("methods", []):
            status = MethodTestStatus.from_dict(entry)
            self._methods[status.method_id] = status

    def save(self) -> None:
        data = {
            "version": 1,
            "total": len(self._methods),
            "created": sum(1 for m in self._methods.values() if m.created),
            "passed": sum(1 for m in self._methods.values() if m.passed),
            "methods": [m.to_dict() for m in self._methods.values()],
        }
        self.json_path.parent.mkdir(parents=True, exist_ok=True)
        self.json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def register_methods(self, methods: list[MethodInfo]) -> int:
        """Register methods from extraction. Returns count of newly added."""
        new_count = 0
        for m in methods:
            if m.method_id not in self._methods:
                self._methods[m.method_id] = MethodTestStatus(
                    method_id=m.method_id,
                    name=m.qualified_name,
                    slug=m.slug,
                    file_path=m.file_path,
                    class_name=m.class_name,
                )
                new_count += 1
        return new_count

    def get_pending(self) -> list[MethodTestStatus]:
        """Get all methods that don't have passing tests yet."""
        return [
            m for m in self._methods.values()
            if not m.passed and m.attempts < 3  # max_attempts default
        ]

    def get_all(self) -> list[MethodTestStatus]:
        return list(self._methods.values())

    def get(self, method_id: str) -> Optional[MethodTestStatus]:
        return self._methods.get(method_id)

    def update(self, method_id: str, **kwargs) -> None:
        if method_id in self._methods:
            for k, v in kwargs.items():
                if hasattr(self._methods[method_id], k):
                    setattr(self._methods[method_id], k, v)
            self.save()

    def mark_created(self, method_id: str, test_file: str) -> None:
        self.update(method_id, created=True, test_file=test_file)

    def mark_compiled(self, method_id: str, success: bool, error: str = "") -> None:
        self.update(method_id, compiled=success, error_message=error)

    def mark_passed(self, method_id: str, passed: bool, coverage: float = 0.0) -> None:
        self.update(method_id, passed=passed, coverage=coverage)

    def increment_attempts(self, method_id: str) -> None:
        m = self._methods.get(method_id)
        if m:
            m.attempts += 1
            self.save()

    def summary(self) -> dict:
        total = len(self._methods)
        created = sum(1 for m in self._methods.values() if m.created)
        compiled = sum(1 for m in self._methods.values() if m.compiled)
        passed = sum(1 for m in self._methods.values() if m.passed)
        coverage_ok = sum(1 for m in self._methods.values() if m.coverage >= 60.0)
        return {
            "total": total,
            "created": created,
            "compiled": compiled,
            "passed": passed,
            "coverage_ok": coverage_ok,
            "remaining": total - passed,
        }
