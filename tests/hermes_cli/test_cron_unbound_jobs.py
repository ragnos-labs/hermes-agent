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


# The characters JavaScript's String.prototype.trim removes. The desktop and
# dashboard editors trim with it, and it removes U+FEFF, which str.strip keeps.
JS_TRIM_CHARS = (
    "\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006"
    "\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
)


def _js_editor_edit(job_id, prompt):
    """An editor that re-sends the shown name trimmed the JavaScript way.

    The desktop and dashboard now send the name only when it was edited,
    but an older client, or one that opened the job before this change,
    still re-sends it with every save.
    """
    from cron.jobs import get_job, update_job

    shown = get_job(job_id)["name"]
    update_job(job_id, {"prompt": prompt.strip(JS_TRIM_CHARS),
                        "name": shown.strip(JS_TRIM_CHARS)})


@pytest.mark.parametrize("raw_name", [None, "keep"], ids=["null", "stored"])
@pytest.mark.parametrize(
    "prompt",
    [
        "\ufeff" + SECRET_PROMPT,
        "\ufeff \ufeff" + SECRET_PROMPT,
        SECRET_PROMPT[:49] + "\ufeff" + SECRET_PROMPT[49:],
        SECRET_PROMPT[:48] + " \ufeff" + SECRET_PROMPT[50:],
    ],
    ids=["leading_bom", "mixed_leading", "bom_at_49", "space_bom_at_48"],
)
def test_bom_edge_in_derived_name_stays_redacted(jobs_file, prompt, raw_name):
    """U+FEFF at either edge of the derived name is trimmed like whitespace,
    so the name a JavaScript editor trims and sends back compares equal."""
    from cron.jobs import create_job, get_job

    job = create_job(prompt=prompt, schedule="every 1h")
    if raw_name is None:
        _set_raw_name(jobs_file, job["id"], None)
    shown = get_job(job["id"])["name"]
    assert shown == shown.strip(JS_TRIM_CHARS)
    assert "sk-live" in shown

    _js_editor_edit(job["id"], "harmless replacement prompt")
    _assert_secret_redacted(job["id"])


@pytest.mark.parametrize("blank", ["\ufeff", " \ufeff\u3000"], ids=["bom", "mixed"])
def test_bom_only_name_counts_as_blank(jobs_file, blank):
    """A name of only U+FEFF and whitespace is blank: it is not marked on
    create, it clears the marker on update, and the audit withholds it."""
    from cron.jobs import create_job, get_job, update_job

    job = create_job(prompt=SECRET_PROMPT, schedule="every 1h", name=blank)
    assert "name_explicit" not in get_job(job["id"])
    assert get_job(job["id"])["name"] == SECRET_PROMPT[:50].strip()

    update_job(job["id"], {"name": "vault rotation"})
    assert get_job(job["id"])["name_explicit"] is True
    update_job(job["id"], {"name": blank})
    assert "name_explicit" not in get_job(job["id"])
    _assert_secret_redacted(job["id"])


def test_audit_withholds_marked_bom_only_name(jobs_file):
    """A legacy record marked explicit with a U+FEFF-only name, or with a
    name the BOM-prefixed prompt starts with, is still withheld."""
    _write(jobs_file, {"jobs": [
        {"id": "a", "name": "\ufeff ", "prompt": "p", "name_explicit": True},
        {"id": "b", "name": "sk-live-7f3a9c", "prompt": "\ufeff" + SECRET_PROMPT,
         "name_explicit": True},
    ]})
    for entry in cron_unbound_jobs_report()["jobs"]:
        assert entry["name"] is None
        assert entry["name_redacted"] is True


@pytest.mark.parametrize(
    "prompt",
    [SECRET_PROMPT[:49] + " " + SECRET_PROMPT[49:], "   " + SECRET_PROMPT],
    ids=["space_at_49", "leading_space"],
)
def test_derived_name_is_trimmed(jobs_file, prompt):
    """Whitespace at the 50-character cut or before the prompt is not part
    of the derived name, so a trimmed re-send is not a rename. The prompt
    is written raw because ``create_job`` strips it; a hand-edited or
    older store can still hold leading whitespace."""
    from cron.jobs import create_job, get_job

    job = create_job(prompt=SECRET_PROMPT, schedule="every 1h")
    data = json.loads(jobs_file.read_text(encoding="utf-8"))
    data["jobs"][0].update(prompt=prompt, name=None)
    jobs_file.write_text(json.dumps(data), encoding="utf-8")
    assert get_job(job["id"])["name"] == prompt[:50].strip()
    assert get_job(job["id"])["name"] != prompt[:50]

    _js_editor_edit(job["id"], "harmless replacement prompt")
    _assert_secret_redacted(job["id"])


