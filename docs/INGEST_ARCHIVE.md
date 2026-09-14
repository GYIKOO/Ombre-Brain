# Local ingest recovery archive (fork feature)

Before `hold` or `grow` dispatch starts any model or memory processing, the
complete bound arguments are saved as a UTF-8 JSON receipt. JSON is readable
in a text editor and preserves arrays, Unicode, timestamps and metadata for
future replay. Transport authentication headers are not archived.

## Location and retention

`OMBRE_INGEST_ARCHIVE_DIR` overrides the directory. Otherwise use
`ingest-archive/` beside `OMBRE_CONFIG_PATH`, or beneath `OMBRE_VAULT_DIR`
(fallback `buckets`). Use a separate directory per character/instance.
This directory is ignored by Git. Do not share it publicly: it contains chat
text. Other sync/backup applications may retain their own copies.

Receipts older than 30 days from receipt time are removed on the next ingest,
and at startup/hourly while the HTTP service runs. Offline time is caught up
at the next startup. This retention also applies to failed receipts; copy any
important failed input elsewhere before its retention expires.

## Guarantees and boundaries

- Atomic file replacement with file flush/fsync happens before processing.
  If saving fails (e.g. disk full), processing does not start.
- Model failure leaves full input on disk. Exceptions record `failed` and an
  exception class; cancellation records `interrupted`. Abrupt process exit
  can leave `received`, meaning outcome unknown.
- `returned` means the tool returned; it does **not** prove every item was
  stored. The exact result text is retained, including partial-failure or
  in-progress notices. Receipt IDs identify attempts, not unique memories.
- Protocol/authentication/schema rejections before dispatch are outside this
  archive. Phone adapters must supply valid arguments first.
- This is a recovery archive, **not** a durable retry worker or exactly-once
  ingest system. Replaying may create/merge memories; review first.
- Older failed requests cannot be reconstructed from metadata-only logs.

## Upstream workflow

`upstream` tracks P0luz/Ombre-Brain; `origin` tracks your fork. Keep changes on
`codex/ingest-archive`. Fetch upstream and merge its main branch into this
branch after reviewing/testing updates. Application one-click updating may
discard or bypass fork customizations; use Git for this installation.

Run focused tests with `PYTHONPATH=src python -m unittest discover -s tests
-p test_ingest_archive_local.py -v`.

## Long-text digest segments

Long input is split losslessly into at most 4096 Unicode characters per
segment, favoring phone-message, paragraph, then line boundaries. A single
oversized line is hard-split. No suffix is silently discarded. Source line
ranges are translated back to the complete original document.

Segments are processed sequentially. Completed model results are saved in
`digest_chunks` receipts in the same archive. An identical input with the
same model settings/prompt reuses completed segments on a later retry,
including after restart. Changed model settings invalidate the cache.
All segments must finish before grow starts creating buckets. This avoids
partial writes caused by a digest failure; it does not yet solve partial
bucket-write failures or provide automatic timed retries.

Chunk caches follow the same 30-day archive retention. They are processing
checkpoints, not separate memory buckets. Model settings and the 8192 output
token budget are independent from the 4096-character input bound.
