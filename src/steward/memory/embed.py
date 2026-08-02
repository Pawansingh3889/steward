"""Turning text into a vector, on this machine, with nothing installed.

The default embedder is a hashed bag of words. It is honestly a **lexical**
matcher, not a semantic one: "I'm out of soap" and "need to restock hand wash"
share no tokens and will not match. That is a real limitation and it is written
down rather than glossed, because the alternative — sending every conversational
turn to an embeddings API — trades the project's whole privacy argument for
better recall on a feature that is, by design, only conversational colour.

The seam is the point, and it is now used: `OllamaEmbedder` below is a local
model that does understand soap and hand wash are the same errand, still
without anything leaving the device. It is off unless `STEWARD_EMBEDDING_MODEL`
names a model, because switching changes what recall finds and a silent default
would rewrite the meaning of somebody's existing memory.

Two details that look fussy and are not:

  * Hashing uses blake2b, not Python's `hash()`. `hash()` is salted per process,
    so vectors written today would not match vectors computed tomorrow — the
    store would silently rot across restarts.
  * Vectors are L2-normalized at write time, so similarity is a dot product and
    a long episode does not outrank a short one purely for being long.
"""

from __future__ import annotations

import hashlib
import math
import re
import struct
from collections import Counter
from typing import Any, Protocol

DIMENSIONS = 256

_TOKEN = re.compile(r"[a-z0-9']+")

# Words carrying no signal about what an episode was *about*. Short list on
# purpose: an aggressive stoplist on a lexical matcher throws away the few
# tokens two related sentences actually share.
# fmt: off
_STOPWORDS = frozenset([
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "for",
    "from", "had", "has", "have", "i", "i'm", "if", "in", "is", "it", "its",
    "me", "my", "of", "on", "or", "that", "the", "their", "them", "then",
    "there", "they", "this", "to", "was", "we", "were", "what", "when",
    "which", "who", "will", "with", "you", "your",
])
# fmt: on


class Embedder(Protocol):
    """Anything that turns text into a fixed-length unit vector."""

    dimensions: int

    def encode(self, text: str) -> list[float]: ...


def tokenize(text: str) -> list[str]:
    return [word for word in _TOKEN.findall(text.lower()) if word not in _STOPWORDS]


