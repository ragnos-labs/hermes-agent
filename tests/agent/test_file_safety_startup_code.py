"""Write-deny rules for code that runs on the next start.

The running install's source root, its interpreter's environment
(``sys.prefix`` and site-packages, where a ``.pth`` file runs at every
interpreter start), ``$HERMES_HOME/scripts`` and the per-user autostart
directories all hold code that runs outside the agent's tool bounds, so file
tools must not write them. On case-insensitive filesystems a differently
cased spelling of a denied path is denied too.
"""
from __future__ import annotations

import os
import site
import sys
import sysconfig
from pathlib import Path

import pytest

import agent.file_safety as fs

# Captured at import, before tests/conftest.py drops the source root for
# tests that write inside the checkout.
_REAL_INSTALL_CODE_ROOTS = fs._install_code_roots


@pytest.fixture(autouse=True)
def _real_install_code_roots(monkeypatch):
    monkeypatch.setattr(fs, "_install_code_roots", _REAL_INSTALL_CODE_ROOTS)


@pytest.fixture
def root_home(tmp_path, monkeypatch):
    root = tmp_path / "hermes"
    root.mkdir()
    monkeypatch.setattr(fs, "_hermes_home_path", lambda: root)
    monkeypatch.setattr(fs, "_hermes_root_path", lambda: root)
    return root


def _assert_startup_denied(path: str) -> None:
    assert fs.is_startup_code_path(path) is True
    assert fs.is_write_denied(path) is True
    err = fs.get_write_denied_error(path)
    assert err and "runs on the next start" in err


def test_install_source_root_is_denied():
    source_root = Path(fs.__file__).resolve().parent.parent
    _assert_startup_denied(str(source_root / "run_agent.py"))
    _assert_startup_denied(str(source_root / "agent" / "file_safety.py"))
    _assert_startup_denied(str(source_root / "plugins" / "new_plugin" / "__init__.py"))


def test_interpreter_site_packages_is_denied():
    purelib = sysconfig.get_paths()["purelib"]
    _assert_startup_denied(os.path.join(purelib, "zz_injected.pth"))
    _assert_startup_denied(os.path.join(purelib, "sitecustomize.py"))
    for directory in site.getsitepackages():
        _assert_startup_denied(os.path.join(directory, "zz_injected.pth"))


def test_virtualenv_prefix_is_denied():
    if sys.prefix == getattr(sys, "base_prefix", sys.prefix):
        pytest.skip("not running in a virtual environment")
    _assert_startup_denied(os.path.join(sys.prefix, "bin", "activate"))
    _assert_startup_denied(os.path.join(sys.prefix, "pyvenv.cfg"))


@pytest.mark.parametrize(
    "rel",
    [
        ".config/systemd/user/hermes-extra.service",
        ".config/systemd/user/default.target.wants/x.service",
        "Library/LaunchAgents/com.example.agent.plist",
    ],
)
def test_user_autostart_dirs_are_denied(tmp_path, monkeypatch, rel):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    _assert_startup_denied(str(home / rel))


def test_ordinary_paths_stay_writable(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for rel in (".config/other/x.conf", "Library/Preferences/x.plist", "project/app.py"):
        assert fs.is_startup_code_path(str(home / rel)) is False


def test_install_roots_never_include_filesystem_root():
    assert os.sep not in fs._install_code_roots()


def test_hermes_scripts_dir_is_control_state(root_home):
    target = str(root_home / "scripts" / "job.py")
    assert fs.is_hermes_control_path(target) is True
    assert fs.is_write_denied(target) is True
    err = fs.get_write_denied_error(target)
    assert err and "Hermes control state" in err


@pytest.mark.parametrize(
    "rel",
    [
        "PLUGINS/evil/__init__.py",
        "Plugins/evil/plugin.yaml",
        "HOOKS/on_start/handler.py",
        "Scripts/job.py",
        "cron/JOBS.json",
        "CRON/jobs.json",
        "Webhook_Subscriptions.json",
        "Profile.yaml",
        "GOVERNANCE.env",
    ],
)
def test_case_folded_control_paths_denied_on_case_insensitive_fs(root_home, monkeypatch, rel):
    monkeypatch.setattr(fs.sys, "platform", "darwin")
    target = str(root_home / rel)
    assert fs.is_hermes_control_path(target) is True
    assert fs.is_write_denied(target) is True


def test_case_folded_config_denied_on_case_insensitive_fs(root_home, monkeypatch):
    import tools.file_tools as file_tools

    monkeypatch.setattr(
        file_tools, "_get_hermes_config_resolved", lambda: str(root_home / "config.yaml")
    )
    monkeypatch.setattr(fs.sys, "platform", "darwin")
    _check_sensitive_path = file_tools._check_sensitive_path
    assert _check_sensitive_path(str(root_home / "CONFIG.yaml")) is not None
    assert _check_sensitive_path(str(root_home / "Plugins" / "x.py")) is not None


def test_case_is_significant_on_case_sensitive_fs(root_home, monkeypatch):
    monkeypatch.setattr(fs.sys, "platform", "linux")
    assert fs.is_hermes_control_path(str(root_home / "PLUGINS" / "x.py")) is False
    assert fs.is_hermes_control_path(str(root_home / "plugins" / "x.py")) is True
