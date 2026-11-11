"""Panel-port parsing must fall back to 8000 on malformed values."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "cli"))

import smite


def test_malformed_panel_port_falls_back_to_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("PANEL_PORT=abc\n", encoding="utf-8")
    assert smite.get_panel_port() == 8000


def test_valid_panel_port_is_used(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("PANEL_PORT=9001\n", encoding="utf-8")
    assert smite.get_panel_port() == 9001