class HashingEmbedder:
    """The zero-dependency default. See the module docstring for its limits."""

    def __init__(self, dimensions: int = DIMENSIONS) -> None:
        self.dimensions = dimensions

    def _bucket(self, token: str) -> tuple[int, float]:
        """A stable bucket and sign for a token.

        The sign halves the collision damage: two unrelated tokens landing in
        the same bucket cancel as often as they reinforce, instead of always
        reinforcing.
        """
        digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        return value % self.dimensions, 1.0 if value & (1 << 63) else -1.0

    def encode(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        counts = Counter(tokenize(text))
        for token, count in counts.items():
            index, sign = self._bucket(token)
            # Sublinear term frequency: saying "soap" five times is more about
            # soap than saying it once, but not five times more.
            vector[index] += sign * (1.0 + math.log(count))
        return normalize(vector)


def normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return vector
    return [value / norm for value in vector]


def similarity(left: list[float], right: list[float]) -> float:
    """Cosine similarity. Both sides are unit vectors, so this is a dot product;
    mismatched lengths mean the two came from different embedders and comparing
    them would produce a confident, meaningless number."""
    if len(left) != len(right):
        raise ValueError(
            f"cannot compare a {len(left)}-dimension vector with a {len(right)}-dimension one"
        )
    return sum(a * b for a, b in zip(left, right, strict=True))


def pack(vector: list[float]) -> bytes:
    """float32 for storage: half the bytes, and the precision lost is far below
    what a hashed bag of words could meaningfully resolve."""
    return struct.pack(f"<{len(vector)}f", *vector)


def unpack(blob: bytes) -> list[float]:
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


class OllamaEmbedder:
    """Vectors from a model on this machine.

    The reason this is worth having is one sentence long: the hashing embedder
    is lexical, so "out of soap" and "need hand wash" share no tokens and score
    zero, while a model knows they are the same errand.

    It reuses `extract.local`'s destination check rather than repeating it. That
    check is the load-bearing line of the privacy argument — a non-loopback host
    is refused unless somebody explicitly says otherwise — and embedding sends
    exactly the same raw sentences the extractor does. Two copies of that rule
    would eventually disagree, and the copy that disagreed quietly would be this
    one.

    The timeout is generous because a model that is not resident has to load
    before it can answer, and an 8B model cold-starting takes far longer than a
    warm one. Thirty seconds looked fine against a warm model and failed on the
    first real call.

    `dimensions` is discovered from the first response, not configured. Every
    model has its own width, and a number typed into a config file that did not
    match the model would produce vectors that pack fine and compare as noise.
    """

    def __init__(
        self,
        model: str,
        *,
        base_url: str | None = None,
        http: Any = None,
        timeout: float = 120.0,
    ) -> None:
        from .. import config
        from ..extract import local

        self.model = model
        self.base_url = (base_url or config.ollama_base()).rstrip("/")
        local.check_destination(self.base_url)
        self._http = http
        self._timeout = timeout
        self.dimensions = 0

    def encode(self, text: str) -> list[float]:
        import httpx

        client = self._http or httpx.Client(timeout=self._timeout)
        try:
            response = client.post(
                f"{self.base_url}/api/embeddings",
                json={"model": self.model, "prompt": text},
            )
            response.raise_for_status()
            vector = response.json().get("embedding")
        except (httpx.HTTPError, ValueError) as exc:
            raise EmbeddingError(f"{self.model} could not embed that: {exc}") from exc
        finally:
            if self._http is None:
                client.close()
        if not isinstance(vector, list) or not vector:
            raise EmbeddingError(f"{self.model} returned no embedding for that text")
        # Everything that can go wrong in here has to come out as EmbeddingError,
        # because that is the one type callers fall back on. A model answering
        # with a list of strings would otherwise raise a bare ValueError past
        # `episodic._encode` and take somebody's whole turn down.
        try:
            values = [float(value) for value in vector]
        except (TypeError, ValueError) as exc:
            raise EmbeddingError(f"{self.model} returned a non-numeric embedding: {exc}") from exc
        self.dimensions = len(values)
        return normalize(values)


class EmbeddingError(RuntimeError):
    """The local model could not produce a vector."""


def why_lexical(*, http: Any = None) -> str:
    """Why `build()` would hand back the lexical matcher, or "" if it would not.

    Three different things send it there and they want three different things
    done about them, so collapsing them into "the model was unreachable" is a
    disservice in exactly the case that matters most: a host that is not this
    machine has been *refused*, not failed to answer, and telling somebody to
    check whether Ollama is running sends them to debug a service while their
    `OLLAMA_BASE` quietly points off the device.
    """
    from .. import config
    from ..extract import local

    if not config.embedding_model():
        return "STEWARD_EMBEDDING_MODEL is unset"
    base = config.ollama_base()
    if not local.is_loopback(base) and not config.local_llm_allow_remote():
        return (
            f"{base} is not on this machine, so it was refused"
            " — set STEWARD_LOCAL_LLM_ALLOW_REMOTE=1 only if you mean it"
        )
    if not local.available(http=http):
        return f"nothing answered at {base}"
    return ""


def build(*, http: Any = None) -> Embedder:
    """The embedder this install is configured for.

    Falls back to the hashing one when the model is unset or unusable, because
    recall is conversational colour and a memory that raises because Ollama is
    not running would take a whole conversation down for a feature nobody asked
    for in that moment. The fallback is silent by design here and reported by
    `steward memory reindex`, which is where somebody is actually asking the
    question — `why_lexical` is the shared answer, so the two cannot drift.
    """
    from .. import config
    from ..extract import local

    if why_lexical(http=http):
        return HashingEmbedder()
    try:
        return OllamaEmbedder(config.embedding_model(), http=http)
    except (local.LocalModelError, EmbeddingError):
        return HashingEmbedder()
