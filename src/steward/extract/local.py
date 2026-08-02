"""The local model: the only thing in this system that sees raw text.

Everything else in `extract/` is a deterministic parser, and deterministic
parsers cover the formulaic majority — bank alerts and calendar files are
written by machines. Free-form text is not, and "the boiler man is coming
Thursday and I'll need to be in" is exactly the sort of thing a person says and
no regex will ever read. That is what Ollama is here for.

**This module has no redaction and must not gain any.** Redacting the input
would defeat the purpose: the whole point is a model that can read the raw
sentence, on this machine, and hand back only the structured residue. The
protection is not redaction — it is that the request never leaves the device.

Which makes the loopback check the load-bearing line of this file. `OLLAMA_BASE`
is a string in an environment variable; a typo, a copied dotfile, or a helpful
tutorial pointing it at a hosted Ollama would turn this module from the safest
component in the system into the one that posts a person's unredacted life to a
stranger. So a non-loopback host is refused, by default, loudly. The override
exists because someone running Ollama on their own LAN is making a real and
legitimate choice — but they have to make it explicitly, and the error tells
them precisely what they would be agreeing to.
"""

from __future__ import annotations

import ipaddress
import json
from typing import Any
from urllib.parse import urlparse

import httpx

from .. import config
from ..models import FactKind
from .base import INFERRED, Candidate, slug

DEFAULT_TIMEOUT = 60.0

# Asking for a schema in the prompt and validating it here, rather than trusting
# the model to be well-behaved: small local models are much worse at instruction
# following than gpt-5, and this one is unattended.
PROMPT = """Extract durable facts from the message below. Reply with ONLY a JSON \
array, no prose, no code fence.

Each element: {{"kind": ..., "key": ..., "value": ...}}
  kind  — one of: {kinds}
  key   — a short stable slug, e.g. "working_hours", "soap"
  value — the fact in under 100 characters

Rules:
- Extract only what the message actually states. Do not infer, guess or embellish.
- Skip pleasantries and questions. If nothing durable is stated, reply [].
- Never invent a date, an amount or a name that is not in the message.

Message:
{message}"""


class LocalModelError(RuntimeError):
    """The local model is unreachable, misconfigured, or answered unusably."""


def is_loopback(base_url: str) -> bool:
    """True only if this URL cannot leave the machine."""
    host = urlparse(base_url).hostname or ""
    if host in ("localhost", "localhost.localdomain"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_destination(base_url: str) -> None:
    """Refuse to send raw text off the device unless told to, in writing."""
    if is_loopback(base_url) or config.local_llm_allow_remote():
        return
    raise LocalModelError(
        f"refusing to send unredacted text to {base_url!r}, which is not on this machine."
        " This model is the one component that sees raw email, calendar entries and"
        " messages; that is only acceptable while the request cannot leave the device."
        " If you genuinely run Ollama on another host you control, set"
        " STEWARD_LOCAL_LLM_ALLOW_REMOTE=1 — and understand that raw personal data will"
        " then travel to it."
    )


def _parse_candidates(content: str) -> list[Candidate]:
    """Read the model's JSON, discarding anything malformed.

    Silence beats a wrong fact here. An extractor that invents a working
    pattern out of a misparse writes it into memory with a source of
    `inferred`, where it sits looking exactly as authoritative as everything
    else on the screen.
    """
    text = content.strip()
    if text.startswith("```"):
        text = text.partition("\n")[2].rpartition("```")[0].strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        parsed = json.loads(text[start : end + 1])
    except ValueError:
        return []
    if not isinstance(parsed, list):
        return []

    candidates: list[Candidate] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind", "")).strip().lower()
        value = str(item.get("value", "")).strip()
        if kind not in FactKind.ALL or not value:
            continue
        key = slug(str(item.get("key") or value))
        candidates.append(Candidate(kind=kind, key=key, value=value[:200], source=INFERRED))
    return candidates


def extract(
    raw: str,
    *,
    model: str | None = None,
    http: httpx.Client | None = None,
) -> list[Candidate]:
    """Free-form text in, candidate facts out, without leaving the machine."""
    base = config.ollama_base()
    check_destination(base)

    body: dict[str, Any] = {
        "model": model or config.ollama_model(),
        "prompt": PROMPT.format(kinds=", ".join(FactKind.ALL), message=raw),
        "stream": False,
        # Deterministic: the same message must extract the same facts, or
        # re-importing a mailbox quietly rewrites memory each time.
        "options": {"temperature": 0.0},
    }
    client = http or httpx.Client(timeout=DEFAULT_TIMEOUT)
    try:
        response = client.post(f"{base}/api/generate", json=body)
    except httpx.HTTPError as exc:
        raise LocalModelError(f"could not reach the local model at {base}: {exc}") from exc
    finally:
        if http is None:
            client.close()

    if response.status_code != 200:
        raise LocalModelError(f"local model returned {response.status_code}: {response.text[:200]}")
    return _parse_candidates(str(response.json().get("response", "")))


def available(*, http: httpx.Client | None = None) -> bool:
    """Whether free-form extraction can run at all.

    Ollama being absent is a normal install — the deterministic parsers still
    work and the surfaces say what is degraded, the same way an unset
    OPENAI_API_KEY is normal.
    """
    base = config.ollama_base()
    if not is_loopback(base) and not config.local_llm_allow_remote():
        return False
    client = http or httpx.Client(timeout=5.0)
    try:
        return client.get(f"{base}/api/tags").status_code == 200
    except httpx.HTTPError:
        return False
    finally:
        if http is None:
            client.close()
