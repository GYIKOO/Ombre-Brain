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

## Rolling phone-message deduplication

After the complete grow request is archived, phone dialogue envelopes are
filtered using source, test scope, role, message ID (or timestamp), and exact
content. No reliable ID/time means no deduplication. Edited text or a new
timestamp is processed again. Only a result with all items created/merged
commits message fingerprints to `message-ingest.db` beside the archive.
Failures, partial results and ambiguous outcomes remain retryable. A per-loop
lock serializes phone batches in the supported single-process deployment.
Earlier history is not retroactively marked processed. No extra context is
added to filtered messages in this first version; short replies may need
context-aware grouping in a future revision.

This is not exactly-once ingestion: a crash after bucket creation but before
fingerprint commit can still repeat work. Multiple worker processes are not
supported for this guard. Already stored hashes persist beyond the 30-day raw
archive retention; they contain no original message text.

## Two-band duplicate review

Settings > Backup & Migration has independent review (default 0.80) and
automatic archive (default 0.985) thresholds, editable for each scan.
The explicit POST scan automatically archives exact-text or high-score pairs,
preferentially retaining the longer text. Scores are not probabilities.
Lower-score candidates are displayed for manual decisions. Only existing local
vectors are compared, without model API calls. Missing vectors still allow
exact-text matching. Protected, pinned and locked buckets are excluded.
GET and refresh after review are read-only. No pair display cap hides matches.

Direct pairs only: a discarded member cannot pull additional memories into
its group through transitive similarity. Decisions record representative IDs
in duplicate-review.db; originals remain archived and can be restored.
Undo and dismiss suppress the unchanged pair on later scans. Changed content
is eligible again. This is scan-triggered, not a scheduled background job.

## Permanent archive cleanup

Dashboard archive details and bulk selection expose human-confirmed permanent
deletion. The server rechecks archive location and metadata under the bucket
lock, refuses pinned/protected or active memories, removes the Markdown file,
and discards derived index state. No MCP hard-delete tool is added. Existing
backups and 30-day raw ingestion receipts are retained independently; deletion
is not a purge of those copies. Deleted archive targets no longer offer undo
in duplicate review.


## 手动恢复（Dashboard）

设置 → 备份与迁移 → 接收缓存与手动重试 → 查看 / 刷新失败记录。
对指定原始 hold/grow 请求点「用当前配置重新处理」，确认后在后台运行；
无需 NJJ 再推送，也不会自动循环调用 API。页面定期读取本地状态，不调用模型。
修正模型配置后再重试；相同输入、提示词和配置下可以复用 digest 分段检查点。
失败请求保留原始参数和结果，manual_retry 保存最近一次尝试状态及累计次数，
每次实际重放另有带 retry_of 的完整 receipt。中途重启会显示「处理曾中断」。
列表不包含内部 digest_chunks 检查点，也不重复展示重试生成的子记录。

部分写入、崩溃后的不确定结果不具备 exactly-once 保证，重试可能产生重复，
请先核对桶，必要时使用已有查重功能。列表不代表 NJJ 的全部历史覆盖情况，
原始请求正常返回但附带打标或向量警告、正文已保存的，不属于重试失败。
仍采用30天接收缓存保留期；过期原文无法从此入口恢复。运行中的请求不会被清理。
这是单服务进程内的手动恢复功能，不是跨进程任务队列。