def test_padded_agent_name_is_shown_trimmed_and_stays_unmarked(jobs_file):
    """Readers show a stored name trimmed. An editor that re-sends it does
    not rename, so a padded agent-chosen name is not marked explicit."""
    from cron.jobs import create_job, get_job

    job = create_job(prompt=SECRET_PROMPT, schedule="every 1h",
                     name=" vault ", mark_name_explicit=False)
    assert get_job(job["id"])["name"] == "vault"

    _js_editor_edit(job["id"], "harmless replacement prompt")
    assert "name_explicit" not in get_job(job["id"])
    (entry,) = cron_unbound_jobs_report()["jobs"]
    assert entry["name"] is None
    assert entry["name_redacted"] is True


def test_case_only_rename_is_a_rename(jobs_file):
    """The name comparison is case-sensitive: a case-only operator rename
    sets the marker and a case-only agent rename clears it."""
    from cron.jobs import create_job, get_job, update_job

    job = create_job(prompt=SECRET_PROMPT, schedule="every 1h",
                     name="vault rotation", mark_name_explicit=False)
    update_job(job["id"], {"name": "Vault Rotation"})
    assert get_job(job["id"])["name_explicit"] is True
    (entry,) = cron_unbound_jobs_report()["jobs"]
    assert entry["name"] == "Vault Rotation"

    update_job(job["id"], {"name": "VAULT ROTATION"}, mark_name_explicit=False)
    assert "name_explicit" not in get_job(job["id"])
    (entry,) = cron_unbound_jobs_report()["jobs"]
    assert entry["name"] is None


def test_edit_without_name_after_agent_prompt_change_stays_redacted(jobs_file):
    """The stale-editor case. The editor opened an unnamed job, an agent
    then replaced the prompt, and the operator saved a prompt edit. The
    desktop and dashboard send ``name`` only when it was edited, so the
    save carries no name and the old derived name is never marked."""
    from cron.jobs import create_job, update_job

    job = create_job(prompt=SECRET_PROMPT, schedule="every 1h")
    _set_raw_name(jobs_file, job["id"], None)
    update_job(job["id"], {"prompt": "agent replacement prompt"},
               mark_name_explicit=False)
    update_job(job["id"], {"prompt": "agent replacement prompt, edited"})
    _assert_secret_redacted(job["id"])


# -- a client that loaded the job before a payload edit ----------------------
#
# Older desktop builds and third-party API clients re-send the name they
# showed with every save. When the stored name was blank, that name was
# derived from the prompt, and it used to follow every prompt edit, so a
# client that loaded the job before an agent changed the prompt re-sent the
# old prompt's first 50 characters, which counted as a rename. The shown name
# is now pinned, so the re-sent name is the stored name.


def _blank_stored_name(path, job_id, how):
    from cron.jobs import update_job

    if how == "cleared":
        update_job(job_id, {"name": ""})
        return
    # A legacy record: blank name and no marker.
    data = json.loads(path.read_text(encoding="utf-8"))
    for job in data["jobs"]:
        if job["id"] == job_id:
            job["name"] = {"null": None, "empty": "", "blank": " \ufeff "}[how]
            job.pop("name_explicit", None)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize("how", ["cleared", "null", "empty", "blank"])
@pytest.mark.parametrize(
    "resend",
    [{}, {"prompt": "operator edit of the stale form"}, {"enabled": False}],
    ids=["name_only", "with_prompt", "with_other_field"],
)
def test_stale_resend_after_agent_prompt_change_stays_redacted(jobs_file, how, resend):
    from cron.jobs import create_job, get_job, update_job

    job = create_job(prompt=SECRET_PROMPT, schedule="every 1h", name="vault rotation")
    _blank_stored_name(jobs_file, job["id"], how)
    shown = get_job(job["id"])["name"]
    assert shown == SECRET_PROMPT[:50].strip()

    update_job(job["id"], {"prompt": "agent replacement prompt"},
               mark_name_explicit=False)
    assert get_job(job["id"])["name"] == shown
    update_job(job["id"], {"name": shown, **resend})
    _assert_secret_redacted(job["id"])


