"""The CLI is a model transport; only validated Hermes calls can escape it."""

import json
import asyncio
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from agent.codex_exec_client import (
    AsyncCodexExecClient, BASE_URL, CodexExecClient, CodexExecError,
    build_argv, decode_response, parse_events,
)


USAGE = {"input_tokens": 17, "cached_input_tokens": 12, "output_tokens": 4}
TOOLS = [{"type": "function", "function": {
    "name": "memory", "description": "Store a lesson", "parameters": {
        "type": "object", "properties": {"content": {"type": "string"}},
        "required": ["content"], "additionalProperties": False,
    },
}}]


def envelope(content="Ready", calls=None):
    return json.dumps({"content": content, "tool_calls": calls or []})


def events(answer=None, usage=None, messages=None):
    texts = [answer or envelope()] if messages is None else messages
    return "\n".join(json.dumps(event) for event in [
        {"type": "thread.started", "thread_id": "test"},
        {"type": "turn.started"},
        *({"type": "item.completed", "item": {"type": "agent_message", "text": text}} for text in texts),
        {"type": "turn.completed", "usage": usage or USAGE},
    ])


_POPEN = subprocess.Popen


def spawn_printing(monkeypatch, output):
    def spawn(argv, **kwargs):
        return _POPEN([sys.executable, "-c", "print(" + repr(output) + ")"], **kwargs)

    monkeypatch.setattr(subprocess, "Popen", spawn)


def call(name="memory", arguments='{"content":"A tested lesson"}', call_id="call_1"):
    return {"id": call_id, "name": name, "arguments": arguments}


def test_tool_round_trip_preserves_native_dispatch():
    message = decode_response(envelope(None, [call()]), {"tools": TOOLS})
    assert message.content is None
    assert message.tool_calls[0].function.name == "memory"
    assert json.loads(message.tool_calls[0].function.arguments) == {"content": "A tested lesson"}


@pytest.mark.parametrize("calls,parameters", [
    ([call(name="terminal")], {"tools": TOOLS}),
    ([call(arguments='{"content":5}')], {"tools": TOOLS}),
    ([call(arguments='{"content":"x", "outside":true}')], {"tools": TOOLS}),
    ([call(arguments='{"content":"x", "content":"y"}')], {"tools": TOOLS}),
    ([call(arguments='{"content":NaN}')], {"tools": TOOLS}),
    ([call(), call()], {"tools": TOOLS}),
    ([call()], {"tools": TOOLS, "tool_choice": "none"}),
    ([], {"tools": TOOLS, "tool_choice": "required"}),
    ([call()], {"tools": TOOLS, "tool_choice": {"type": "function", "function": {"name": "other"}}}),
    ([call(), call(call_id="call_2")], {"tools": TOOLS, "parallel_tool_calls": False}),
])
def test_unusable_tools_never_reach_hermes(calls, parameters):
    with pytest.raises(CodexExecError, match="."):
        decode_response(envelope("answer", calls), parameters)


@pytest.mark.parametrize("text", ["no JSON", '{"content":"x","tool_calls":[],"extra":true}', '{"content":null,"tool_calls":[]}'])
def test_malformed_or_empty_output_is_failure(text):
    with pytest.raises(CodexExecError):
        decode_response(text, {})


def test_auxiliary_structured_output_is_validated():
    request = {"response_format": {"type": "json_schema", "json_schema": {"schema": {
        "type": "object", "required": ["learned"], "properties": {"learned": {"type": "boolean"}},
    }}}}
    assert decode_response(envelope('{"learned":true}'), request).content == '{"learned":true}'
    with pytest.raises(CodexExecError):
        decode_response(envelope('{"learned":"maybe"}'), request)


def test_remote_schema_references_are_not_fetched():
    tools = [{"type": "function", "function": {"name": "memory", "parameters": {"$ref": "https://invalid.example/schema"}}}]
    with pytest.raises(CodexExecError):
        decode_response(envelope(None, [call()]), {"tools": tools})


