# Test pruning ledger — coverage map (Task 2)

Scratch instrument map only. **Do not treat rows here as deletions.**
Coverage % is not a deletion reason. Empty-hit tests are Task 3 candidates
and still need `tests/README.md` §4 before any prune.

## Header

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Command | `uv run pytest --cov=cowork_agent --cov-context=test --cov-report=term-missing --cov-report=json:C:\Users\PC\AppData\Local\Temp\email-agent-cov\coverage.json -n 0 -q` |
| xdist | **off** (`-n 0`) — preferred so `--cov-context=test` contexts stay correct under pytest-cov 7.1.0 / coverage 7.15.4 |
| pytest-cov | 7.1.0 |
| coverage.py | 7.15.4 |
| Result | **1840 passed**, 9 skipped, 267 deselected, 3 xfailed, 2 xpassed in 60.19s |
| Marker filter | default `addopts` kept (`-m 'not live'`) |
| Data store | repo `.coverage` (gitignored); contexts re-exported via `uv run coverage json --show-contexts` to `%TEMP%\email-agent-cov\coverage-contexts.json` (outside repo; initial pytest JSON lacked `show_contexts`) |
| Suite default | **uninstrumented** — `--cov` not in `addopts`; plugin idle unless flags passed |

### xdist + coverage note

This map used `-n 0` deliberately. Under xdist `loadgroup`, context labels can
corrupt or merge incorrectly; a correct map beats a fast map. Do not use
testmon 20-vs-102 (BM25) as prune evidence.

---

## 1. `src/` files with **0** test hits

These are **possible missing tests**, not deletion candidates.

### 1a. Measurable statements, 0% covered

| Path | Statements | Covered % |
|---|---:|---:|
| `src/cowork_agent/features/ai_chat/graph/__init__.py` | 3 | 0.0 |
| `src/cowork_agent/features/ai_chat/graph/nodes.py` | 8 | 0.0 |
| `src/cowork_agent/features/ai_chat/graph/runner.py` | 19 | 0.0 |
| `src/cowork_agent/features/ai_chat/graph/state.py` | 15 | 0.0 |

All four sit under `features/ai_chat/graph/` (unused / unwired graph package in this run).

### 1b. No measurable statements (empty modules)

Listed for completeness; not actionable as missing tests.

| Path | Note |
|---|---|
| `src/cowork_agent/features/__init__.py` | no measurable statements |
| `src/cowork_agent/features/ai_chat/memory_eval/__init__.py` | no measurable statements |
| `src/cowork_agent/integrations/__init__.py` | no measurable statements |
| `src/cowork_agent/integrations/storage/__init__.py` | no measurable statements |
| `src/cowork_agent/orchestration/__init__.py` | no measurable statements |
| `src/cowork_agent/persistence/__init__.py` | no measurable statements |
| `src/cowork_agent/security/__init__.py` | no measurable statements |

Every other measured `src/cowork_agent/**/*.py` file had at least one hit
(158 files in the coverage report; package filesystem scan matched).

---

## 2. Tests with empty executed `cowork_agent` lines (Task 3 candidates)

### Method

- pytest-cov contexts are `<nodeid>|<phase>` (`setup` / `run`; almost no `teardown` here).
- Queried via coverage SQLite (`.coverage` `context` + `line_bits`) and
  `coverage json --show-contexts`, using `coverage.numbits.numbits_to_nums`.
- A collected test with **no** context row that attributes any `src/cowork_agent`
  line is treated as **empty executed lines** for this package scope.

### Limitation

- Collected non-live nodeids: **1845**. Distinct context nodeids: **1605**.
- **240** collected tests never appear in coverage contexts for `cowork_agent`.
- That usually means they never executed measured package lines (often they
  exercise `scripts/`, fixture JSON schemas, pure constants, or harness code
  **outside** `--cov=cowork_agent`) — not proof they are dead.
- Among the **1605** tests that *did* record run-phase `src/` hits: **0** were
  empty and **0** were def/class-only (every one hit at least one non-`def`/`class` line).
- Coverage records executable lines only; def/class-only detection is therefore
  conservative and may under-flag decorative tests that still import module bodies.

### Summary by test file