@pytest.mark.parametrize(
    "updates",
    [{"prompt": "new prompt"}, {"prompt": "", "skills": ["daily-digest"]}],
    ids=["prompt", "skills"],
)
def test_payload_edit_pins_a_blank_name(jobs_file, updates):
    """A payload edit that would change the derived name stores the name
    readers showed before it, without the marker."""
    from cron.jobs import create_job, get_job, update_job

    job = create_job(prompt=SECRET_PROMPT, schedule="every 1h")
    _set_raw_name(jobs_file, job["id"], None)
    update_job(job["id"], updates)
    stored = json.loads(jobs_file.read_text(encoding="utf-8"))["jobs"][0]
    assert stored["name"] == SECRET_PROMPT[:50].strip()
    assert "name_explicit" not in stored
    assert get_job(job["id"])["name"] == SECRET_PROMPT[:50].strip()


def test_edit_that_keeps_the_derived_name_leaves_a_blank_name_alone(jobs_file):
    from cron.jobs import create_job, update_job

    job = create_job(prompt=SECRET_PROMPT, schedule="every 1h")
    _set_raw_name(jobs_file, job["id"], None)
    update_job(job["id"], {"enabled": False, "prompt": SECRET_PROMPT + " more"})
    assert json.loads(jobs_file.read_text(encoding="utf-8"))["jobs"][0]["name"] is None


def _stored_record(path):
    (stored,) = json.loads(path.read_text(encoding="utf-8"))["jobs"]
    return stored


def test_skills_edit_without_prompt_pins_the_old_first_skill(jobs_file):
    """A skills-only job shows its first skill. Editing the skills, with no
    prompt key in the update, pins the skill readers showed before."""
    from cron.jobs import create_job, get_job, update_job

    job = create_job(prompt="", schedule="every 1h", skills=["  old-digest  "])
    _set_raw_name(jobs_file, job["id"], None)
    assert get_job(job["id"])["name"] == "old-digest"
    update_job(job["id"], {"skills": ["new-digest"]})
    stored = _stored_record(jobs_file)
    assert stored["name"] == "old-digest"
    assert "name_explicit" not in stored
    assert get_job(job["id"])["name"] == "old-digest"


def test_script_edit_without_prompt_pins_the_old_script(jobs_file):
    """A script-only job shows the first 50 characters of its script.
    Editing the script, with no prompt key in the update, pins that name."""
    from cron.jobs import create_job, get_job, update_job

    old_script = "  " + "old-collector-" * 5 + ".sh"
    job = create_job(prompt=None, schedule="every 1h", script=old_script,
                     no_agent=True)
    _set_raw_name(jobs_file, job["id"], None)
    shown = old_script.strip()[:50]
    assert get_job(job["id"])["name"] == shown
    update_job(job["id"], {"script": "new-collector.sh"})
    stored = _stored_record(jobs_file)
    assert stored["name"] == shown
    assert "name_explicit" not in stored
    assert get_job(job["id"])["name"] == shown


@pytest.mark.parametrize("raw_name", [None, "", " \ufeff "], ids=["null", "empty", "blank"])
def test_pin_drops_a_stale_marker_on_a_blank_name(jobs_file, raw_name):
    """A hand edit or a foreign writer can leave ``name_explicit: true`` on a
    blank name. The pinned name is derived, so the update drops the marker and
    the audit keeps the old prompt prefix redacted."""
    from cron.jobs import create_job, update_job

    job = create_job(prompt=SECRET_PROMPT, schedule="every 1h")
    data = json.loads(jobs_file.read_text(encoding="utf-8"))
    data["jobs"][0]["name"] = raw_name
    data["jobs"][0]["name_explicit"] = True
    jobs_file.write_text(json.dumps(data), encoding="utf-8")

    update_job(job["id"], {"prompt": "harmless replacement prompt"},
               mark_name_explicit=False)
    stored = _stored_record(jobs_file)
    assert stored["name"] == SECRET_PROMPT[:50].strip()
    assert "name_explicit" not in stored
    _assert_secret_redacted(job["id"])


def _stale_marker_on_blank_name(path, raw_name):
    """A hand edit or a foreign writer left ``name_explicit: true`` on a
    blank name. Readers show the first 50 characters of the prompt."""
    from cron.jobs import create_job, get_job

    job = create_job(prompt=SECRET_PROMPT, schedule="every 1h")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["jobs"][0]["name"] = raw_name
    data["jobs"][0]["name_explicit"] = True
    path.write_text(json.dumps(data), encoding="utf-8")
    shown = get_job(job["id"])["name"]
    assert shown == SECRET_PROMPT[:50].strip()
    return job["id"], shown