def test_usage_and_success_must_both_be_present():
    answer, usage = parse_events(events())
    assert answer == envelope()
    assert usage == {**USAGE, "agent_message_count": 1}
    with pytest.raises(CodexExecError):
        parse_events(events().splitlines()[2])
    with pytest.raises(CodexExecError):
        parse_events(events(usage={**USAGE, "cached_input_tokens": 18}))


def test_single_agent_message_is_the_answer():
    answer, usage = parse_events(events(messages=[envelope("Only")]))
    assert answer == envelope("Only")
    assert usage["agent_message_count"] == 1


@pytest.mark.parametrize("texts", [
    ["Checking the request.", envelope("Final")],
    ["Checking the request.", "Still working.", envelope("Final")],
    [envelope("Earlier"), envelope("Middle"), envelope("Final")],
])
def test_last_agent_message_in_the_turn_is_the_answer(texts):
    answer, usage = parse_events(events(messages=texts))
    assert answer == envelope("Final")
    assert usage["agent_message_count"] == len(texts)
    assert {key: usage[key] for key in USAGE} == USAGE


@pytest.mark.parametrize("position", [0, 1, 2])
@pytest.mark.parametrize("text", [None, 5, ["x"], {"text": "x"}])
def test_non_string_agent_message_is_refused_in_any_position(position, text):
    texts = [envelope("a"), envelope("b"), envelope("c")]
    texts[position] = text
    with pytest.raises(CodexExecError) as failure:
        parse_events(events(messages=texts))
    assert failure.value.code == "invalid_events"


def test_missing_agent_message_text_is_refused():
    stream = events(messages=[]).splitlines()
    stream.insert(2, json.dumps({"type": "item.completed", "item": {"type": "agent_message"}}))
    with pytest.raises(CodexExecError) as failure:
        parse_events("\n".join(stream))
    assert failure.value.code == "invalid_events"


def test_turn_without_agent_message_is_refused():
    with pytest.raises(CodexExecError) as failure:
        parse_events(events(messages=[]))
    assert failure.value.code == "incomplete_response"


def test_multiple_messages_keep_turn_and_usage_checks():
    texts = ["Checking.", envelope("Final")]
    with pytest.raises(CodexExecError) as failure:
        parse_events(events(messages=texts) + "\n" + json.dumps({"type": "turn.completed", "usage": USAGE}))
    assert failure.value.code == "invalid_events"
    with pytest.raises(CodexExecError) as failure:
        parse_events("\n".join(events(messages=texts).splitlines()[:-1]))
    assert failure.value.code == "incomplete_response"
    with pytest.raises(CodexExecError) as failure:
        parse_events(events(messages=texts, usage={**USAGE, "output_tokens": -1}))
    assert failure.value.code == "invalid_usage"
    with pytest.raises(CodexExecError) as failure:
        parse_events(events(messages=texts) + "\n" + json.dumps({"type": "error", "message": "boom"}))
    assert failure.value.code == "provider_failed"
    attempted = json.dumps({"type": "item.started", "item": {"type": "command_execution"}})
    with pytest.raises(CodexExecError) as failure:
        parse_events(events(messages=texts) + "\n" + attempted)
    assert failure.value.code == "unexpected_tool_execution"


def test_schema_validation_applies_to_the_last_message(monkeypatch):
    spawn_printing(monkeypatch, events(messages=[envelope("Earlier"), "not the JSON envelope"]))
    with pytest.raises(CodexExecError) as failure:
        CodexExecClient(settings={}).create(model="test-model", messages=[])
    assert failure.value.code == "invalid_response"

    spawn_printing(monkeypatch, events(messages=["Checking the request.", envelope("Final")]))
    result = CodexExecClient(settings={}).create(model="test-model", messages=[])
    assert result.choices[0].message.content == "Final"


def test_structured_output_validation_applies_to_the_last_message(monkeypatch):
    request = {"response_format": {"type": "json_schema", "json_schema": {"schema": {
        "type": "object", "required": ["learned"], "properties": {"learned": {"type": "boolean"}},
    }}}}
    spawn_printing(monkeypatch, events(messages=[envelope('{"learned":true}'), envelope('{"learned":"maybe"}')]))
    with pytest.raises(CodexExecError) as failure:
        CodexExecClient(settings={}).create(model="test-model", messages=[], **request)
    assert failure.value.code == "invalid_response"

    spawn_printing(monkeypatch, events(messages=[envelope('{"learned":"maybe"}'), envelope('{"learned":true}')]))
    result = CodexExecClient(settings={}).create(model="test-model", messages=[], **request)
    assert result.choices[0].message.content == '{"learned":true}'


