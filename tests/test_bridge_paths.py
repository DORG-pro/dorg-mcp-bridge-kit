"""Tests for bridges/<name>/ workspace path resolution."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from cli.bridge_paths import (  # noqa: E402
    assemble_build_context,
    init_bridge,
    resolve_bridge_workspace,
    validate_bridge_name,
)


def test_validate_bridge_name():
    assert validate_bridge_name("dorghub") == "dorghub"
    assert validate_bridge_name("acme-crm") == "acme-crm"
    with pytest.raises(ValueError):
        validate_bridge_name("Bad_Name")
    with pytest.raises(ValueError):
        validate_bridge_name("_hidden")


def test_init_and_package_bridge(tmp_path, monkeypatch):
    monkeypatch.setattr("cli.bridge_paths.BRIDGES_DIR", tmp_path / "bridges")
    monkeypatch.setattr("cli.bridge_paths.TEMPLATE_DIR", _REPO / "bridges" / "_template")
    monkeypatch.setattr("cli.bridge_paths.RUNTIME_DIR", _REPO / "bridge")

    ws = init_bridge("test-svc", template="passthrough")
    assert ws.project_config.exists()
    assert ws.documentation.exists()

    ws.manifest.write_text(
        '{"competency_id":"t","tools":[]}',
        encoding="utf-8",
    )
    build = assemble_build_context(ws)
    assert (build / "server.py").exists()
    assert (build / "competency.manifest.json").exists()
    assert (build / "Dockerfile").exists()

    shutil.rmtree(ws.root)