def _assert_prefix_not_explicit(path, job_id, shown):
    stored = _stored_record(path)
    assert stored["name"] in (None, "", " \ufeff ", shown)
    assert "name_explicit" not in stored
    _assert_secret_redacted(job_id)


@pytest.mark.parametrize("raw_name", [None, "", " \ufeff "], ids=["null", "empty", "blank"])
def test_operator_resend_of_shown_name_ignores_a_stale_marker(jobs_file, raw_name):
    """An older client re-sends the name it was shown with a prompt edit.
    The stale marker on the blank name must not make that name explicit."""
    from cron.jobs import update_job

    job_id, shown = _stale_marker_on_blank_name(jobs_file, raw_name)
    update_job(job_id, {"prompt": "harmless replacement prompt", "name": shown})
    assert _stored_record(jobs_file)["name"] == shown
    _assert_prefix_not_explicit(jobs_file, job_id, shown)


@pytest.mark.parametrize("raw_name", [None, "", " \ufeff "], ids=["null", "empty", "blank"])
def test_agent_resend_of_shown_name_ignores_a_stale_marker(jobs_file, monkeypatch, raw_name):
    """The ``cronjob`` tool re-sends the whole schema, shown name included."""
    job_id, shown = _stale_marker_on_blank_name(jobs_file, raw_name)
    resent = _agent_cronjob(monkeypatch, {
        "action": "update", "job_id": job_id,
        "prompt": "harmless replacement prompt", "name": shown,
    })
    assert resent["success"] is True
    assert _stored_record(jobs_file)["name"] == shown
    _assert_prefix_not_explicit(jobs_file, job_id, shown)


@pytest.mark.parametrize("raw_name", [None, "", " \ufeff "], ids=["null", "empty", "blank"])
def test_resend_then_later_prompt_edit_ignores_a_stale_marker(jobs_file, raw_name):
    """A schedule-only edit re-sends the shown name and stores it; a later
    prompt edit must not find an explicit name to keep."""
    from cron.jobs import update_job

    job_id, shown = _stale_marker_on_blank_name(jobs_file, raw_name)
    update_job(job_id, {"schedule": "every 2h", "name": shown})
    _assert_prefix_not_explicit(jobs_file, job_id, shown)
    update_job(job_id, {"prompt": "harmless replacement prompt"})
    assert _stored_record(jobs_file)["name"] == shown
    _assert_prefix_not_explicit(jobs_file, job_id, shown)


@pytest.mark.parametrize("cleared", ["", None, " \ufeff"], ids=["empty", "null", "blank"])
def test_clear_names_the_job_after_its_current_prompt(jobs_file, cleared):
    """After a clear, readers show the name derived from the updated payload
    and the marker is gone; a later prompt edit pins that name."""
    from cron.jobs import create_job, get_job, update_job

    job = create_job(prompt="old prompt", schedule="every 1h", name="vault rotation")
    assert get_job(job["id"])["name_explicit"] is True
    update_job(job["id"], {"name": cleared, "prompt": SECRET_PROMPT})
    shown = get_job(job["id"])
    assert shown["name"] == SECRET_PROMPT[:50].strip()
    assert "name_explicit" not in shown
    # A later prompt edit keeps that name, and re-sending it is no rename.
    update_job(job["id"], {"prompt": "harmless replacement prompt"},
               mark_name_explicit=False)
    assert get_job(job["id"])["name"] == shown["name"]
    update_job(job["id"], {"name": shown["name"]})
    _assert_secret_redacted(job["id"])


def test_rename_and_case_only_rename_after_pin_are_shown(jobs_file):
    from cron.jobs import create_job, get_job, update_job

    job = create_job(prompt=SECRET_PROMPT, schedule="every 1h")
    _set_raw_name(jobs_file, job["id"], None)
    update_job(job["id"], {"prompt": "agent replacement prompt"},
               mark_name_explicit=False)
    update_job(job["id"], {"name": "vault rotation"})
    assert get_job(job["id"])["name_explicit"] is True
    update_job(job["id"], {"name": "Vault Rotation"}, mark_name_explicit=False)
    assert "name_explicit" not in get_job(job["id"])
    update_job(job["id"], {"name": "VAULT ROTATION"})
    (entry,) = cron_unbound_jobs_report()["jobs"]
    assert entry["name"] == "VAULT ROTATION"
    assert entry["name_redacted"] is False


