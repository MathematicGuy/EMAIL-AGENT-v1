# Implementation Plan — Architecture Documents Path Update

Update the 4 current architecture documents in `docs/architectures/current-architectures/` to align all paths, symbols, package-tree citations, line numbers, and former `infrastructure/memory.py` splits with the reorganized `src/cowork_agent/` codebase as specified in `path-update-handoff.md`.

## Tasks

### Task 1: Update `current-overall-architecture.md`
- **Files:** `docs/architectures/current-architectures/current-overall-architecture.md`
- **Validation command:** `python -m pytest tests/unit/features/email_action_plan/test_policies.py -q`
- **Criteria:** All `mail_todo` paths replaced with `cowork_agent` paths, package tree updated, symbol split cited, line numbers updated.
- **Status:** done

### Task 2: Update `current-email-architecture.md`
- **Files:** `docs/architectures/current-architectures/current-email-architecture.md`
- **Validation command:** `python -m pytest tests/unit/features/email_action_plan/test_policies.py -q`
- **Criteria:** All `mail_todo` paths replaced with `cowork_agent` paths, entry point and split paths updated, line numbers updated.
- **Status:** done

### Task 3: Update `current-rag-architecture.md`
- **Files:** `docs/architectures/current-architectures/current-rag-architecture.md`
- **Validation command:** `python -m pytest tests/unit/features/email_action_plan/test_policies.py -q`
- **Criteria:** All `mail_todo` paths replaced with `cowork_agent` paths, package tree updated, RAG absence claims preserved.
- **Status:** done

### Task 4: Update `current-architecture-review.md`
- **Files:** `docs/architectures/current-architectures/current-architecture-review.md`
- **Validation command:** `python -m pytest tests/unit/features/email_action_plan/test_policies.py -q`
- **Criteria:** All `mail_todo` paths replaced with `cowork_agent` paths, line citations updated, historical evidence preserved, new verification recorded separately.
- **Status:** done

### Task 5: Demo-validation task (Verify all 4 architecture documents)
- **Files:** All 4 docs in `docs/architectures/current-architectures/`
- **Validation command:** `python -m pytest tests/unit/features/email_action_plan/test_policies.py -q`
- **Criteria:** All line citations resolve against live `src/cowork_agent/` files, no broken references exist, tests pass.
- **Status:** done
