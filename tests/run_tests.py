"""
Run all CycFlow-py tests in dependency order:
  1. test_core  — базовые типы (PReg, RecRule, Record, RecBuffer)
  2. test_cbf   — файловый формат CBF
  3. test_csv   — запись в CSV
  4. test_tcp   — TCP-сервер и клиент
"""
import sys
import pytest

SUITES = [
    "tests/test_core.py",
    "tests/test_cbf.py",
    "tests/test_csv.py",
    "tests/test_tcp.py",
]

if __name__ == "__main__":
    extra = sys.argv[1:]  # любые дополнительные флаги pytest (например -v, -s)
    code = pytest.main([*SUITES, *extra])
    sys.exit(code)