| Test file | Empty-context nodeids |
|---|---:|
| `tests/unit/scripts/test_evaluate_ingestion_latency.py` | 45 |
| `tests/unit/scripts/test_evaluate_retrieval.py` | 39 |
| `tests/unit/fixtures/test_retrieval_golden.py` | 17 |
| `tests/unit/scripts/test_email_evaluation_artifacts.py` | 15 |
| `tests/unit/domain/test_target_contracts.py` | 14 |
| `tests/unit/test_xdist_harness.py` | 10 |
| `tests/unit/scripts/test_fetch_gmail_evaluation_candidates.py` | 9 |
| `tests/unit/test_pg_probe.py` | 8 |
| `tests/unit/fixtures/test_routing_loader.py` | 6 |
| `tests/unit/scripts/test_build_evaluation_dashboard.py` | 6 |
| `tests/unit/scripts/test_evaluate_routing.py` | 6 |
| `tests/unit/scripts/test_memeval_preflight.py` | 6 |
| `tests/unit/features/ai_chat/memory_eval/test_probe_set_fires_retrieval.py` | 5 |
| `tests/unit/features/ai_chat/test_evaluation_dataset.py` | 5 |
| `tests/unit/scripts/test_build_email_evaluation_report.py` | 5 |
| `tests/unit/scripts/test_evaluate_chat_rag.py` | 5 |
| `tests/unit/domain/test_chat_contracts.py` | 4 |
| `tests/unit/persistence/test_chat_history_migration.py` | 4 |
| `tests/unit/persistence/test_identity_session_migration.py` | 3 |
| `tests/unit/persistence/test_migration_lineage.py` | 3 |
| `tests/unit/persistence/test_task_episode_migration.py` | 3 |
| `tests/unit/scripts/test_evaluate_email_golden.py` | 3 |
| `tests/unit/scripts/test_evaluate_memory_provider.py` | 3 |
| `tests/unit/test_network_guard.py` | 3 |
| `tests/unit/features/ai_chat/test_memory_gateway.py` | 2 |
| `tests/unit/persistence/test_durable_chat_session_migration.py` | 2 |
| `tests/unit/persistence/test_project_document_migration.py` | 2 |
| `tests/integration/email_action_plan/test_rag_retrieval_golden.py` | 1 |
| `tests/unit/features/test_ports.py` | 1 |
| `tests/unit/features/test_routing.py` | 1 |
| `tests/unit/fixtures/test_chat_routing_loader.py` | 1 |
| `tests/unit/scripts/test_evaluate_memory.py` | 1 |
| `tests/unit/test_prompting.py` | 1 |
| `tests/unit/test_purge_chat_memory_script.py` | 1 |
| **Total** | **240** |

### Full nodeid list (candidates only)

Still need `tests/README.md` §4 before any delete. Not deletions by themselves.

#### `tests/unit/scripts/test_evaluate_ingestion_latency.py` (45)

- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_aggregates_backend_metrics_and_preserves_nulls`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_aggregates_every_metric_with_repository_nearest_rank_percentiles`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_cli_writes_metadata_only_report`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_environment_classification_accepts_only_loopback_or_remote[]`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_environment_classification_accepts_only_loopback_or_remote[cloud]`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_environment_classification_accepts_only_loopback_or_remote[localhost]`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_expect_local_rejects_remote_database_samples`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_failed_sample_preserves_null_environment_metadata_without_fabrication`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_help_runs_without_provider_keys`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_missing_metrics_and_failed_samples_are_recorded_without_zero_filling`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_mixed_host_check_ignores_unknown_failed_sample_metadata`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_only_ready_verified_complete_samples_count_as_complete[overrides0]`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_only_ready_verified_complete_samples_count_as_complete[overrides1]`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_only_ready_verified_complete_samples_count_as_complete[overrides2]`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_only_ready_verified_complete_samples_count_as_complete[overrides3]`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_only_ready_verified_complete_samples_count_as_complete[overrides4]`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_provider_metadata_accepts_only_nonempty_strings_or_null[-embedding_provider]`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_provider_metadata_accepts_only_nonempty_strings_or_null[-storage_provider]`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_provider_metadata_accepts_only_nonempty_strings_or_null[0-embedding_provider]`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_provider_metadata_accepts_only_nonempty_strings_or_null[0-storage_provider]`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_provider_metadata_accepts_only_nonempty_strings_or_null[False-embedding_provider]`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_provider_metadata_accepts_only_nonempty_strings_or_null[False-storage_provider]`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_provider_metadata_accepts_only_nonempty_strings_or_null[value3-embedding_provider]`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_provider_metadata_accepts_only_nonempty_strings_or_null[value3-storage_provider]`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_provider_metadata_accepts_only_nonempty_strings_or_null[value4-embedding_provider]`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_provider_metadata_accepts_only_nonempty_strings_or_null[value4-storage_provider]`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_ready_verified_sample_with_metrics_and_environment_counts_as_complete`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_rejects_mixed_database_host_classes_before_aggregation`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_rejects_sensitive_and_unknown_sample_fields[answer-secret answer]`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_rejects_sensitive_and_unknown_sample_fields[cookies-session=secret]`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_rejects_sensitive_and_unknown_sample_fields[credentials-secret]`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_rejects_sensitive_and_unknown_sample_fields[document_id-backend-correlation-id]`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_rejects_sensitive_and_unknown_sample_fields[document_text-secret document]`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_rejects_sensitive_and_unknown_sample_fields[prompt-secret prompt]`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_rejects_sensitive_and_unknown_sample_fields[question-secret question]`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_rejects_sensitive_and_unknown_sample_fields[retrieved_chunk_content-secret chunk]`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_rejects_sensitive_and_unknown_sample_fields[signed_url-https://storage.invalid/secret]`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_rejects_sensitive_and_unknown_sample_fields[unexpected-anything]`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_rejects_unknown_metric_keys`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_snapshot_bytes_is_a_nonnegative_integer_when_present[-1]`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_snapshot_bytes_is_a_nonnegative_integer_when_present[1.5]`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_snapshot_bytes_is_a_nonnegative_integer_when_present[100]`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_snapshot_bytes_is_a_nonnegative_integer_when_present[True]`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_snapshot_bytes_is_optional_observational_metadata[None]`
- `tests/unit/scripts/test_evaluate_ingestion_latency.py::test_snapshot_bytes_is_optional_observational_metadata[omitted]`

#### `tests/unit/scripts/test_evaluate_retrieval.py` (39)

- `tests/unit/scripts/test_evaluate_retrieval.py::test_a_miss_contributes_zero_and_is_not_dropped`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_absolute_and_margin_gate_equality_passes_and_below_abstains`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_abstention_counts_no_results_and_zero_chunks_alike`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_aggregate_slices_by_probe_and_document`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_calibration_sweep_accounts_for_28_answerable_and_four_unanswerable`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_candidate_thresholds_are_deterministic_boundaries`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_case_result_rejects_invalid_score_evidence`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_default_output_path_stays_under_documented_evaluations_store`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_empty_expected_sections_are_excluded_and_counted`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_gate_flag_ranges_include_metric_and_latency_boundaries`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_gate_flags_reject_non_finite_and_out_of_range_values[--fail-over-latency-p95--1]`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_gate_flags_reject_non_finite_and_out_of_range_values[--fail-over-latency-p95-inf]`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_gate_flags_reject_non_finite_and_out_of_range_values[--fail-over-latency-p95-nan]`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_gate_flags_reject_non_finite_and_out_of_range_values[--fail-under-doc-mrr-inf]`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_gate_flags_reject_non_finite_and_out_of_range_values[--fail-under-mrr-nan]`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_gate_flags_reject_non_finite_and_out_of_range_values[--fail-under-recall--0.01]`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_gate_flags_reject_non_finite_and_out_of_range_values[--fail-under-recall-1.01]`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_gate_is_per_kind_preserves_inherited_abstention_and_undefined_margin`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_help_runs_without_provider_keys`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_hit_at_3_boundary_between_rank_three_and_four`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_latency_percentiles`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_launch_gate_equality_passes_and_all_strict_violations_are_reported`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_recall_at_5_over_multiple_expected_documents`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_reciprocal_rank_is_one_over_rank`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_rerank_without_hybrid_exits_two`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_run_evaluation_maps_dense_bm25_rrf_and_jina_score_provenance`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_run_evaluation_preserves_empty_and_reranker_fallback_provenance`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_run_evaluation_rejects_invalid_raw_rerank_scores[0.9]`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_run_evaluation_rejects_invalid_raw_rerank_scores[True]`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_run_evaluation_rejects_invalid_raw_rerank_scores[inf]`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_run_evaluation_rejects_invalid_raw_rerank_scores[nan]`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_run_evaluation_rejects_mixed_rerank_scores`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_score_report_schema_is_recursive_closed_and_metadata_only`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_score_summary_covers_empty_single_and_two_result_cases`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_section_level_is_stricter_than_document_level`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_section_level_skips_past_a_wrong_section_to_a_later_right_one`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_singleton_kind_retains_undefined_margin_summary_without_threshold`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_unanswerable_cases_are_excluded_from_ranked_metrics`
- `tests/unit/scripts/test_evaluate_retrieval.py::test_unknown_relevance_level_is_rejected`

#### `tests/unit/fixtures/test_retrieval_golden.py` (17)

- `tests/unit/fixtures/test_retrieval_golden.py::test_invalid_json_is_rejected`
- `tests/unit/fixtures/test_retrieval_golden.py::test_legacy_case_objects_match_the_immutable_snapshot`
- `tests/unit/fixtures/test_retrieval_golden.py::test_missing_email_body_key_is_rejected`
- `tests/unit/fixtures/test_retrieval_golden.py::test_missing_required_field_is_rejected`
- `tests/unit/fixtures/test_retrieval_golden.py::test_non_array_root_is_rejected`
- `tests/unit/fixtures/test_retrieval_golden.py::test_notes_are_optional`
- `tests/unit/fixtures/test_retrieval_golden.py::test_repository_fixture_enforces_exact_appended_allocation`
- `tests/unit/fixtures/test_retrieval_golden.py::test_repository_fixture_rejects_changed_legacy_object`
- `tests/unit/fixtures/test_retrieval_golden.py::test_repository_fixture_requires_contiguous_ids`
- `tests/unit/fixtures/test_retrieval_golden.py::test_repository_fixture_requires_null_email_bodies_on_appended_cases`
- `tests/unit/fixtures/test_retrieval_golden.py::test_repository_fixture_requires_one_hundred_cases`
- `tests/unit/fixtures/test_retrieval_golden.py::test_rule_1_rejects_duplicate_ids`
- `tests/unit/fixtures/test_retrieval_golden.py::test_rule_1_rejects_malformed_id`
- `tests/unit/fixtures/test_retrieval_golden.py::test_rule_2_rejects_unknown_probe`
- `tests/unit/fixtures/test_retrieval_golden.py::test_rule_3_rejects_answerable_without_expected_documents`
- `tests/unit/fixtures/test_retrieval_golden.py::test_rule_3_rejects_unanswerable_with_expected_documents`
- `tests/unit/fixtures/test_retrieval_golden.py::test_schema_rules_pass_without_a_corpus`

#### `tests/unit/scripts/test_email_evaluation_artifacts.py` (15)

- `tests/unit/scripts/test_email_evaluation_artifacts.py::test_approved_artifacts_validate_and_return_copies`
- `tests/unit/scripts/test_email_evaluation_artifacts.py::test_atomic_write_and_load_json_object_are_metadata_safe`
- `tests/unit/scripts/test_email_evaluation_artifacts.py::test_candidate_metadata_and_content_are_strict`
- `tests/unit/scripts/test_email_evaluation_artifacts.py::test_candidate_requires_complete_named_content_and_unique_ids`
- `tests/unit/scripts/test_email_evaluation_artifacts.py::test_candidate_requires_newest_first_received_at_order`
- `tests/unit/scripts/test_email_evaluation_artifacts.py::test_candidate_requires_the_fixed_inbox_query`
- `tests/unit/scripts/test_email_evaluation_artifacts.py::test_dataset_fingerprint_is_key_order_stable_and_label_sensitive`
- `tests/unit/scripts/test_email_evaluation_artifacts.py::test_golden_rejects_prediction_and_private_content`
- `tests/unit/scripts/test_email_evaluation_artifacts.py::test_json_loader_requires_an_object`
- `tests/unit/scripts/test_email_evaluation_artifacts.py::test_non_candidate_artifacts_recursively_reject_private_keys[valid_golden-validate_golden_dataset]`
- `tests/unit/scripts/test_email_evaluation_artifacts.py::test_non_candidate_artifacts_recursively_reject_private_keys[valid_proposals-validate_proposal_batch]`
- `tests/unit/scripts/test_email_evaluation_artifacts.py::test_non_candidate_artifacts_recursively_reject_private_keys[valid_review_export-validate_review_export]`
- `tests/unit/scripts/test_email_evaluation_artifacts.py::test_non_candidate_artifacts_recursively_reject_private_keys[valid_run-validate_run_artifact]`
- `tests/unit/scripts/test_email_evaluation_artifacts.py::test_run_validator_enforces_an_absolute_fifty_case_cap`
- `tests/unit/scripts/test_email_evaluation_artifacts.py::test_validators_enforce_fixed_enums_and_case_limits`

#### `tests/unit/domain/test_target_contracts.py` (14)

- `tests/unit/domain/test_target_contracts.py::test_actionability_values`
- `tests/unit/domain/test_target_contracts.py::test_expected_document_type_values`
- `tests/unit/domain/test_target_contracts.py::test_frozen_rejects_mutation[action_plan_output]`
- `tests/unit/domain/test_target_contracts.py::test_frozen_rejects_mutation[envelope]`
- `tests/unit/domain/test_target_contracts.py::test_frozen_rejects_mutation[route_decision]`
- `tests/unit/domain/test_target_contracts.py::test_frozen_rejects_mutation[trace_event]`
- `tests/unit/domain/test_target_contracts.py::test_reason_code_values`
- `tests/unit/domain/test_target_contracts.py::test_retrieval_filters_defaults_document_ids_years_months_empty`
- `tests/unit/domain/test_target_contracts.py::test_retrieval_status_values`
- `tests/unit/domain/test_target_contracts.py::test_route_values`
- `tests/unit/domain/test_target_contracts.py::test_supporting_enum_values`
- `tests/unit/domain/test_target_contracts.py::test_target_contracts_version`
- `tests/unit/domain/test_target_contracts.py::test_trace_content_policy_constants`
- `tests/unit/domain/test_target_contracts.py::test_trace_latency_defaults_to_all_none`

#### `tests/unit/test_xdist_harness.py` (10)

- `tests/unit/test_xdist_harness.py::test_checkout_src_is_moved_to_the_front_of_sys_path`
- `tests/unit/test_xdist_harness.py::test_cowork_agent_is_imported_from_this_checkout`
- `tests/unit/test_xdist_harness.py::test_disabling_the_xdist_plugin_is_detected`
- `tests/unit/test_xdist_harness.py::test_disabling_the_xdist_plugin_is_not_a_usage_error`
- `tests/unit/test_xdist_harness.py::test_importing_cowork_agent_does_not_import_langfuse`
- `tests/unit/test_xdist_harness.py::test_reconcile_injects_four_workers_when_the_user_did_not_pick_n`
- `tests/unit/test_xdist_harness.py::test_reconcile_strips_glued_and_equals_forms`
- `tests/unit/test_xdist_harness.py::test_reconcile_strips_worker_flags_only_when_xdist_is_disabled`
- `tests/unit/test_xdist_harness.py::test_serial_tests_are_grouped_instead_of_forcing_n0`
- `tests/unit/test_xdist_harness.py::test_testmon_flags_are_rejected`

#### `tests/unit/scripts/test_fetch_gmail_evaluation_candidates.py` (9)

- `tests/unit/scripts/test_fetch_gmail_evaluation_candidates.py::test_candidate_record_removes_grapheme_artifacts_but_preserves_emoji_zwj`
- `tests/unit/scripts/test_fetch_gmail_evaluation_candidates.py::test_candidate_record_removes_only_whole_separator_lines`
- `tests/unit/scripts/test_fetch_gmail_evaluation_candidates.py::test_candidate_record_removes_repeated_invisible_format_controls`
- `tests/unit/scripts/test_fetch_gmail_evaluation_candidates.py::test_candidate_record_replaces_line_boundaries_with_spaces`
- `tests/unit/scripts/test_fetch_gmail_evaluation_candidates.py::test_fetch_candidates_deduplicates_message_ids_across_pages`
- `tests/unit/scripts/test_fetch_gmail_evaluation_candidates.py::test_fetch_candidates_keeps_complete_content_and_orders_newest_first`
- `tests/unit/scripts/test_fetch_gmail_evaluation_candidates.py::test_help_runs_without_gmail_credentials`
- `tests/unit/scripts/test_fetch_gmail_evaluation_candidates.py::test_limit_is_bounded_to_200`
- `tests/unit/scripts/test_fetch_gmail_evaluation_candidates.py::test_write_candidates_validates_before_replacing_existing_destination`

#### `tests/unit/test_pg_probe.py` (8)

- `tests/unit/test_pg_probe.py::test_definitive_tcp_failure_reports_unreachable[error0]`
- `tests/unit/test_pg_probe.py::test_definitive_tcp_failure_reports_unreachable[error1]`
- `tests/unit/test_pg_probe.py::test_definitive_tcp_failure_reports_unreachable[error2]`
- `tests/unit/test_pg_probe.py::test_loopback_gets_the_short_timeout_and_remote_keeps_the_long_one`
- `tests/unit/test_pg_probe.py::test_preflight_ceiling_stays_far_below_the_connect_timeout`
- `tests/unit/test_pg_probe.py::test_unexpected_socket_error_falls_through_to_the_real_connect`
- `tests/unit/test_pg_probe.py::test_unset_url_is_unavailable_without_touching_the_network`
- `tests/unit/test_pg_probe.py::test_url_without_a_host_falls_through_to_the_real_connect`

#### `tests/unit/fixtures/test_routing_loader.py` (6)

- `tests/unit/fixtures/test_routing_loader.py::test_loader_rejects_duplicate_ids`
- `tests/unit/fixtures/test_routing_loader.py::test_loader_rejects_empty_reason_codes`
- `tests/unit/fixtures/test_routing_loader.py::test_loader_rejects_missing_field`
- `tests/unit/fixtures/test_routing_loader.py::test_loader_rejects_non_array_root`
- `tests/unit/fixtures/test_routing_loader.py::test_loader_rejects_unknown_enum_value`
- `tests/unit/fixtures/test_routing_loader.py::test_real_fixture_loads_with_required_coverage`

#### `tests/unit/scripts/test_build_evaluation_dashboard.py` (6)

- `tests/unit/scripts/test_build_evaluation_dashboard.py::test_default_paths_use_the_evaluation_workspace`
- `tests/unit/scripts/test_build_evaluation_dashboard.py::test_load_reports_separates_current_and_historical_evidence`
- `tests/unit/scripts/test_build_evaluation_dashboard.py::test_main_writes_dashboard_from_reports`
- `tests/unit/scripts/test_build_evaluation_dashboard.py::test_render_dashboard_describes_only_currently_reported_retrievers`
- `tests/unit/scripts/test_build_evaluation_dashboard.py::test_render_dashboard_keeps_component_latency_gaps_visible`
- `tests/unit/scripts/test_build_evaluation_dashboard.py::test_render_dashboard_warns_about_chunking_cohorts`

#### `tests/unit/scripts/test_evaluate_routing.py` (6)

- `tests/unit/scripts/test_evaluate_routing.py::test_actionability_agreement_accuracy`
- `tests/unit/scripts/test_evaluate_routing.py::test_default_output_directory_stays_under_documented_evaluations_store`
- `tests/unit/scripts/test_evaluate_routing.py::test_false_negative_retrieval_counts_missed_retrieval`
- `tests/unit/scripts/test_evaluate_routing.py::test_false_negative_retrieval_without_retrieval_labels`
- `tests/unit/scripts/test_evaluate_routing.py::test_help_runs_without_provider_keys`
- `tests/unit/scripts/test_evaluate_routing.py::test_precision_recall_math_including_zero_division`

#### `tests/unit/scripts/test_memeval_preflight.py` (6)

- `tests/unit/scripts/test_memeval_preflight.py::test_a_database_named_like_a_throwaway_is_recognised`
- `tests/unit/scripts/test_memeval_preflight.py::test_a_directory_without_the_harness_fails_the_checkout_check`
- `tests/unit/scripts/test_memeval_preflight.py::test_a_warning_does_not_stop_a_run_and_a_failure_does`
- `tests/unit/scripts/test_memeval_preflight.py::test_the_cause_chain_names_what_the_adapter_hid`
- `tests/unit/scripts/test_memeval_preflight.py::test_the_summary_line_names_what_failed`
- `tests/unit/scripts/test_memeval_preflight.py::test_the_target_is_described_without_its_password`

#### `tests/unit/features/ai_chat/memory_eval/test_probe_set_fires_retrieval.py` (5)

- `tests/unit/features/ai_chat/memory_eval/test_probe_set_fires_retrieval.py::test_probe_sets_are_found`
- `tests/unit/features/ai_chat/memory_eval/test_probe_set_fires_retrieval.py::test_recall_expectations_exist_somewhere_in_the_seed[v1-four-scopes.json]`
- `tests/unit/features/ai_chat/memory_eval/test_probe_set_fires_retrieval.py::test_recall_expectations_exist_somewhere_in_the_seed[v2-four-scopes-wide.json]`
- `tests/unit/features/ai_chat/memory_eval/test_probe_set_fires_retrieval.py::test_the_probe_set_is_found[v1-four-scopes.json]`
- `tests/unit/features/ai_chat/memory_eval/test_probe_set_fires_retrieval.py::test_the_probe_set_is_found[v2-four-scopes-wide.json]`

#### `tests/unit/features/ai_chat/test_evaluation_dataset.py` (5)

- `tests/unit/features/ai_chat/test_evaluation_dataset.py::test_dataset_has_entries`
- `tests/unit/features/ai_chat/test_evaluation_dataset.py::test_dataset_labels_are_metadata_only`
- `tests/unit/features/ai_chat/test_evaluation_dataset.py::test_dataset_no_duplicate_ids`
- `tests/unit/features/ai_chat/test_evaluation_dataset.py::test_dataset_opaque_ids`
- `tests/unit/features/ai_chat/test_evaluation_dataset.py::test_dataset_version_constant`

#### `tests/unit/scripts/test_build_email_evaluation_report.py` (5)

- `tests/unit/scripts/test_build_email_evaluation_report.py::test_cli_does_not_write_report_for_an_incompatible_pair`
- `tests/unit/scripts/test_build_email_evaluation_report.py::test_report_rejects_a_mismatched_dataset_fingerprint`
- `tests/unit/scripts/test_build_email_evaluation_report.py::test_report_rejects_an_unknown_run_case_id`
- `tests/unit/scripts/test_build_email_evaluation_report.py::test_report_rejects_duplicate_run_case_ids`
- `tests/unit/scripts/test_build_email_evaluation_report.py::test_report_rejects_runs_over_fifty_cases`

#### `tests/unit/scripts/test_evaluate_chat_rag.py` (5)

- `tests/unit/scripts/test_evaluate_chat_rag.py::test_cli_writes_a_metadata_only_report`
- `tests/unit/scripts/test_evaluate_chat_rag.py::test_default_output_directory_uses_the_evaluation_workspace`
- `tests/unit/scripts/test_evaluate_chat_rag.py::test_metadata_only_report_calculates_retrieval_linkage_abstention_and_latency`
- `tests/unit/scripts/test_evaluate_chat_rag.py::test_ragas_fails_clearly_when_the_optional_dependency_is_absent`
- `tests/unit/scripts/test_evaluate_chat_rag.py::test_ragas_requires_text_fields_in_every_case`

#### `tests/unit/domain/test_chat_contracts.py` (4)

- `tests/unit/domain/test_chat_contracts.py::test_chat_summary_episode_has_no_raw_email_or_transcript_fields`
- `tests/unit/domain/test_chat_contracts.py::test_contract_version_is_declared`
- `tests/unit/domain/test_chat_contracts.py::test_stream_contract_has_no_tool_variants`
- `tests/unit/domain/test_chat_contracts.py::test_task_episode_contract_version_and_compact_bounds_are_public`

#### `tests/unit/persistence/test_chat_history_migration.py` (4)

- `tests/unit/persistence/test_chat_history_migration.py::test_chat_history_down_migration_removes_the_new_storage`
- `tests/unit/persistence/test_chat_history_migration.py::test_chat_history_schema_stores_completed_turns_and_a_bounded_title`
- `tests/unit/persistence/test_chat_history_migration.py::test_chat_turn_lifecycle_down_migration_restores_completed_turn_shape`
- `tests/unit/persistence/test_chat_history_migration.py::test_chat_turn_lifecycle_migration_supports_idempotent_pending_turns`

#### `tests/unit/persistence/test_identity_session_migration.py` (3)

- `tests/unit/persistence/test_identity_session_migration.py::test_identity_session_down_migration_reverses_new_schema_in_dependency_order`
- `tests/unit/persistence/test_identity_session_migration.py::test_identity_session_schema_adds_a_workspace_to_mailbox_connections`
- `tests/unit/persistence/test_identity_session_migration.py::test_identity_session_schema_stores_only_a_token_hash`

#### `tests/unit/persistence/test_migration_lineage.py` (3)

- `tests/unit/persistence/test_migration_lineage.py::test_known_collisions_are_still_collisions`
- `tests/unit/persistence/test_migration_lineage.py::test_no_new_duplicate_migration_numbers`
- `tests/unit/persistence/test_migration_lineage.py::test_the_migrations_directory_is_found`

#### `tests/unit/persistence/test_task_episode_migration.py` (3)

- `tests/unit/persistence/test_task_episode_migration.py::test_task_episode_down_migration_drops_only_its_table`
- `tests/unit/persistence/test_task_episode_migration.py::test_task_episode_migration_binds_all_public_compact_limits_and_privacy_constraints`
- `tests/unit/persistence/test_task_episode_migration.py::test_task_episode_retrieval_uses_explicit_fts_matching`

#### `tests/unit/scripts/test_evaluate_email_golden.py` (3)

- `tests/unit/scripts/test_evaluate_email_golden.py::test_build_envelopes_loads_candidate_content_only_into_ephemeral_messages`
- `tests/unit/scripts/test_evaluate_email_golden.py::test_cli_rejects_invalid_selected_identity_before_constructing_or_calling_classifier`
- `tests/unit/scripts/test_evaluate_email_golden.py::test_select_shard_is_contiguous_and_limited_to_fifty_cases`

#### `tests/unit/scripts/test_evaluate_memory_provider.py` (3)

- `tests/unit/scripts/test_evaluate_memory_provider.py::test_default_provider_falls_back_to_gemini`
- `tests/unit/scripts/test_evaluate_memory_provider.py::test_default_provider_follows_llm_provider`
- `tests/unit/scripts/test_evaluate_memory_provider.py::test_unknown_provider_is_rejected`

#### `tests/unit/test_network_guard.py` (3)

- `tests/unit/test_network_guard.py::test_http_client_to_an_external_host_is_blocked`
- `tests/unit/test_network_guard.py::test_loopback_stays_reachable_for_the_postgres_and_live_tiers`
- `tests/unit/test_network_guard.py::test_raw_socket_to_an_external_host_is_blocked`

#### `tests/unit/features/ai_chat/test_memory_gateway.py` (2)

- `tests/unit/features/ai_chat/test_memory_gateway.py::test_gateway_exposes_only_authorized_durable_write_operations`
- `tests/unit/features/ai_chat/test_memory_gateway.py::test_task_episode_transition_and_deletion_gateway_signatures_keep_authority_internal`

#### `tests/unit/persistence/test_durable_chat_session_migration.py` (2)

- `tests/unit/persistence/test_durable_chat_session_migration.py::test_durable_chat_session_down_migration_removes_the_ownership_table`
- `tests/unit/persistence/test_durable_chat_session_migration.py::test_durable_chat_session_schema_stores_only_ownership_metadata`

#### `tests/unit/persistence/test_project_document_migration.py` (2)

- `tests/unit/persistence/test_project_document_migration.py::test_project_document_down_migration_removes_dependents_before_projects`
- `tests/unit/persistence/test_project_document_migration.py::test_project_document_schema_has_private_metadata_but_no_document_content`

#### `tests/integration/email_action_plan/test_rag_retrieval_golden.py` (1)

- `tests/integration/email_action_plan/test_rag_retrieval_golden.py::test_email_e2e_memory_contains_only_the_legacy_six_document_corpus`

#### `tests/unit/features/test_ports.py` (1)

- `tests/unit/features/test_ports.py::test_semantic_memory_port_is_retrieval_only`

#### `tests/unit/features/test_routing.py` (1)

- `tests/unit/features/test_routing.py::test_guard_table_matches_readme_category_mapping`

#### `tests/unit/fixtures/test_chat_routing_loader.py` (1)

- `tests/unit/fixtures/test_chat_routing_loader.py::test_chat_routing_fixture_is_balanced_and_has_required_trap_groups`

#### `tests/unit/scripts/test_evaluate_memory.py` (1)

- `tests/unit/scripts/test_evaluate_memory.py::test_a_missing_probe_set_exits_two`

#### `tests/unit/test_prompting.py` (1)

- `tests/unit/test_prompting.py::test_all_tags_are_lowercase_so_casefolded_marker_checks_match`

#### `tests/unit/test_purge_chat_memory_script.py` (1)

- `tests/unit/test_purge_chat_memory_script.py::test_purge_script_module_is_importable`

---

## 3. Explicit non-deletion note

- **Zero-hit `src/` files** → investigate missing coverage; do **not** delete tests.
- **Empty-context / empty-hit tests** → Task 3 candidates only; apply §4.
- **Coverage percentage** → not a deletion reason.
- Default suite remains uninstrumented after this scratch run.
