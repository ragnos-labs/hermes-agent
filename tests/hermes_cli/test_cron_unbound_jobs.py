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