def test_unnamed_create_keeps_its_stored_derived_name(jobs_file):
    """``create_job`` still stores the derived name without the marker, and
    a prompt edit keeps it."""
    from cron.jobs import create_job, update_job

    job = create_job(prompt=SECRET_PROMPT, schedule="every 1h")
    before = json.loads(jobs_file.read_text(encoding="utf-8"))["jobs"][0]
    assert before["name"] == SECRET_PROMPT[:50].strip()
    assert "name_explicit" not in before
    update_job(job["id"], {"prompt": "harmless replacement prompt"})
    after = json.loads(jobs_file.read_text(encoding="utf-8"))["jobs"][0]
    assert after["name"] == before["name"]
    assert "name_explicit" not in after


# -- Unicode normalization in the audit's name guard -------------------------

NFC_PROMPT = "Caf\u00e9 r\u00e9sum\u00e9 SECRET_PROMPT_MARKER rotate the vault token now"


def _nfd(text):
    import unicodedata

    return unicodedata.normalize("NFD", text)


def _nfc(text):
    import unicodedata

    return unicodedata.normalize("NFC", text)


# A decomposed prompt cut after the "e" of a decomposed "\u00e9" at 49/50.
_CUT_PROMPT = (
    _nfd("Caf\u00e9 SECRET_PROMPT_MARKER rotate the vault").ljust(49, "_")
    + "e\u0301 token now"
)
assert _CUT_PROMPT[49:51] == "e\u0301"


@pytest.mark.parametrize(
    ("prompt", "name"),
    [
        (NFC_PROMPT, _nfd(NFC_PROMPT[:40])),
        (_nfd(NFC_PROMPT), NFC_PROMPT[:40]),
        ("\uff33\uff25\uff23\uff32\uff25\uff34 SECRET_PROMPT_MARKER", "SECRET SECRET_PROMPT"),
        (_CUT_PROMPT, _nfc(_CUT_PROMPT[:50])),
        ("Cafe\u0325\u0301 SECRET_PROMPT_MARKER", "Caf\u00e9"),
        ("Caf\uff45\u0325\u0301 SECRET_PROMPT_MARKER", "Caf\u00e9"),
    ],
    ids=[
        "nfd_name_nfc_prompt",
        "nfc_name_nfd_prompt",
        "fullwidth_prompt",
        "cut_combining_mark",
        "reordered_marks",
        "fullwidth_reordered_marks",
    ],
)
def test_prefix_guard_normalizes_unicode(jobs_file, prompt, name):
    """A name that is the start of the prompt in another Unicode form is
    still the prompt's text and is withheld."""
    assert not prompt.startswith(name)
    _write(jobs_file, {"jobs": [
        {"id": "a", "name": name, "prompt": prompt, "name_explicit": True},
        {"id": "b", "name": name, "prompt": "p", "no_agent": True,
         "script": prompt, "name_explicit": True},
    ]})
    for entry in cron_unbound_jobs_report()["jobs"]:
        assert entry["name"] is None
        assert entry["name_redacted"] is True


def test_normalized_rename_through_update_job_is_withheld(jobs_file):
    """An operator surface that sends the shown name decomposed renames the
    job as far as ``update_job`` is concerned; the audit still withholds it."""
    from cron.jobs import create_job, get_job, update_job

    job = create_job(prompt=NFC_PROMPT, schedule="every 1h")
    update_job(job["id"], {"name": _nfd(get_job(job["id"])["name"])})
    assert get_job(job["id"])["name_explicit"] is True
    (entry,) = cron_unbound_jobs_report()["jobs"]
    assert entry["name"] is None


def test_prefix_guard_keeps_distinct_accented_names(jobs_file):
    _write(jobs_file, {"jobs": [
        {"id": "a", "name": "r\u00e9sum\u00e9 digest", "prompt": NFC_PROMPT,
         "name_explicit": True},
    ]})
    (entry,) = cron_unbound_jobs_report()["jobs"]
    assert entry["name"] == "r\u00e9sum\u00e9 digest"


AGENT_PROMPT ="Summarize the deploy notes for AGENT_PROMPT_MARKER each hour"


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