@pytest.mark.parametrize("count", [1, 2, 3])
def test_completion_usage_reports_agent_message_count_only(monkeypatch, count):
    texts = [f"Private commentary {index}" for index in range(count - 1)] + [envelope("Final")]
    spawn_printing(monkeypatch, events(messages=texts))
    result = CodexExecClient(settings={}).create(model="test-model", messages=[])
    assert result.usage.agent_message_count == count
    assert type(result.usage.agent_message_count) is int
    assert "Private commentary" not in repr(result.usage)


def test_executor_usage_without_count_is_unchanged():
    client = CodexExecClient(settings={}, executor=lambda request, reasoning: (envelope(), USAGE))
    result = client.create(model="test-model", messages=[])
    assert not hasattr(result.usage, "agent_message_count")
    assert result.usage.prompt_tokens == 17


def test_native_cli_tool_execution_is_rejected():
    attempted = json.dumps({"type": "item.started", "item": {"type": "command_execution"}})
    with pytest.raises(CodexExecError) as failure:
        parse_events(attempted + "\n" + events())
    assert failure.value.code == "unexpected_tool_execution"


def test_quota_hold_preserves_reported_reset_without_retrying():
    with pytest.raises(CodexExecError) as failure:
        parse_events(json.dumps({"type": "turn.failed", "error": {"message": "Usage limit reached", "reset_at": 2000000000}}))
    assert failure.value.code == "usage_exhausted"
    assert failure.value.reset_at == 2000000000


def test_cli_contract_disables_tools_and_keeps_prompt_out_of_argv(tmp_path):
    argv = build_argv("codex", tmp_path, "test-model", "high")
    assert "--ephemeral" in argv and "--ignore-user-config" in argv
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert "features.shell_tool=false" in argv and "features.multi_agent=false" in argv
    assert "features.plugins=false" in argv and "web_search=\"disabled\"" in argv
    assert argv[-1] == "-"


def test_unsupported_images_fail_before_inference():
    def unexpected(request, reasoning):
        pytest.fail("Unsupported input reached inference")

    client = CodexExecClient(settings={}, executor=unexpected)
    with pytest.raises(CodexExecError) as failure:
        client.create(model="test-model", messages=[{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "https://invalid.example/image.png"}},
        ]}])
    assert failure.value.code == "unsupported_request"


def test_real_subprocess_carries_prompt_and_collects_validated_response(monkeypatch):
    original = subprocess.Popen
    observed = []
    script = "import sys; prompt=sys.stdin.read(); assert 'A private prompt' in prompt; print(" + repr(events()) + ")"

    def spawn(argv, **kwargs):
        observed.append(argv)
        assert "OPENAI_API_KEY" not in kwargs["env"]
        return original([sys.executable, "-c", script], **kwargs)

    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-test-value")
    monkeypatch.setattr(subprocess, "Popen", spawn)
    client = CodexExecClient(settings={})
    result = client.create(model="test-model", messages=[{"role": "user", "content": "A private prompt"}])
    assert result.choices[0].message.content == "Ready"
    assert result.usage.prompt_tokens_details.cached_tokens == 12
    assert "A private prompt" not in " ".join(observed[0])
    assert not client._processes


