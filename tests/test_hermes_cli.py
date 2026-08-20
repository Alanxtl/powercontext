# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

from typer.testing import CliRunner

import powercontext.cli.hermes as hermes_cli
from powercontext.cli.app import create_cli
from powercontext.cli.system import doctor_app, setup_app


def _write_plugin(root: Path) -> Path:
    plugin = root / "integrations" / "hermes" / "plugins" / "powercontext"
    plugin.mkdir(parents=True)
    (plugin / "__init__.py").write_text("def register(): pass\n", encoding="utf-8")
    (plugin / "plugin.yaml").write_text("name: powercontext\n", encoding="utf-8")
    return plugin


def test_setup_hermes_copies_provider_from_a_local_checkout(tmp_path: Path, monkeypatch) -> None:
    checkout = tmp_path / "powercontext"
    _write_plugin(checkout)
    hermes_home = tmp_path / "hermes"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(hermes_cli, "which", lambda _name: "/usr/bin/hermes")

    result = CliRunner().invoke(
        create_cli([setup_app]),
        ["setup", "hermes", "--source", str(checkout), "--json"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "plugin": "powercontext",
        "plugin_path": str(hermes_home / "plugins" / "powercontext"),
        "hermes_home": str(hermes_home),
        "data_dir": str(tmp_path / "data"),
    }
    assert (hermes_home / "plugins" / "powercontext" / "plugin.yaml").is_file()


def test_setup_hermes_reports_missing_cli(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(hermes_cli, "which", lambda _name: None)

    result = CliRunner().invoke(
        create_cli([setup_app]),
        ["setup", "hermes", "--source", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert "Hermes CLI is not installed" in result.output


def test_doctor_hermes_reports_an_installed_provider(tmp_path: Path, monkeypatch) -> None:
    hermes_home = tmp_path / "hermes"
    plugin = hermes_home / "plugins" / "powercontext"
    plugin.mkdir(parents=True)
    (plugin / "__init__.py").write_text("def register(): pass\n", encoding="utf-8")
    (plugin / "plugin.yaml").write_text("name: powercontext\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setattr(hermes_cli, "which", lambda _name: "/usr/bin/hermes")

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "hermes", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["checks"]["hermes"] == {
        "ok": True,
        "status": "ok",
        "detail": "/usr/bin/hermes",
    }
    assert payload["checks"]["plugin"] == {
        "ok": True,
        "status": "ok",
        "detail": "powercontext is installed",
    }


def test_doctor_hermes_reports_missing_provider(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setattr(hermes_cli, "which", lambda _name: "/usr/bin/hermes")

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "hermes"])

    assert result.exit_code == 1
    assert "hermes: ok - /usr/bin/hermes" in result.output
    assert "plugin: failed - PowerContext Hermes plugin is not installed" in result.output


def test_setup_hermes_remote_checkout_uses_the_requested_ref(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    captured: list[list[str]] = []

    def fake_run(command, **_kwargs):
        captured.append(command)
        _write_plugin(Path(command[-1]))
        return Mock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(hermes_cli.subprocess, "run", fake_run)

    plugin = hermes_cli.resolve_hermes_plugin_dir(source="oceanbase/powercontext", ref="v0.0.2")

    assert (
        plugin
        == tmp_path
        / "data"
        / "checkouts"
        / "hermes"
        / "v0.0.2"
        / "integrations"
        / "hermes"
        / "plugins"
        / "powercontext"
    )
    assert captured[0][:6] == ["git", "clone", "--depth", "1", "--branch", "v0.0.2"]
    assert captured[0][6] == "https://github.com/oceanbase/powercontext.git"
