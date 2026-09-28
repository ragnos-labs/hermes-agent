"""``hermes cron unbound-jobs``: read-only JSON audit of jobs with no bound.

Jobs created before the toolset bound shipped carry no ``toolset_bound`` and
run under the full ``cron`` platform cap. The audit lists them so an
operator can review them before deploying. It must never write the store.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os

import pytest

from hermes_cli.cron import (
    UNBOUND_JOBS_SCHEMA,
    cron_command,
    cron_unbound_jobs,
    cron_unbound_jobs_report,
)
from hermes_cli.subcommands.cron import build_cron_parser


@pytest.fixture
def jobs_file(tmp_path, monkeypatch):
    monkeypatch.setattr("cron.jobs.CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr("cron.jobs.JOBS_FILE", tmp_path / "cron" / "jobs.json")
    monkeypatch.setattr("cron.jobs.OUTPUT_DIR", tmp_path / "cron" / "output")
    (tmp_path / "cron").mkdir()
    return tmp_path / "cron" / "jobs.json"


def _write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    os.utime(path, (1_000_000_000, 1_000_000_000))


def _fingerprint(path):
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()


MIXED = {
    "jobs": [
        {"id": "b-unbound", "name": "legacy", "prompt": "secret prompt text",
         "enabled_toolsets": ["terminal"], "created_at": "2026-01-01T00:00:00"},
        {"id": "a-bound", "name": "agent", "prompt": "p", "toolset_bound": ["web"]},
        {"id": "c-empty-bound", "prompt": "p", "toolset_bound": []},
        {"id": "a-null", "prompt": "p", "toolset_bound": None,
         "origin": {"platform": "telegram", "chat_id": "123"}, "enabled": False},
    ]
}


def test_lists_only_unbound_jobs_sorted_without_prompts(jobs_file):
    _write(jobs_file, MIXED)
    report = cron_unbound_jobs_report()
    assert report["schema"] == UNBOUND_JOBS_SCHEMA
    assert report["status"] == "unbound_jobs_found"
    assert report["total_jobs"] == 4
    assert report["unbound_count"] == 2
    assert [job["id"] for job in report["jobs"]] == ["a-null", "b-unbound"]
    first, second = report["jobs"]
    assert first["origin_platform"] == "telegram"
    assert first["enabled"] is False
    assert second["enabled_toolsets"] == ["terminal"]
    assert second["origin_platform"] is None
    text = json.dumps(report)
    assert "prompt" not in text
    assert "chat_id" not in text


@pytest.mark.parametrize(
    "data",
    [
        MIXED,
        MIXED["jobs"],
        {"jobs": {job["id"]: {k: v for k, v in job.items() if k != "id"} for job in MIXED["jobs"]}},
    ],
    ids=["wrapped", "bare_list", "id_keyed_map"],
)
def test_audit_never_writes_the_store(jobs_file, data):
    _write(jobs_file, data)
    before = _fingerprint(jobs_file)
    report = cron_unbound_jobs_report()
    assert report["unbound_count"] == 2
    assert sorted(job["id"] for job in report["jobs"]) == ["a-null", "b-unbound"]
    assert _fingerprint(jobs_file) == before


def test_exit_codes(jobs_file, capsys):
    _write(jobs_file, {"jobs": [{"id": "x", "toolset_bound": ["web"]}]})
    assert cron_unbound_jobs() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "no_unbound_jobs"
    assert out["unbound_count"] == 0

    _write(jobs_file, MIXED)
    assert cron_unbound_jobs() == 1
    assert json.loads(capsys.readouterr().out)["unbound_count"] == 2


@pytest.mark.parametrize("content", ["{not json", "42", '"text"', '{"jobs": 5}'])
def test_unreadable_store_is_never_clean(jobs_file, capsys, content):
    jobs_file.write_text(content, encoding="utf-8")
    before = _fingerprint(jobs_file)
    assert cron_unbound_jobs() == 2
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "unreadable"
    assert out["unbound_count"] is None
    assert out["total_jobs"] is None
    assert _fingerprint(jobs_file) == before


def test_missing_store_reports_no_jobs(jobs_file):
    assert not jobs_file.exists()
    report = cron_unbound_jobs_report()
    assert report["status"] == "no_unbound_jobs"
    assert report["total_jobs"] == 0
    assert not jobs_file.exists()


def test_cli_subcommand_exits_with_report_status(jobs_file, capsys):
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    build_cron_parser(subparsers, cmd_cron=cron_command)
    args = parser.parse_args(["cron", "unbound-jobs"])

    _write(jobs_file, MIXED)
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1
    assert json.loads(capsys.readouterr().out)["status"] == "unbound_jobs_found"

    _write(jobs_file, {"jobs": []})
    assert args.func(args) == 0


# -- derived names are payload text and are never printed ----------------------

SECRET_PROMPT = "sk-live-7f3a9c SECRET_PROMPT_MARKER rotate the vault token and post it"


def test_unnamed_job_prompt_never_in_output(jobs_file, capsys):
    """An unnamed job is named after its prompt; the audit withholds it."""
    from cron.jobs import create_job, get_job

    job = create_job(prompt=SECRET_PROMPT, schedule="every 1h")
    # The store derives the name from the prompt: this is what could leak.
    assert get_job(job["id"])["name"] == SECRET_PROMPT[:50].strip()

    assert cron_unbound_jobs() == 1
    out = capsys.readouterr().out
    report = json.loads(out)
    (entry,) = report["jobs"]
    assert entry["id"] == job["id"]
    assert entry["name"] is None
    assert entry["name_redacted"] is True
    for fragment in ("sk-live", "SECRET_PROMPT_MARKER", "rotate the vault"):
        assert fragment not in out


def test_derived_name_stays_redacted_after_prompt_edit(jobs_file):
    """``update_job`` keeps a derived name when the prompt changes, so the
    name no longer matches the prompt; it is still withheld."""
    from cron.jobs import create_job, get_job, update_job

    job = create_job(prompt=SECRET_PROMPT, schedule="every 1h")
    update_job(job["id"], {"prompt": "harmless replacement prompt"})
    assert get_job(job["id"])["name"] == SECRET_PROMPT[:50].strip()

    text = json.dumps(cron_unbound_jobs_report())
    assert "SECRET_PROMPT_MARKER" not in text
    assert '"name_redacted": true' in text


def test_explicit_name_is_reported(jobs_file):
    from cron.jobs import create_job, get_job, update_job

    named = create_job(prompt=SECRET_PROMPT, schedule="every 1h", name="nightly vault check")
    unnamed = create_job(prompt="another prompt body", schedule="every 1h")
    update_job(unnamed["id"], {"name": "renamed by operator"})
    # A later edit without a name keeps the marker.
    update_job(named["id"], {"prompt": "new prompt"})
    assert get_job(named["id"])["name_explicit"] is True

    by_id = {job["id"]: job for job in cron_unbound_jobs_report()["jobs"]}
    assert by_id[named["id"]]["name"] == "nightly vault check"
    assert by_id[named["id"]]["name_redacted"] is False
    assert by_id[unnamed["id"]]["name"] == "renamed by operator"
    assert by_id[unnamed["id"]]["name_redacted"] is False


@pytest.mark.parametrize(
    "job",
    [
        # Legacy job: no marker, so the name is withheld whatever it holds.
        {"id": "legacy", "name": "legacy name", "prompt": "p"},
        # A caller cannot mark a derived name explicit by writing the key.
        {"id": "prefix", "name": "sk-live-7f3a9c", "prompt": SECRET_PROMPT,
         "name_explicit": True},
        {"id": "script", "name": "backup.sh --token", "no_agent": True,
         "script": "backup.sh --token abc", "name_explicit": True},
        {"id": "truthy", "name": "n", "prompt": "p", "name_explicit": "yes"},
        {"id": "nonstr", "name": 7, "prompt": "p", "name_explicit": True},
    ],
    ids=["no_marker", "prompt_prefix", "script_prefix", "truthy_marker", "non_string"],
)
def test_name_redacted_unless_explicit(jobs_file, job):
    _write(jobs_file, {"jobs": [job]})
    (entry,) = cron_unbound_jobs_report()["jobs"]
    assert entry["name"] is None
    assert entry["name_redacted"] is True


def test_update_job_ignores_caller_supplied_marker(jobs_file):
    from cron.jobs import create_job, get_job, update_job

    job = create_job(prompt=SECRET_PROMPT, schedule="every 1h")
    update_job(job["id"], {"name_explicit": True})
    assert "name_explicit" not in get_job(job["id"])
    update_job(job["id"], {"name": "set"})
    assert get_job(job["id"])["name_explicit"] is True
    update_job(job["id"], {"name": ""})
    assert "name_explicit" not in get_job(job["id"])


@pytest.mark.parametrize("pad", ["", "  "], ids=["exact", "padded"])
def test_resent_stored_name_with_new_prompt_stays_redacted(jobs_file, pad):
    """Editors (desktop, dashboard) re-send the pre-filled stored name with
    every edit. That is not a rename, so a derived name stays withheld even
    after the prompt it came from is replaced."""
    from cron.jobs import create_job, get_job, update_job

    job = create_job(prompt=SECRET_PROMPT, schedule="every 1h")
    stored = get_job(job["id"])["name"]
    update_job(
        job["id"],
        {"prompt": "harmless replacement prompt", "name": f"{pad}{stored}{pad}"},
    )
    assert "name_explicit" not in get_job(job["id"])

    (entry,) = cron_unbound_jobs_report()["jobs"]
    assert entry["name"] is None
    assert entry["name_redacted"] is True
    text = json.dumps(cron_unbound_jobs_report())
    for fragment in ("sk-live", "SECRET_PROMPT_MARKER", "rotate the vault"):
        assert fragment not in text


def test_cli_edit_resending_stored_name_stays_redacted(jobs_file):
    """The CLI and TUI edit path (``cronjob()`` without the agent flag)."""
    from cron.jobs import create_job, get_job
    from tools.cronjob_tools import cronjob

    job = create_job(prompt=SECRET_PROMPT, schedule="every 1h")
    stored = get_job(job["id"])["name"]
    result = json.loads(
        cronjob(action="update", job_id=job["id"],
                prompt="harmless replacement prompt", name=stored)
    )
    assert result["success"] is True
    assert "name_explicit" not in get_job(job["id"])
    assert "SECRET_PROMPT_MARKER" not in json.dumps(cron_unbound_jobs_report())


def test_operator_rename_is_shown(jobs_file):
    """A real rename from an operator surface is reported, and later
    edits that re-send it keep it."""
    from cron.jobs import create_job, get_job, update_job
    from tools.cronjob_tools import cronjob

    job = create_job(prompt=SECRET_PROMPT, schedule="every 1h")
    result = json.loads(
        cronjob(action="update", job_id=job["id"],
                prompt="harmless replacement prompt", name="vault rotation")
    )
    assert result["success"] is True
    assert get_job(job["id"])["name_explicit"] is True
    update_job(job["id"], {"prompt": "second prompt", "name": "vault rotation"})
    assert get_job(job["id"])["name_explicit"] is True

    (entry,) = cron_unbound_jobs_report()["jobs"]
    assert entry["name"] == "vault rotation"
    assert entry["name_redacted"] is False


def _set_raw_name(path, job_id, name):
    """Store ``name`` exactly as given, bypassing ``update_job``. Readers
    (``get_job``, ``list_jobs``) show a derived name for null or blank."""
    data = json.loads(path.read_text(encoding="utf-8"))
    for job in data["jobs"]:
        if job["id"] == job_id:
            job["name"] = name
    path.write_text(json.dumps(data), encoding="utf-8")


def _desktop_edit(job_id, prompt):
    """The desktop editor pre-fills the name ``get_job`` shows and sends it
    back with the edited prompt."""
    from cron.jobs import get_job, update_job

    prefilled = get_job(job_id)["name"]
    update_job(job_id, {"prompt": prompt, "name": prefilled})


def _assert_secret_redacted(job_id):
    from cron.jobs import get_job

    assert "name_explicit" not in get_job(job_id)
    report = cron_unbound_jobs_report()
    (entry,) = report["jobs"]
    assert entry["name"] is None
    assert entry["name_redacted"] is True
    text = json.dumps(report)
    for fragment in ("sk-live", "SECRET_PROMPT_MARKER", "rotate the vault"):
        assert fragment not in text


@pytest.mark.parametrize("raw_name", [None, "", "  "], ids=["null", "empty", "blank"])
def test_desktop_edit_of_unnamed_job_stays_redacted(jobs_file, raw_name):
    """A null or blank stored name is shown as the first 50 characters of
    the prompt. Re-sending that shown name with a new prompt is not a
    rename, so the old prompt never reaches the audit."""
    from cron.jobs import create_job, get_job

    job = create_job(prompt=SECRET_PROMPT, schedule="every 1h")
    _set_raw_name(jobs_file, job["id"], raw_name)
    assert get_job(job["id"])["name"] == SECRET_PROMPT[:50].strip()

    _desktop_edit(job["id"], "harmless replacement prompt")
    _assert_secret_redacted(job["id"])


@pytest.mark.parametrize("named", [False, True], ids=["unnamed", "named"])
def test_clear_then_desktop_edit_stays_redacted(jobs_file, named):
    """Clearing the name and then editing the prompt from the desktop
    editor must not mark the derived name explicit."""
    from cron.jobs import create_job, update_job

    job = create_job(
        prompt=SECRET_PROMPT, schedule="every 1h",
        **({"name": "vault rotation"} if named else {}),
    )
    update_job(job["id"], {"name": ""})
    _desktop_edit(job["id"], "harmless replacement prompt")
    _assert_secret_redacted(job["id"])


def test_desktop_rename_of_unnamed_job_is_shown(jobs_file):
    """A real rename from the desktop editor is still reported."""
    from cron.jobs import create_job, get_job, update_job

    job = create_job(prompt=SECRET_PROMPT, schedule="every 1h")
    _set_raw_name(jobs_file, job["id"], None)
    update_job(job["id"], {"prompt": "harmless replacement prompt",
                           "name": "vault rotation"})
    assert get_job(job["id"])["name_explicit"] is True
    (entry,) = cron_unbound_jobs_report()["jobs"]
    assert entry["name"] == "vault rotation"
    assert entry["name_redacted"] is False


AGENT_PROMPT = "Summarize the deploy notes for AGENT_PROMPT_MARKER each hour"


def _agent_cronjob(monkeypatch, args):
    """Dispatch ``cronjob`` the way a model calls it. No enabled list and no
    platform cap, so the caller is unbounded and its jobs carry no bound
    and stay visible to the audit."""
    import tools.cronjob_tools  # noqa: F401  (registers the cronjob tool)
    from tools.registry import registry

    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"mcp_servers": {}})
    return json.loads(registry.dispatch("cronjob", args))


def test_agent_created_name_is_redacted(jobs_file, monkeypatch, capsys):
    """A model-chosen name can copy prompt content, so it is never marked."""
    from cron.jobs import get_job

    created = _agent_cronjob(monkeypatch, {
        "action": "create", "schedule": "every 1h", "prompt": AGENT_PROMPT,
        "name": "deploy notes AGENT_PROMPT_MARKER",
    })
    assert created["success"] is True
    stored = get_job(created["job_id"])
    assert stored["name"] == "deploy notes AGENT_PROMPT_MARKER"
    assert "name_explicit" not in stored
    assert stored.get("toolset_bound") is None
    capsys.readouterr()

    assert cron_unbound_jobs() == 1
    out = capsys.readouterr().out
    (entry,) = json.loads(out)["jobs"]
    assert entry["id"] == created["job_id"]
    assert entry["name"] is None
    assert entry["name_redacted"] is True
    assert "AGENT_PROMPT_MARKER" not in out


def test_agent_rename_clears_marker_and_resend_keeps_it(jobs_file, monkeypatch):
    """An agent that re-sends the whole schema keeps an operator's name
    marked; an agent that changes the name clears the marker."""
    from cron.jobs import create_job, get_job

    job = create_job(prompt="nightly check", schedule="every 1h", name="nightly vault check")
    resent = _agent_cronjob(monkeypatch, {
        "action": "update", "job_id": job["id"], "prompt": AGENT_PROMPT,
        "name": "nightly vault check",
    })
    assert resent["success"] is True
    assert get_job(job["id"])["name_explicit"] is True

    renamed = _agent_cronjob(monkeypatch, {
        "action": "update", "job_id": job["id"],
        "name": "deploy notes AGENT_PROMPT_MARKER",
    })
    assert renamed["success"] is True
    assert "name_explicit" not in get_job(job["id"])
    (entry,) = cron_unbound_jobs_report()["jobs"]
    assert entry["name"] is None
    assert entry["name_redacted"] is True


# -- the audit reads every store the scheduler reads ---------------------------

CONTROL_CHAR_STORE = '{"jobs": [{"id": "ctl", "prompt": "line one\nline two"}]}'


@pytest.mark.parametrize(
    "raw",
    [
        json.dumps(MIXED),
        json.dumps(MIXED["jobs"]),
        json.dumps({"version": 1}),
        json.dumps({}),
        CONTROL_CHAR_STORE,
        "﻿" + json.dumps(MIXED),
    ],
    ids=["wrapped", "bare_list", "dict_without_jobs", "empty_dict", "control_chars", "bom"],
)
def test_audit_agrees_with_load_jobs(jobs_file, raw):
    """The audit counts the jobs ``load_jobs()`` (the scheduler's reader)
    returns, without writing the store the way ``load_jobs()`` may."""
    from cron.jobs import load_jobs

    jobs_file.write_text(raw, encoding="utf-8")
    before = _fingerprint(jobs_file)
    report = cron_unbound_jobs_report()
    assert _fingerprint(jobs_file) == before
    assert report["status"] != "unreadable"
    assert report["total_jobs"] == len(load_jobs())


def test_control_character_store_is_audited(jobs_file, capsys):
    jobs_file.write_text(CONTROL_CHAR_STORE, encoding="utf-8")
    assert cron_unbound_jobs() == 1
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "unbound_jobs_found"
    assert [job["id"] for job in out["jobs"]] == ["ctl"]
    assert "line one" not in json.dumps(out)


def test_cron_doctor_warns_about_unbound_jobs(jobs_file, capsys):
    from cron.jobs import create_job
    from hermes_cli.cron import cron_doctor

    create_job(prompt=SECRET_PROMPT, schedule="every 1h")
    assert cron_doctor() == 0
    out = capsys.readouterr().out
    assert "1 job(s) have no recorded toolset bound" in out
    assert "hermes cron unbound-jobs" in out
    assert "SECRET_PROMPT_MARKER" not in out

    _write(jobs_file, {"jobs": [{"id": "x", "toolset_bound": ["web"],
                                 "schedule": {"kind": "interval", "minutes": 60}}]})
    cron_doctor()
    assert "no recorded toolset bound" not in capsys.readouterr().out
