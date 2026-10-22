from __future__ import annotations
import hashlib
import random


def stream_seed(root_seed: int, stream: str, *parts) -> int:
    payload = ':'.join([str(int(root_seed)), str(stream), *(str(x) for x in parts)])
    return int.from_bytes(hashlib.sha256(payload.encode('utf-8')).digest()[:8], 'big')


def rng(root_seed: int, stream: str, *parts) -> random.Random:
    return random.Random(stream_seed(root_seed, stream, *parts))
