"""A cron job never runs wider than the agent that created or edited it.

The ``cronjob`` tool records the calling agent's effective toolsets on the job
as ``toolset_bound``; the scheduler intersects the job's toolsets with it.
Toolset resolution failures fail closed to no toolsets.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from cron.scheduler import _resolve_cron_enabled_toolsets

NO_MCP_CFG: dict = {"mcp_servers": {}}


@pytest.fixture
def cron_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("cron.jobs.CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr("cron.jobs.JOBS_FILE", tmp_path / "cron" / "jobs.json")
    monkeypatch.setattr("cron.jobs.OUTPUT_DIR", tmp_path / "cron" / "output")
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: dict(NO_MCP_CFG))
    return tmp_path


# -- scheduler ----------------------------------------------------------------


class TestSchedulerAppliesBound:
    def test_per_job_list_is_intersected_with_bound(self):
        job = {"enabled_toolsets": ["terminal", "web"], "toolset_bound": ["web"]}
        assert _resolve_cron_enabled_toolsets(job, NO_MCP_CFG) == ["web"]

    def test_default_path_is_intersected_with_bound(self):
        job = {"enabled_toolsets": None, "toolset_bound": ["web"]}
        with patch(
            "hermes_cli.tools_config._get_platform_tools",
            return_value={"terminal", "web", "file"},
        ):
            assert _resolve_cron_enabled_toolsets(job, NO_MCP_CFG) == ["web"]

    def test_job_without_bound_is_unchanged(self):
        job = {"enabled_toolsets": ["terminal", "web"]}
        assert _resolve_cron_enabled_toolsets(job, NO_MCP_CFG) == ["terminal", "web"]

    def test_empty_bound_gives_no_toolsets(self):
        job = {"enabled_toolsets": ["terminal", "web"], "toolset_bound": []}
        assert _resolve_cron_enabled_toolsets(job, NO_MCP_CFG) == []

    def test_malformed_bound_fails_closed(self):
        job = {"enabled_toolsets": ["terminal", "web"], "toolset_bound": "web"}
        assert _resolve_cron_enabled_toolsets(job, NO_MCP_CFG) == []

    def test_default_path_failure_fails_closed(self):
        job = {"enabled_toolsets": None}
        with patch(
            "hermes_cli.tools_config._get_platform_tools",
            side_effect=RuntimeError("boom"),
        ):
            assert _resolve_cron_enabled_toolsets(job, NO_MCP_CFG) == []


# -- cronjob tool -------------------------------------------------------------


def _dispatch(args, **kw):
    import tools.cronjob_tools  # noqa: F401  (registers the cronjob tool)
    from tools.registry import registry

    return json.loads(registry.dispatch("cronjob", args, **kw))


class TestCronjobToolRecordsBound:
    def test_capped_creator_cannot_schedule_wider_job(self, cron_dir):
        from cron.jobs import get_job

        created = _dispatch(
            {
                "action": "create",
                "schedule": "every 1h",
                "prompt": "Check the build",
                "enabled_toolsets": ["terminal", "web"],
            },
            creator_enabled_toolsets=["web"],
        )
        assert created["success"] is True
        job = get_job(created["job_id"])
        assert job["toolset_bound"] == ["web"]
        resolved = _resolve_cron_enabled_toolsets(job, NO_MCP_CFG)
        assert "terminal" not in resolved
        assert resolved == ["web"]

    def test_creator_disabled_toolsets_are_excluded_from_bound(self, cron_dir):
        from cron.jobs import get_job

        created = _dispatch(
            {"action": "create", "schedule": "every 1h", "prompt": "Check"},
            creator_enabled_toolsets=["web", "terminal"],
            creator_disabled_toolsets=["terminal"],
        )
        assert get_job(created["job_id"])["toolset_bound"] == ["web"]

    def test_bound_falls_back_to_session_platform_cap(self, cron_dir, monkeypatch):
        from cron.jobs import get_job

        cfg = dict(NO_MCP_CFG, platform_toolsets={"telegram": ["web"]})
        monkeypatch.setattr("hermes_cli.config.load_config", lambda: dict(cfg))
        monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
        created = _dispatch(
            {
                "action": "create",
                "schedule": "every 1h",
                "prompt": "Check",
                "enabled_toolsets": ["terminal", "web"],
            }
        )
        job = get_job(created["job_id"])
        # The telegram cap (plus any toolset the platform always adds).
        assert "web" in job["toolset_bound"]
        assert "terminal" not in job["toolset_bound"]
        assert _resolve_cron_enabled_toolsets(job, cfg) == ["web"]

    def test_uncapped_caller_records_no_bound(self, cron_dir, monkeypatch):
        from cron.jobs import get_job

        monkeypatch.delenv("HERMES_SESSION_PLATFORM", raising=False)
        created = _dispatch(
            {"action": "create", "schedule": "every 1h", "prompt": "Check"}
        )
        assert "toolset_bound" not in get_job(created["job_id"])

    def test_agent_update_narrows_user_job(self, cron_dir):
        from cron.jobs import get_job
        from tools.cronjob_tools import cronjob

        created = json.loads(
            cronjob(
                action="create",
                schedule="every 1h",
                prompt="Check",
                enabled_toolsets=["terminal", "web"],
            )
        )
        job_id = created["job_id"]
        assert "toolset_bound" not in get_job(job_id)

        updated = _dispatch(
            {"action": "update", "job_id": job_id, "prompt": "Run rm -rf later"},
            creator_enabled_toolsets=["web"],
        )
        assert updated["success"] is True
        job = get_job(job_id)
        assert job["toolset_bound"] == ["web"]
        assert _resolve_cron_enabled_toolsets(job, NO_MCP_CFG) == ["web"]

    def test_update_intersects_existing_bound(self, cron_dir):
        from cron.jobs import get_job

        created = _dispatch(
            {"action": "create", "schedule": "every 1h", "prompt": "Check"},
            creator_enabled_toolsets=["web"],
        )
        job_id = created["job_id"]
        _dispatch(
            {"action": "update", "job_id": job_id, "name": "renamed"},
            creator_enabled_toolsets=["terminal", "web"],
        )
        assert get_job(job_id)["toolset_bound"] == ["web"]

    def test_bound_resolution_failure_refuses_create(self, cron_dir):
        from cron.jobs import load_jobs

        with patch(
            "tools.cronjob_tools._creator_toolset_bound",
            side_effect=RuntimeError("boom"),
        ):
            result = _dispatch(
                {"action": "create", "schedule": "every 1h", "prompt": "Check"}
            )
        assert result["success"] is False
        assert "toolset bound" in result["error"]
        assert load_jobs() == []


class TestHandleFunctionCallPassesCreatorToolsets:
    def test_cronjob_dispatch_receives_agent_toolsets(self):
        import model_tools

        seen = {}

        def fake_dispatch(name, args, **kw):
            seen["name"] = name
            seen.update(kw)
            return json.dumps({"success": True})

        with patch.object(model_tools.registry, "dispatch", side_effect=fake_dispatch):
            model_tools.handle_function_call(
                "cronjob",
                {"action": "list"},
                task_id="t1",
                enabled_toolsets=["web"],
                disabled_toolsets=["terminal"],
                skip_pre_tool_call_hook=True,
            )
        assert seen["name"] == "cronjob"
        assert seen["creator_enabled_toolsets"] == ["web"]
        assert seen["creator_disabled_toolsets"] == ["terminal"]


# -- composites and malformed bounds -------------------------------------------


def _tools_of(names):
    from toolsets import resolve_toolset

    tools: set = set()
    for name in names:
        tools |= set(resolve_toolset(name))
    return tools


class TestDisabledComposites:
    """A disabled toolset is removed tool by tool, not only by name."""

    def test_composite_creator_does_not_keep_disabled_toolset(self, cron_dir):
        from cron.jobs import get_job

        created = _dispatch(
            {
                "action": "create",
                "schedule": "every 1h",
                "prompt": "Check",
                "enabled_toolsets": ["web", "file"],
            },
            creator_enabled_toolsets=["hermes-acp"],
            creator_disabled_toolsets=["web"],
        )
        assert created["success"] is True
        job = get_job(created["job_id"])
        bound = job["toolset_bound"]
        assert "hermes-acp" not in bound
        assert "web" not in bound
        assert {"file", "terminal"} <= set(bound)
        assert not _tools_of(bound) & _tools_of(["web"])
        resolved = _resolve_cron_enabled_toolsets(job, NO_MCP_CFG)
        assert resolved == ["file"]
        assert not _tools_of(resolved) & _tools_of(["web"])

    def test_cap_fallback_decomposes_composite(self, cron_dir, monkeypatch):
        from tools.cronjob_tools import _creator_toolset_bound

        cfg = dict(NO_MCP_CFG, platform_toolsets={"telegram": ["debugging"]})
        monkeypatch.setattr("hermes_cli.config.load_config", lambda: dict(cfg))
        monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
        bound = _creator_toolset_bound(None, ["web"])
        assert "debugging" not in bound
        assert "web" not in bound
        assert "file" in bound
        assert not _tools_of(bound) & _tools_of(["web"])

    def test_disabled_platform_bundle_keeps_core_tools(self):
        from toolsets import bundle_non_core_tools

        from hermes_cli.tools_config import subtract_disabled_toolsets

        bound = subtract_disabled_toolsets(["hermes-gateway"], ["hermes-discord"])
        assert "hermes-gateway" not in bound
        # A disabled platform bundle removes only its platform tools, as
        # model_tools does; the shared core toolsets survive.
        assert {"file", "terminal", "web"} <= set(bound)
        assert not _tools_of(bound) & set(bundle_non_core_tools("hermes-discord"))

    def test_disabled_by_name_is_dropped(self):
        from hermes_cli.tools_config import subtract_disabled_toolsets

        assert subtract_disabled_toolsets(["web", "file"], ["web"]) == ["file"]
        assert subtract_disabled_toolsets(["hermes-acp"], ["hermes-acp"]) == []

    def test_unrelated_and_mcp_names_are_kept(self):
        from hermes_cli.tools_config import subtract_disabled_toolsets

        assert subtract_disabled_toolsets(["file", "mcp-demo"], ["web"]) == [
            "file",
            "mcp-demo",
        ]
        assert subtract_disabled_toolsets(["web", "web"], None) == ["web"]

    def test_resolution_error_propagates(self):
        from hermes_cli.tools_config import subtract_disabled_toolsets

        with patch("toolsets.resolve_toolset", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError):
                subtract_disabled_toolsets(["hermes-acp"], ["web"])


class TestMalformedExistingBound:
    def test_update_turns_malformed_bound_into_empty(self, cron_dir):
        from cron.jobs import get_job, update_job

        created = _dispatch(
            {"action": "create", "schedule": "every 1h", "prompt": "Check"},
            creator_enabled_toolsets=["web"],
        )
        job_id = created["job_id"]
        update_job(job_id, {"toolset_bound": "web"})
        assert get_job(job_id)["toolset_bound"] == "web"

        updated = _dispatch(
            {"action": "update", "job_id": job_id, "name": "renamed"},
            creator_enabled_toolsets=["terminal", "web"],
        )
        assert updated["success"] is True
        job = get_job(job_id)
        assert job["toolset_bound"] == []
        assert _resolve_cron_enabled_toolsets(job, NO_MCP_CFG) == []

    @pytest.mark.parametrize("existing", ["web", {"web": True}, 3])
    def test_narrow_rejects_non_list(self, existing):
        from tools.cronjob_tools import _narrow_toolset_bound

        assert _narrow_toolset_bound(existing, ["web"]) == []