def test_timeout_reaps_the_real_process(monkeypatch):
    original = subprocess.Popen
    processes = []

    def spawn(argv, **kwargs):
        process = original([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", spawn)
    client = CodexExecClient(settings={"timeout_seconds": 0.2})
    with pytest.raises(CodexExecError) as failure:
        client.create(model="test-model", messages=[])
    assert failure.value.code == "timeout"
    assert processes[0].poll() is not None
    assert not client._processes


def test_slow_reader_receives_prompt_larger_than_pipe_capacity(monkeypatch):
    original = subprocess.Popen
    prompt = "synthetic-context-" * 100000
    script = (
        "import json,sys,time; time.sleep(0.3); data=sys.stdin.read(); "
        "request=json.loads(data.split('\\n',1)[1]); "
        "assert request['messages'][0]['content'] == 'synthetic-context-' * 100000; "
        "print(" + repr(events()) + ")"
    )

    def spawn(argv, **kwargs):
        return original([sys.executable, "-c", script], **kwargs)

    monkeypatch.setattr(subprocess, "Popen", spawn)
    client = CodexExecClient(settings={"timeout_seconds": 3})
    result = client.create(model="test-model", messages=[{"role": "user", "content": prompt}])
    assert result.choices[0].message.content == "Ready"
    assert not client._processes


def test_close_cancels_and_reaps_a_running_request(monkeypatch):
    original = subprocess.Popen
    started = threading.Event()
    processes, failures = [], []
    client = CodexExecClient(settings={"timeout_seconds": 10})

    def spawn(argv, **kwargs):
        process = original([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        processes.append(process)
        started.set()
        return process

    def run():
        try:
            client.create(model="test-model", messages=[])
        except CodexExecError as exc:
            failures.append(exc.code)

    monkeypatch.setattr(subprocess, "Popen", spawn)
    worker = threading.Thread(target=run)
    worker.start()
    assert started.wait(5)
    client.close()
    worker.join(5)
    assert not worker.is_alive()
    assert failures == ["cancelled"]
    assert processes[0].poll() is not None


@pytest.mark.asyncio
async def test_async_auxiliary_stream_is_an_async_iterator():
    sync = CodexExecClient(settings={}, executor=lambda request, reasoning: (envelope(), USAGE))
    client = AsyncCodexExecClient(sync)
    stream = await client.chat.completions.create(model="test-model", messages=[], stream=True)
    chunks = [chunk async for chunk in stream]
    assert chunks[0].choices[0].delta.content == "Ready"
    assert chunks[-1].usage.prompt_tokens == 17
    await client.close()


@pytest.mark.asyncio
async def test_async_cancellation_keeps_shared_client_and_other_request_alive(monkeypatch):
    original = subprocess.Popen
    started = threading.Event()
    processes = []
    slow = True

    def spawn(argv, **kwargs):
        script = "import time; time.sleep(30)" if slow else "print(" + repr(events()) + ")"
        process = original([sys.executable, "-c", script], **kwargs)
        processes.append(process)
        started.set()
        return process

    monkeypatch.setattr(subprocess, "Popen", spawn)
    client = AsyncCodexExecClient(CodexExecClient(settings={}))
    first = asyncio.create_task(client.create(model="test-model", messages=[]))
    assert await asyncio.to_thread(started.wait, 5)
    slow = False
    second = asyncio.create_task(client.create(model="test-model", messages=[]))
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert (await second).choices[0].message.content == "Ready"
    assert (await client.create(model="test-model", messages=[])).choices[0].message.content == "Ready"
    assert not client.client.is_closed
    assert all(process.poll() is not None for process in processes)
    await client.close()


def test_real_runtime_and_auxiliary_resolution_share_the_cli_backend(monkeypatch):
    from hermes_cli.runtime_provider import resolve_runtime_provider
    from agent.agent_runtime_helpers import create_openai_client
    from agent.auxiliary_client import resolve_provider_client, _to_async_client
    from providers import get_provider_profile

    monkeypatch.setattr("agent.codex_exec_client.shutil.which", lambda command: "/test/codex")
    assert get_provider_profile("codex-exec").name == "codex_exec"
    runtime = resolve_runtime_provider(requested="codex_exec")
    assert runtime["base_url"] == BASE_URL and runtime["api_mode"] == "chat_completions"
    agent = SimpleNamespace(provider="codex_exec", _interrupt_requested=False)
    primary = create_openai_client(agent, {"base_url": BASE_URL}, reason="test", shared=False)
    auxiliary, model = resolve_provider_client("codex_exec", "test-model")
    assert isinstance(primary, CodexExecClient) and isinstance(auxiliary, CodexExecClient)
    assert model == "test-model"
    converted, _ = _to_async_client(auxiliary, model)
    assert isinstance(converted, AsyncCodexExecClient)
