"""Narrow raw-document storage interface.

M2 stores raw bytes directly in Postgres (`documents.raw_bytes`). The
brief (§9) wants MinIO for object storage; that's deferred to a later
milestone (M6 hardening explicitly lists it). Isolating storage behind
`save_raw`/`storage_backend_name` means that swap touches this module only
— not the ingest pipeline, not the schema's `storage_backend` column
semantics (it already exists and is set to "postgres" today).
"""

from app.storage.postgres_storage import save_raw, storage_backend_name

__all__ = ["save_raw", "storage_backend_name"]
