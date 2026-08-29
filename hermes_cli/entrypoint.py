"""Effect-free public entrypoint for bounded strict one-shot execution.

Ordinary invocations are delegated lazily to :mod:`hermes_cli.main`.  The
strict ``-z`` path completes admission before importing the ordinary startup
stack, so rejected requests cannot load dotenv, plugins, tools, MCP, sessions,
hooks, provider discovery, or process-global bootstrap code.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

_PROFILE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_ENV_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_MAX_CONFIG_BYTES = 1_000_000
_MAX_CONFIG_NODES = 4096
_MAX_CONFIG_DEPTH = 32
_STRICT_ROOT_FIELDS = frozenset({"model", "agent"})
_STRICT_MODEL_FIELDS = frozenset(
    {
        "output_budget_mode",
        "max_tokens",
        "provider",
        "default",
        "model",
        "base_url",
        "api_mode",
        "key_env",
    }
)
_STRICT_AGENT_FIELDS = frozenset({"system_prompt"})


class StrictRejected(Exception):
    """Content-free strict admission failure."""


def _contains_public_oneshot(argv: Sequence[str]) -> bool:
    return any(arg in {"-z", "--oneshot"} or arg.startswith("--oneshot=") for arg in argv)


def _profile_home(argv: Sequence[str], environ: Mapping[str, str]) -> Path:
    explicit: str | None = None
    for index, arg in enumerate(argv):
        if arg in {"-p", "--profile"}:
            if index + 1 >= len(argv):
                raise StrictRejected
            explicit = argv[index + 1]
            break
        if arg.startswith("--profile="):
            explicit = arg.split("=", 1)[1]
            break

    base = Path(environ.get("HERMES_HOME", "").strip() or (Path.home() / ".hermes"))
    root = base.parent.parent if base.parent.name == "profiles" else base
    if explicit is not None:
        if not _PROFILE_RE.fullmatch(explicit):
            raise StrictRejected
        return root if explicit == "default" else root / "profiles" / explicit
    if base.parent.name == "profiles" or environ.get("HERMES_S6_SUPERVISED_CHILD", ""):
        return base
    try:
        active = (root / "active_profile").read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return root
    except (OSError, UnicodeError):
        raise StrictRejected from None
    if not active or active == "default":
        return root
    if not _PROFILE_RE.fullmatch(active):
        raise StrictRejected
    return root / "profiles" / active


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        stat_result = path.stat()
    except FileNotFoundError:
        return {}
    except OSError:
        raise StrictRejected from None
    if not path.is_file() or stat_result.st_size > _MAX_CONFIG_BYTES:
        raise StrictRejected
    try:
        import yaml

        class _StrictMappingLoader(yaml.SafeLoader):
            def construct_mapping(self, node: Any, deep: bool = False) -> dict[Any, Any]:
                if not isinstance(node, yaml.MappingNode):
                    raise StrictRejected
                self.flatten_mapping(node)
                mapping: dict[Any, Any] = {}
                for key_node, value_node in node.value:
                    key = self.construct_object(key_node, deep=deep)
                    try:
                        duplicate = key in mapping
                    except TypeError:
                        raise StrictRejected from None
                    if duplicate:
                        raise StrictRejected
                    mapping[key] = self.construct_object(value_node, deep=deep)
                return mapping

        value = yaml.load(
            path.read_text(encoding="utf-8"), Loader=_StrictMappingLoader
        ) or {}
    except Exception:
        raise StrictRejected from None
    if not isinstance(value, dict):
        raise StrictRejected
    _validate_owned_tree(value)
    return value


def _validate_owned_tree(root: object) -> None:
    seen: set[int] = set()
    stack: list[tuple[object, int]] = [(root, 0)]
    visited = 0
    while stack:
        value, depth = stack.pop()
        visited += 1
        if visited > _MAX_CONFIG_NODES or depth > _MAX_CONFIG_DEPTH:
            raise StrictRejected
        if isinstance(value, (dict, list)):
            identity = id(value)
            if identity in seen:
                raise StrictRejected
            seen.add(identity)
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str):
                    raise StrictRejected
                stack.append((child, depth + 1))
        elif isinstance(value, list):
            stack.extend((child, depth + 1) for child in value)
        elif value is not None and not isinstance(value, (str, int, float, bool)):
            raise StrictRejected


def _deep_merge(left: dict[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    for key, value in right.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def _read_strict_probe(argv: Sequence[str], environ: Mapping[str, str]) -> dict[str, Any] | None:
    home = _profile_home(argv, environ)
    user = _read_yaml_mapping(home / "config.yaml")
    managed = _read_yaml_mapping(Path("/etc/hermes/config.yaml"))
    config = _deep_merge(user, managed)
    model = config.get("model")
    if not isinstance(model, dict) or "output_budget_mode" not in model:
        return None
    if model.get("output_budget_mode") != "strict":
        raise StrictRejected
    return config


def _parse_strict_args(argv: Sequence[str]) -> str:
    prompt: object | None = None
    index = 0
    saw_chat = False
    while index < len(argv):
        arg = argv[index]
        if arg in {"-p", "--profile"}:
            if index + 1 >= len(argv):
                raise StrictRejected
            index += 2
            continue
        if arg.startswith("--profile="):
            index += 1
            continue
        if arg in {"-z", "--oneshot"}:
            if prompt is not None or index + 1 >= len(argv):
                raise StrictRejected
            prompt = argv[index + 1]
            index += 2
            continue
        if arg.startswith("--oneshot="):
            if prompt is not None:
                raise StrictRejected
            prompt = arg.split("=", 1)[1]
            index += 1
            continue
        if arg == "chat" and not saw_chat and prompt is None:
            saw_chat = True
            index += 1
            continue
        raise StrictRejected
    if not isinstance(prompt, str):
        raise StrictRejected
    try:
        encoded = prompt.encode("ascii")
    except UnicodeEncodeError:
        raise StrictRejected from None
    if len(encoded) > 8000:
        raise StrictRejected
    return prompt


def _admit_static_route(config: Mapping[str, Any], environ: Mapping[str, str]) -> dict[str, str]:
    if set(config) - _STRICT_ROOT_FIELDS:
        raise StrictRejected
    model = config.get("model")
    if not isinstance(model, Mapping):
        raise StrictRejected
    if set(model) - _STRICT_MODEL_FIELDS:
        raise StrictRejected
    identities = [key for key in ("default", "model") if key in model]
    if len(identities) != 1:
        raise StrictRejected
    agent = config.get("agent")
    if agent is not None:
        if not isinstance(agent, Mapping) or set(agent) - _STRICT_AGENT_FIELDS:
            raise StrictRejected
        if "system_prompt" in agent and not isinstance(agent["system_prompt"], str):
            raise StrictRejected
    if isinstance(model.get("max_tokens"), bool) or model.get("max_tokens") != 2000:
        raise StrictRejected
    provider = model.get("provider")
    name = model[identities[0]]
    base_url = model.get("base_url")
    api_mode = model.get("api_mode")
    key_env = model.get("key_env")
    if provider != "custom" or api_mode != "chat_completions":
        raise StrictRejected
    if not all(isinstance(value, str) and value.strip() for value in (name, base_url, key_env)):
        raise StrictRejected
    parsed = urlparse(str(base_url))
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise StrictRejected
    if parsed.query or parsed.fragment or not _ENV_RE.fullmatch(str(key_env)):
        raise StrictRejected
    secret = environ.get(str(key_env))
    if not isinstance(secret, str) or not secret:
        raise StrictRejected
    forbidden_env = (
        "HERMES_KANBAN_TASK", "HERMES_INFERENCE_MODEL", "HERMES_INFERENCE_PROVIDER",
        "HERMES_DUMP_REQUESTS", "HERMES_RELAY_ENABLED", "HERMES_NOUS_API_KEY",
    )
    if any(environ.get(key, "").strip() for key in forbidden_env):
        raise StrictRejected
    return {
        "model": str(name).strip(),
        "provider": "custom",
        "base_url": str(base_url).rstrip("/"),
        "api_key": secret,
    }


def _dispatch_ordinary() -> int | None:
    from hermes_cli.main import main as ordinary_main

    return ordinary_main()


def main(argv: Sequence[str] | None = None) -> int | None:
    args = list(sys.argv[1:] if argv is None else argv)
    if not _contains_public_oneshot(args):
        return _dispatch_ordinary()
    try:
        config = _read_strict_probe(args, os.environ)
    except StrictRejected:
        sys.stderr.write("strict_config_rejected\n")
        return 2
    if config is None:
        return _dispatch_ordinary()
    try:
        prompt = _parse_strict_args(args)
        route = _admit_static_route(config, os.environ)
        from hermes_cli.oneshot import run_strict_oneshot

        code, response, _usage = run_strict_oneshot(prompt, config=config, route=route)
    except StrictRejected:
        sys.stderr.write("strict_cli_rejected\n")
        return 2
    except BaseException:  # content-free by contract, including SDK/import failures
        sys.stderr.write("strict_execution_failed\n")
        return 2
    if code != 0 or not response:
        sys.stderr.write("strict_execution_failed\n")
        return 2
    sys.stdout.write(response)
    if not response.endswith("\n"):
        sys.stdout.write("\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
