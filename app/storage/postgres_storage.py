"""Postgres-backed raw storage. M2's implementation of `app.storage`.

There is no separate "save" step here beyond what the `documents` insert
already does — `raw_bytes` lives in the same row as everything else. This
module exists so the *pipeline* code never says "insert raw_bytes into
documents" directly; it says "save_raw(...)" and gets back a reference,
which is what makes swapping to MinIO later a one-module change (see
app/storage/__init__.py).
"""


def storage_backend_name() -> str:
    return "postgres"


def save_raw(raw_bytes: bytes) -> bytes:
    """Return the value to store as `documents.raw_bytes`.

    For the Postgres backend this is the identity function: the bytes
    themselves are what gets written to the row. A MinIO-backed
    implementation would instead upload `raw_bytes` and return an object
    key/URL, at which point `documents.raw_bytes` would need to become
    `documents.raw_ref` — a schema change, made deliberately visible here
    rather than hidden, so it isn't mistaken for a drop-in swap.
    """
    return raw_bytes
