"""Write-deny rules for Hermes control state under HERMES_HOME.

plugins/, hooks/, cron/jobs.json, webhook_subscriptions.json, profile.yaml
and other profiles decide which code runs and which tools the model gets, so
file tools must not write them.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import agent.file_safety as fs


@pytest.fixture
def root_home(tmp_path, monkeypatch):
    root = tmp_path / "hermes"
    (root / "profiles" / "coder").mkdir(parents=True)
    monkeypatch.setattr(fs, "_hermes_home_path", lambda: root)
    monkeypatch.setattr(fs, "_hermes_root_path", lambda: root)
    return root


@pytest.fixture
def profile_home(tmp_path, monkeypatch):
    root = tmp_path / "hermes"
    active = root / "profiles" / "coder"
    active.mkdir(parents=True)
    (root / "profiles" / "other").mkdir(parents=True)
    monkeypatch.setattr(fs, "_hermes_home_path", lambda: active)
    monkeypatch.setattr(fs, "_hermes_root_path", lambda: root)
    return root, active


@pytest.mark.parametrize(
    "rel",
    [
        "plugins/evil/__init__.py",
        "plugins/evil/plugin.yaml",
        "plugins",
        "hooks/on_start/HOOK.yaml",
        "hooks/on_start/handler.py",
        "cron/jobs.json",
        "webhook_subscriptions.json",
        "profile.yaml",
        "governance.env",
        "config.yaml",
        "profiles/coder/config.yaml",
        "profiles/coder/plugins/x.py",
        "profiles/new/profile.yaml",
    ],
)
def test_control_paths_denied_in_root_home(root_home, rel):
    target = str(root_home / rel)
    assert fs.is_hermes_control_path(target) is True
    assert fs.is_write_denied(target) is True
    err = fs.get_write_denied_error(target)
    assert err and "Hermes control state" in err


@pytest.mark.parametrize(
    "rel",
    [
        "skills/foo/SKILL.md",
        "memories/MEMORY.md",
        "cron/output/job1.md",
        "notes.txt",
        "pluginsx/readme.md",
    ],
)
def test_ordinary_home_paths_stay_writable(root_home, rel):
    assert fs.is_hermes_control_path(str(root_home / rel)) is False


def test_active_profile_home_stays_writable_except_control(profile_home):
    root, active = profile_home
    assert fs.is_hermes_control_path(str(active / "skills" / "a" / "SKILL.md")) is False
    assert fs.is_hermes_control_path(str(active / "memories" / "MEMORY.md")) is False
    assert fs.is_hermes_control_path(str(active / "sandboxes" / "docker" / "x")) is False
    for rel in ("plugins/p.py", "hooks/h/handler.py", "cron/jobs.json",
                "webhook_subscriptions.json", "profile.yaml"):
        assert fs.is_hermes_control_path(str(active / rel)) is True, rel


def test_profile_mode_denies_root_and_other_profiles(profile_home):
    root, _active = profile_home
    assert fs.is_hermes_control_path(str(root / "plugins" / "p.py")) is True
    assert fs.is_hermes_control_path(str(root / "cron" / "jobs.json")) is True
    assert fs.is_hermes_control_path(str(root / "profiles" / "other" / "skills" / "s.md")) is True
    assert fs.is_hermes_control_path(str(root / "profiles" / "fresh")) is True


def test_symlink_into_plugins_is_denied(root_home, tmp_path):
    (root_home / "plugins").mkdir()
    link = tmp_path / "innocent.py"
    link.symlink_to(root_home / "plugins" / "evil.py")
    assert fs.is_write_denied(str(link)) is True


def test_file_tools_sensitive_check_refuses_control_paths(root_home, monkeypatch):
    import tools.file_tools as ft

    monkeypatch.setattr(ft, "_get_hermes_config_resolved", lambda: None)
    err = ft._check_sensitive_path(str(root_home / "hooks" / "h" / "handler.py"))
    assert err and "Hermes control state" in err
    assert ft._check_sensitive_path(str(root_home / "webhook_subscriptions.json"))
    assert ft._check_sensitive_path(str(root_home / "skills" / "s" / "SKILL.md")) is None
    assert ft._check_sensitive_path(str(Path("/tmp") / "safe.txt")) is None


def test_config_yaml_denied_by_shared_classifier(root_home):
    """config.yaml holds the toolset caps, so the shared classifier denies it
    for every writer, not only the file tools' own config check."""
    target = str(root_home / "config.yaml")
    assert fs._classify_write_denial(target) == "hermes_control"
    assert fs.is_write_denied(target) is True
