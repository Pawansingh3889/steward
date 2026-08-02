"""Settings: the only module that reads os.environ, and the only place a
missing value becomes a sentence instead of a plausible default.

The .env reader is the idiom carried through canibuy → steward → here:
deliberately tiny, no dotenv dependency, no interpolation, and `setdefault` so a
real environment variable always beats the file.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_DB = "steward.sqlite3"
DEFAULT_MODEL = "gpt-5"
DEFAULT_OPENAI_BASE = "https://api.openai.com"
DEFAULT_LINQ_BASE = "https://api.linqapp.com/api/partner/v3"
# Loopback by default, and extract/local.py refuses to send raw text anywhere
# that is not — see the reasoning there.
DEFAULT_OLLAMA_BASE = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "llama3.2"


class ConfigError(RuntimeError):
    """A required setting is missing or unusable."""


def _load_env() -> None:
    env = Path(__file__).resolve().parents[2] / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def _str(name: str, default: str = "") -> str:
    _load_env()
    return os.environ.get(name, default).strip()


def db_path() -> str:
    return _str("STEWARD_DB") or DEFAULT_DB


def openai_api_key() -> str:
    """Empty means the agent is unconfigured — a normal install. Everything that
    is not the model still works, and the agent surfaces say so."""
    return _str("OPENAI_API_KEY")


def openai_api_base() -> str:
    """The host only — `llm.complete` appends `/v1/chat/completions`. A trailing
    slash here would produce a double one there, which some proxies 404 on."""
    return (_str("OPENAI_API_BASE") or DEFAULT_OPENAI_BASE).rstrip("/")


def agent_model() -> str:
    return _str("STEWARD_AGENT_MODEL") or DEFAULT_MODEL


def ollama_base() -> str:
    """Where the local extraction model lives. Trailing slash stripped so the
    loopback check in extract/local.py sees a clean host."""
    return (_str("OLLAMA_BASE") or DEFAULT_OLLAMA_BASE).rstrip("/")


def ollama_model() -> str:
    return _str("OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL


def local_llm_allow_remote() -> bool:
    """Opt-in to sending unredacted text to a non-loopback Ollama.

    Deliberately strict about what counts as yes. Someone who writes
    `STEWARD_LOCAL_LLM_ALLOW_REMOTE=false` means no, and a truthiness check on a
    non-empty string would read it as yes and ship their mail off the box.
    """
    return _str("STEWARD_LOCAL_LLM_ALLOW_REMOTE").lower() in ("1", "true", "yes", "on")


def linq_token() -> str:
    return _str("LINQ_API_TOKEN")


def linq_api_base() -> str:
    return _str("LINQ_API_BASE") or DEFAULT_LINQ_BASE


def linq_from_number() -> str:
    return _str("LINQ_FROM_NUMBER")


def pay_warden_command() -> list[str]:
    """How to launch pay-warden as an MCP subprocess.

    A separate process on purpose: pay-warden is pinned to mcp 1.x and this is
    on 2.x, and the protocol is the contract between them rather than a shared
    import. It also means a policy engine crash cannot take the agent with it.
    """
    command = _str("PAY_WARDEN_COMMAND")
    if not command:
        raise ConfigError(
            "PAY_WARDEN_COMMAND is unset — every purchase is policy-checked, so"
            " there is no path to spending without it (see .env.example)"
        )
    return [command, *_str("PAY_WARDEN_ARGS").split()]


def pay_warden_cwd() -> str | None:
    """Directory to launch pay-warden in.

    It resolves its policy file and audit database relative to its own working
    directory, so launching it from steward's would hand it a missing policy —
    and a policy engine that cannot find its policy is the one component here
    that must never start up degraded. Empty means inherit ours.
    """
    return _str("PAY_WARDEN_CWD") or None


def secret_values() -> tuple[str, ...]:
    """Literal strings that must never reach the model. Read live rather than
    cached, so rotating a key cannot leave a stale value unprotected."""
    values = (
        openai_api_key(),
        linq_token(),
        _str("PRAVA_SECRET_KEY"),
    )
    return tuple(value for value in values if value)
