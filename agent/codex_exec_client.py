"""Model-only Codex CLI transport for Hermes' native agent loop.

The CLI produces a schema-checked assistant response. It never executes the
forwarded tools: Hermes remains responsible for dispatch, memory and sessions.
No SDK endpoint, token extraction, or provider fallback is used here.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from agent.acp_openai_bridge import build_openai_tool_call, completion_to_stream_chunks

BASE_URL = "codex-exec://local"
_MAX_BYTES = 8_000_000
_CALL_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_INSTRUCTIONS = (
    "You supply one assistant response for the Hermes agent loop. "
    "The following JSON contains the authoritative conversation, available "
    "function definitions and response constraints. Follow the conversation's "
    "system/developer instructions. Request actions ONLY through the tool_calls "
    "array in your final JSON; Hermes executes them and supplies their results "
    "on the next request. Never execute tools yourself or claim an unexecuted "
    "action succeeded. Arguments must be JSON encoded as a string. Return the "
    "required JSON envelope, using an empty tool_calls array for a final answer."
)


class CodexExecError(RuntimeError):
    """A failed CLI response is never usable as a partial assistant response."""

    def __init__(self, code: str, message: str, *, reset_at: int | None = None):
        super().__init__(message)
        self.code = code
        self.reset_at = reset_at


def load_settings() -> dict[str, Any]:
    from hermes_cli.config import load_config_readonly

    settings = load_config_readonly().get("codex_exec", {})
    if not isinstance(settings, dict):
        raise ValueError("codex_exec configuration must be an object")
    return dict(settings)


def resolve_process() -> dict[str, Any]:
    settings = load_settings()
    command = settings.get("command", "codex")
    if not isinstance(command, str) or not command:
        raise ValueError("codex_exec.command must name an executable")
    resolved = shutil.which(command)
    if resolved is None:
        raise CodexExecError("command_unavailable", "Codex CLI executable is unavailable")
    return {
        "provider": "codex_exec", "api_key": "codex-exec", "base_url": BASE_URL,
        "command": resolved, "args": [], "source": "process",
    }


def _json(text: str) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def constant(_value):
        raise ValueError("non-finite JSON number")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def response_schema() -> dict[str, Any]:
    return {
        "type": "object", "additionalProperties": False,
        "required": ["content", "tool_calls"],
        "properties": {
            "content": {"type": ["string", "null"]},
            "tool_calls": {
                "type": "array", "maxItems": 32,
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["id", "name", "arguments"],
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                        "arguments": {"type": "string"},
                    },
                },
            },
        },
    }


def _validate(value: Any, schema: dict[str, Any]) -> None:
    try:
        from jsonschema.validators import validator_for
        from referencing import Registry
        from referencing.exceptions import NoSuchResource
    except ImportError as exc:
        raise CodexExecError(
            "dependency_unavailable", "Install the hermes-agent[codex-exec] extra"
        ) from exc

    # Tool schemas cannot cause the validator to retrieve remote references.
    def deny_reference(uri):
        raise NoSuchResource(ref=uri)

    try:
        validator = validator_for(schema)
        validator.check_schema(schema)
        validator(schema, registry=Registry(retrieve=deny_reference)).validate(value)
    except Exception as exc:
        raise CodexExecError("invalid_response", "Codex output does not match its schema") from exc


def decode_response(text: str, request: dict[str, Any]) -> SimpleNamespace:
    try:
        result = _json(text)
    except (ValueError, TypeError) as exc:
        raise CodexExecError("invalid_response", "Codex returned malformed JSON") from exc
    _validate(result, response_schema())
    tools = {}
    for item in request.get("tools") or []:
        if item.get("type") != "function" or not isinstance(item.get("function"), dict):
            raise CodexExecError("unsupported_request", "Only function tools are supported")
        definition = item["function"]
        name = definition.get("name")
        if not isinstance(name, str) or name in tools:
            raise CodexExecError("unsupported_request", "Invalid or duplicate tool definition")
        tools[name] = definition
    calls = []
    ids = set()
    for item in result["tool_calls"]:
        if item["name"] not in tools or not _CALL_ID.fullmatch(item["id"]) or item["id"] in ids:
            raise CodexExecError("invalid_response", "Codex returned an unknown tool or invalid call ID")
        try:
            arguments = _json(item["arguments"])
        except (ValueError, TypeError) as exc:
            raise CodexExecError("invalid_response", "Malformed tool arguments") from exc
        if not isinstance(arguments, dict):
            raise CodexExecError("invalid_response", "Tool arguments must be an object")
        _validate(arguments, tools[item["name"]].get("parameters", {"type": "object"}))
        ids.add(item["id"])
        calls.append(build_openai_tool_call(
            call_id=item["id"], name=item["name"], arguments=item["arguments"],
        ))
    choice = request.get("tool_choice", "auto")
    if choice == "none" and calls or choice == "required" and not calls:
        raise CodexExecError("invalid_response", "Codex did not respect tool_choice")
    if isinstance(choice, dict):
        name = (choice.get("function") or {}).get("name")
        if not calls or any(call.function.name != name for call in calls):
            raise CodexExecError("invalid_response", "Codex did not use the required function")
    if request.get("parallel_tool_calls") is False and len(calls) > 1:
        raise CodexExecError("invalid_response", "Parallel tool calls are disabled")
    if not calls and not (result["content"] or "").strip():
        raise CodexExecError("invalid_response", "Codex returned an empty assistant response")
    response_format = request.get("response_format") or {}
    if not calls and response_format.get("type") in {"json_object", "json_schema"}:
        try:
            structured = _json(result["content"])
        except (ValueError, TypeError) as exc:
            raise CodexExecError("invalid_response", "Malformed structured assistant content") from exc
        schema = (response_format.get("json_schema") or {}).get("schema", {"type": "object"})
        _validate(structured, schema)
    return SimpleNamespace(
        role="assistant", content=result["content"], tool_calls=calls or None,
        reasoning_content=None, refusal=None,
    )


def _environment() -> dict[str, str]:
    # Codex owns its native login cache. Never extract OAuth tokens or inherit
    # provider/API keys, personal plugins, unrelated integrations or Git auth.
    allowed = ("HOME", "PATH", "LANG", "LC_ALL", "TMPDIR", "CODEX_HOME",
               "SYSTEMROOT", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "TEMP", "TMP")
    return {key: os.environ[key] for key in allowed if key in os.environ}


def build_argv(command: str, directory: Path, model: str, reasoning: str) -> list[str]:
    settings = {
        "approval_policy": "never", "model_reasoning_effort": reasoning,
        "project_doc_max_bytes": 0, "web_search": "disabled",
        "features.shell_tool": False, "features.unified_exec": False,
        "features.multi_agent": False, "features.apps": False,
        "features.plugins": False, "features.remote_plugin": False,
        "features.memories": False, "features.hooks": False,
        "features.skill_mcp_dependency_install": False,
        "shell_environment_policy.inherit": "none",
    }
    argv = [command, "exec", "--json", "--ephemeral", "--ignore-user-config",
            "--strict-config", "--sandbox", "read-only", "--skip-git-repo-check",
            "--cd", str(directory), "--model", model,
            "--output-schema", str(directory / "response-schema.json")]
    for key, value in settings.items():
        argv.extend(["-c", f"{key}={json.dumps(value)}"])
    return [*argv, "-"]


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=3)
    except ProcessLookupError:
        pass


def parse_events(text: str) -> tuple[str, dict[str, int]]:
    """Return the turn's last assistant message and its token usage.

    Codex may complete more than one agent_message in a turn (for example a
    progress note before the schema-constrained answer). Codex itself treats
    the last one as the turn's result (``--output-last-message``), so the last
    message is returned and still passes through ``decode_response``. The
    returned usage carries ``agent_message_count``, a count only: message text
    is never recorded.
    """
    answer = None
    messages = 0
    usage = None
    completed = False
    for line in text.splitlines():
        try:
            event = _json(line)
            kind = event["type"]
        except (ValueError, TypeError, KeyError) as exc:
            raise CodexExecError("invalid_events", "Malformed Codex event stream") from exc
        if kind in {"error", "turn.failed"}:
            detail = event.get("error", event)
            detail = detail if isinstance(detail, dict) else {"message": str(detail)}
            message = str(detail.get("message", "")).lower()
            rate_limited = any(word in message for word in ("usage limit", "rate limit", "quota", "usage_limit"))
            reset = detail.get("resets_at", detail.get("reset_at"))
            raise CodexExecError(
                "usage_exhausted" if rate_limited else "provider_failed",
                "Codex usage is exhausted" if rate_limited else "Codex inference failed",
                reset_at=reset if type(reset) is int and reset > 0 else None,
            )
        if kind.startswith("item."):
            item = event.get("item") or {}
            if item.get("type") not in {"reasoning", "agent_message"}:
                raise CodexExecError("unexpected_tool_execution", "Model-only Codex attempted tool execution")
            if kind == "item.completed" and item.get("type") == "agent_message":
                if not isinstance(item.get("text"), str):
                    raise CodexExecError("invalid_events", "Invalid Codex assistant response")
                answer = item["text"]
                messages += 1
        if completed and kind != "turn.completed" and kind.startswith(("item.", "turn.")):
            raise CodexExecError("invalid_events", "Codex event after turn completed")
        if kind == "turn.completed":
            if completed:
                raise CodexExecError("invalid_events", "Multiple Codex turns in one response")
            completed = True
            usage = event.get("usage")
    if not completed or answer is None or not isinstance(usage, dict):
        raise CodexExecError("incomplete_response", "Codex did not complete its response with usage")
    for field in ("input_tokens", "cached_input_tokens", "output_tokens"):
        if type(usage.get(field)) is not int or usage[field] < 0:
            raise CodexExecError("invalid_usage", "Codex returned invalid token usage")
    if usage["cached_input_tokens"] > usage["input_tokens"]:
        raise CodexExecError("invalid_usage", "Cached usage exceeds input usage")
    return answer, {**usage, "agent_message_count": messages}


class CodexExecClient:
    """Sync OpenAI-shaped client, with an optional host-owned executor."""

    SUPPORTS_HERMES_TOOL_CALLS = True

    def __init__(self, *, settings: dict | None = None, executor: Callable | None = None,
                 cancel_check: Callable[[], bool] | None = None, **_kwargs):
        self.settings = dict(load_settings() if settings is None else settings)
        self.command = self.settings.get("command", "codex")
        self.timeout = self.settings.get("timeout_seconds", 120)
        if isinstance(self.timeout, bool) or not isinstance(self.timeout, (int, float)) or not math.isfinite(self.timeout) or not 0 < self.timeout <= 900:
            raise ValueError("codex_exec.timeout_seconds must be between 0 and 900")
        self.api_key, self.base_url = "codex-exec", BASE_URL
        self._executor, self._cancel_check = executor, cancel_check
        self._processes: set[subprocess.Popen] = set()
        self._lock = threading.Lock()
        self.is_closed = False
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def close(self) -> None:
        with self._lock:
            self.is_closed = True
            processes = list(self._processes)
        for process in processes:
            _stop(process)

    def _run(self, request: dict[str, Any], reasoning: str,
             cancel_event: threading.Event | None = None) -> tuple[str, dict[str, int]]:
        payload = _INSTRUCTIONS + "\n" + json.dumps(request, ensure_ascii=False, allow_nan=False)
        if len(payload.encode()) > _MAX_BYTES:
            raise CodexExecError("request_too_large", "Codex request exceeds the transport limit")
        with tempfile.TemporaryDirectory(prefix="hermes-codex-exec-") as temporary:
            directory = Path(temporary)
            (directory / "response-schema.json").write_text(json.dumps(response_schema()), encoding="utf-8")
            with tempfile.TemporaryFile(dir=directory) as stdin, (directory / "events.jsonl").open("w+b") as stdout, (directory / "stderr.log").open("w+b") as stderr:
                # A seekable stdin avoids partial pipe writes when a slow CLI
                # has not consumed a large conversation before a polling tick.
                stdin.write(payload.encode())
                stdin.seek(0)
                with self._lock:
                    if self.is_closed:
                        raise CodexExecError("cancelled", "Codex client is closed")
                    process = subprocess.Popen(
                        build_argv(self.command, directory, request["model"], reasoning),
                        stdin=stdin, stdout=stdout, stderr=stderr,
                        env=_environment(), cwd=directory,
                        start_new_session=os.name != "nt",
                    )
                    self._processes.add(process)
                try:
                    deadline = time.monotonic() + self.timeout
                    while True:
                        if self.is_closed or cancel_event and cancel_event.is_set() or self._cancel_check and self._cancel_check():
                            raise CodexExecError("cancelled", "Codex request was cancelled")
                        if time.monotonic() >= deadline:
                            raise CodexExecError("timeout", "Codex request timed out")
                        if os.fstat(stdout.fileno()).st_size + os.fstat(stderr.fileno()).st_size > _MAX_BYTES:
                            raise CodexExecError("output_too_large", "Codex output exceeds the transport limit")
                        try:
                            process.wait(timeout=0.1)
                            break
                        except subprocess.TimeoutExpired:
                            pass
                    if self.is_closed or cancel_event and cancel_event.is_set() or self._cancel_check and self._cancel_check():
                        raise CodexExecError("cancelled", "Codex request was cancelled")
                    stdout.seek(0)
                    data = stdout.read(_MAX_BYTES + 1)
                    if len(data) > _MAX_BYTES:
                        raise CodexExecError("output_too_large", "Codex output exceeds the transport limit")
                    # Parse errors first to retain typed quota holds even when
                    # the CLI exits nonzero. Never expose raw stderr or prompts.
                    try:
                        decoded = data.decode("utf-8", errors="strict")
                    except UnicodeDecodeError as exc:
                        raise CodexExecError("invalid_events", "Codex returned invalid UTF-8") from exc
                    answer, usage = parse_events(decoded)
                    if process.returncode != 0:
                        raise CodexExecError("provider_failed", "Codex exited unsuccessfully")
                    return answer, usage
                finally:
                    _stop(process)
                    with self._lock:
                        self._processes.discard(process)

    def create(self, **kwargs: Any) -> Any:
        if self.is_closed:
            raise CodexExecError("cancelled", "Codex client is closed")
        request = {key: kwargs[key] for key in (
            "model", "messages", "tools", "tool_choice", "parallel_tool_calls", "response_format",
        ) if key in kwargs}
        if not isinstance(request.get("model"), str) or not request["model"] or not isinstance(request.get("messages"), list):
            raise CodexExecError("unsupported_request", "Codex requires a model and messages")
        for message in request["messages"]:
            if not isinstance(message, dict):
                raise CodexExecError("unsupported_request", "Messages must be objects")
            content = message.get("content")
            if isinstance(content, list):
                if any(not isinstance(part, dict) or part.get("type") != "text"
                       or not isinstance(part.get("text"), str) for part in content):
                    raise CodexExecError("unsupported_request", "Codex exec currently accepts text context only")
            elif content is not None and not isinstance(content, str):
                raise CodexExecError("unsupported_request", "Message content must be text")
        reasoning = kwargs.get("reasoning_effort") or self.settings.get("reasoning_effort", "high")
        if reasoning not in {"none", "minimal", "low", "medium", "high", "xhigh"}:
            raise CodexExecError("unsupported_request", "Unsupported Codex reasoning effort")
        answer, usage = (self._executor(request, reasoning) if self._executor else self._run(request, reasoning, kwargs.get("_cancel_event")))
        message = decode_response(answer, request)
        completion = SimpleNamespace(
            id="codex-exec", model=request["model"],
            choices=[SimpleNamespace(index=0, message=message, finish_reason="tool_calls" if message.tool_calls else "stop")],
            usage=SimpleNamespace(
                prompt_tokens=usage["input_tokens"], completion_tokens=usage["output_tokens"],
                total_tokens=usage["input_tokens"] + usage["output_tokens"],
                prompt_tokens_details=SimpleNamespace(cached_tokens=usage["cached_input_tokens"]),
                **({"agent_message_count": usage["agent_message_count"]}
                   if type(usage.get("agent_message_count")) is int else {}),
            ),
        )
        return completion_to_stream_chunks(completion) if kwargs.get("stream") else completion


class AsyncCodexExecClient:
    SUPPORTS_HERMES_TOOL_CALLS = True

    def __init__(self, client: CodexExecClient):
        self.client = client
        self.api_key, self.base_url = client.api_key, client.base_url
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **kwargs: Any) -> Any:
        cancel_event = threading.Event()
        worker = asyncio.create_task(asyncio.to_thread(
            self.client.create, **kwargs, _cancel_event=cancel_event,
        ))
        try:
            result = await asyncio.shield(worker)
            if kwargs.get("stream"):
                return _AsyncChunks(result)
            return result
        except asyncio.CancelledError:
            cancel_event.set()
            # The cached client may serve other requests. Reap only this
            # request's subprocess, leaving the wrapper reusable.
            try:
                await asyncio.shield(worker)
            except Exception:
                pass
            raise

    async def close(self) -> None:
        await asyncio.to_thread(self.client.close)


class _AsyncChunks:
    def __init__(self, chunks):
        self._chunks = iter(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._chunks)
        except StopIteration:
            raise StopAsyncIteration from None

    async def close(self):
        pass
