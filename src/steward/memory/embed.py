"""Turning text into a vector, on this machine, with nothing installed.

The default embedder is a hashed bag of words. It is honestly a **lexical**
matcher, not a semantic one: "I'm out of soap" and "need to restock hand wash"
share no tokens and will not match. That is a real limitation and it is written
down rather than glossed, because the alternative — sending every conversational
turn to an embeddings API — trades the project's whole privacy argument for
better recall on a feature that is, by design, only conversational colour.

The seam is the point. `Embedder` is a protocol, `embed()` takes one, and phase
2 brings Ollama in for exactly this: a local model that does understand that
soap and hand wash are the same errand, still without anything leaving the
device. Until then the cheap version is honest about what it is.

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
from typing import Protocol

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
