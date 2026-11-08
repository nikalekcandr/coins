import json
from pathlib import Path
import subprocess
import sys

import pytest
from coinarb.fetch import load_storage_state

ROOT = Path(__file__).resolve().parents[1]


def test_windows_scripts_are_ascii_and_keep_errors_visible():
    for name in ("SETUP.bat", "RUN.bat"):
        text = (ROOT / name).read_bytes().decode("ascii")
        assert "pause" in text
        assert 'cd /d "%~dp0"' in text
        assert "if errorlevel 1" in text


def test_missing_dependencies_has_friendly_error():
    result = subprocess.run([sys.executable, "-S", str(ROOT / "scan.py"), "demo"],
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode != 0
    assert "requirements.txt" in result.stdout
    assert "Traceback" not in result.stderr


def test_editthiscookie_export(tmp_path):
    path = tmp_path / "cookies.json"
    path.write_text(json.dumps([{"name": "session", "value": "abc", "domain": ".meshok.net",
                                 "path": "/", "expirationDate": 1900000000,
                                 "sameSite": "no_restriction", "hostOnly": True}]))
    assert load_storage_state(str(path)) == {"cookies": [{"name": "session", "value": "abc",
        "domain": ".meshok.net", "path": "/", "expires": 1900000000, "sameSite": "None"}], "origins": []}


def test_playwright_state_and_invalid_files(tmp_path):
    path = tmp_path / "cookies.json"
    state = {"cookies": [], "origins": []}
    path.write_text(json.dumps(state))
    assert load_storage_state(str(path)) == state
    path.write_text('{}')
    with pytest.raises(ValueError, match="cookies"):
        load_storage_state(str(path))
    with pytest.raises(ValueError, match="cookies"):
        load_storage_state(str(tmp_path / "missing.json"))
