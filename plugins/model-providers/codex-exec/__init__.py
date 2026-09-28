"""Codex CLI supplies responses while Hermes owns its native tool loop."""

from providers import register_provider
from providers.base import ProviderProfile


class CodexExecProfile(ProviderProfile):
    def fetch_models(self, **kwargs):
        return None


register_provider(CodexExecProfile(
    name="codex_exec", aliases=("codex-exec",), display_name="Codex CLI (exec)",
    description="Model responses through codex exec, with Hermes tools and memory",
    api_mode="chat_completions", auth_type="external_process",
    base_url="codex-exec://codex-exec", env_vars=(),
))
