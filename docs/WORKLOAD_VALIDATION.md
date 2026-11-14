# Synthetic ingestion workload baseline

Run `python scripts/evaluate_workload.py` for the fixed small profile, or add
`--profile medium` for the larger bounded profile. Both generate their corpus
locally and print a JSON report. The optional `--root` points to an empty
directory where the generated files should be kept for inspection.

The profiles contain 120 or 800 active files across Python, JavaScript,
TypeScript, and Markdown. The corpus is under 10 MB; each production bounded
read is limited to 1 MB per file, the profile byte budget, and the profile file
count. A chunk count ceiling of 20,000 is enforced. Stable generation uses
seed `20261111`. The report includes a canonical logical corpus SHA-256,
file/byte/chunk counts, parser failures, phase wall times, Linux peak RSS when
available, and separately classified edit, rename, add, and delete path changes
(including the complete set of removed paths). It also reports
provider and embedding calls as zero.

The benchmark calls Sentra's actual `_source_paths`, `_markdown_paths`,
`read_checkout_files`, `Chunker.chunk_source`, and `chunk_markdown` code. Its
incremental change summary compares the resulting path/content-hash inventory;
it does not exercise database upserts or prove database deletion behavior.
Git checkout, persistence, embedding, retrieval, and answer generation are
outside this measurement. Treat timing and memory as synthetic index
preparation measurements, not production capacity or retrieval-quality claims.

Run the focused offline checks with `pytest tests/test_workload_baseline.py`.
They cover deterministic inventory, mixed-language parsing, exclusions,
symlink handling on Linux, edit/rename/delete detection, and fail-closed file,
total-byte, and file-count limits.
