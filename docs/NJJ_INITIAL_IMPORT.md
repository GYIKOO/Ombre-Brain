# NJJ initial summary import

Review export: `src/ombrebrain/storage/njj_import.py`.
Apply selected summaries offline: `scripts/njj_apply_review.py REVIEW BACKUP VAULT`.
The initial apply uses NJJ keywords, topics and importance without LLM calls.
Original event dates are stored as `njj_event_date`; Ombre creation dates
remain import dates. Source IDs, coverage, child IDs and the backup hash remain
in Markdown metadata. The initial tool does not yet implement changed-source
reconciliation, deletion suppression or raw-message coverage seeding.
Do not use it as the future automatic repair importer yet.

The production instance has a separate configuration, token and port from the
test instance. Embeddings are initially disabled for user content review.
A later indexing pass is necessary before semantic search is available.
Private backup, review and applied manifests are under ignored data paths.
