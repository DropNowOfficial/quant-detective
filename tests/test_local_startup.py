"""Source-level launcher selection checks, not Windows execution acceptance."""
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("document", ["README.md", "docs/live-screener.zh-CN.md"])
def test_live_startup_instructions_sync_before_project_runner(document):
    text = (ROOT / document).read_text()
    first_command_block = text.split("```bash", 1)[1].split("```", 1)[0]
    assert "uv sync --frozen" in first_command_block
    assert "uv run --frozen qd-market serve" in first_command_block
    assert first_command_block.index("uv sync --frozen") < first_command_block.index("uv run --frozen qd-market serve")
    assert "python -m market_data serve" not in first_command_block


def test_windows_launcher_selects_synced_python_and_explains_missing_prerequisites():
    text = (ROOT / "start-live.cmd").read_text().lower()
    assert 'cd /d "%~dp0"' in text
    assert 'if not exist ".venv\\scripts\\python.exe"' in text
    assert '".venv\\scripts\\python.exe" -m market_data serve' in text
    assert '".venv\\scripts\\python.exe" -c ' in text
    assert "import pydantic, exchange_calendars" in text
    prerequisite_check = re.search(r'-c "([^\"]+)"', text).group(1)
    compile(prerequisite_check, "start-live prerequisite check", "exec")
    assert "uv sync --frozen" in text
    assert "where curl" in text
    assert "where python" not in text
    assert '\npython ' not in text
    assert 'exit /b %exit_code%' in text
