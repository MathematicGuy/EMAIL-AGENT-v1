# Ingestion latency baselines

This directory holds reviewed, metadata-only snapshots promoted from local
`../runs/` artifacts. There is no committed baseline yet.

A candidate baseline must cover 10 measured repetitions of the ordered
three-fixture sequence: cold `dang-ky-xe-pdf-v1`, then warm
`procedure-116194-pdf-v1`, then warm `law-31-2024-docx-v1` in the same project.
It must identify the database host class and storage/embedding providers,
retain failed or incomplete sample metadata, and contain none of the forbidden
content or transient `document_id` listed in the parent README.

Initial baselines are record-only. Do not add regression thresholds until
multiple comparable runs establish expected variance.
