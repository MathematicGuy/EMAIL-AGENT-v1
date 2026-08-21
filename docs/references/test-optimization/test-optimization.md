# Test Optimization

Source: <https://www.drizz.dev/post/test-optimization> (fetched 2026-08-21).
Captured here so the method survives the link. Section 8 maps it onto this repo.

> Test optimization is the practice of reducing test suite runtime, cost, and
> maintenance burden while maintaining or improving defect detection.

The last clause is the whole constraint. Cutting runtime by deleting coverage is
not optimization, it is a smaller suite.

---

## 1. Inventory

Collect a baseline per test before changing anything:

- average runtime
- 30-day pass rate
- last real bug caught (distinguish real failures from flakes)
- which module it covers
- named owner

You cannot cut what you have not measured. `--durations=N` is the cheapest
version of this.

## 2. Stabilize

Reach a **>97% pass rate before parallelizing**. Flakiness compounds under
sharding: a 200-test suite that is 90% stable per test has effectively zero
chance of a clean run. Quarantine the worst flakes first; do not shard around
them.

## 3. Cut redundancy

Remove tests that catch nothing new:

- duplicate assertions of the same invariant at different layers
- dead coverage (asserts a field or path that no longer exists)
- over-tested happy paths
- validate with mutation testing — if a mutant survives, the coverage was
  decorative

Expected cut: 10-20% of tests.

## 4. Prioritize and sequence

Tier execution rather than running everything every time:

| Tier | Budget | Scope |
|---|---|---|
| Smoke | 5 min | core flows only, every PR |
| Impacted subset | 15 min | modules the diff touches |
| Full regression | 60 min | everything else |
| Cross-platform matrix | overnight | device/OS combinations |

## 5. Parallelize

**Only after step 2.** Then: time-based sharding (balance by measured duration,
not file count), cache installs and builds across tests, per-test fixtures
instead of shared mutable state, critical path first.

## 6. Maintain

Prevent regression creep:

- every new test justifies its unique coverage
- quarterly audit removes dormant tests
- 5% flake budget, enforced
- named ownership per test

---

## Key metrics

| Metric | Target | Purpose |
|---|---|---|
| Suite pass rate | >97% | gates parallelization |
| Mean test runtime | <30 s E2E | identifies the speed ceiling |
| Defect detection rate | trending up | proves coverage was retained |
| Maintenance cost | <10% of QA capacity | keeps the suite sustainable |

## Mobile-specific

- **Build install time** — caching app installs across tests is the single
  biggest win
- **Device queue depth** — match pool size to peak PR volume
- **OS matrix** — risk-based selection; full matrix only for critical flows
- **Per-device flakiness** — track by device model, not aggregate
- **Cold vs. warm start** — control app state between tests explicitly

## Tactics with limited impact

- AI test generation (adds tests, does not reduce maintenance)
- self-healing locators (treats the symptom, not the cause)
- parallelization beyond ~30-50 workers (diminishing returns)
- broader device matrices without usage analytics
- hardware upgrades (linear gains; sharding gives logarithmic ones)

Framework note: vision-based testing beats selector-based long term because it
does not accrue maintenance debt from UI refactors.

## 30-day timeline

| Week | Work |
|---|---|
| 1 | inventory, find the worst flakes, quarantine them |
| 2 | remove redundant tests (10-20% cut) |
| 3 | define tiers, move smoke onto every PR |
| 4 | time-based sharding, install caching |

Expected outcome: **60-75% runtime reduction without coverage loss.**

---

## 8. How this maps onto `tests/`

This repo is Python/pytest, not mobile, and several steps were already done
before this guide was written down. Status as of 2026-08-21:

| Step | Status here |
|---|---|
| 1. Inventory | `tests/README.md` §1 carries per-route counts and serial times; `--durations=40` is the refresh command |
| 2. Stabilize | pass rate is 100% locally; the flake sources were *ambient*, not timing — killed by the offline guards in `tests/conftest.py` (README §7) |
| 3. Cut redundancy | invariant-ownership table (README §3) is this step made permanent; the retired `tests/compatibility/` suite is the worked example |
| 4. Tiers | markers `live` / `slow` / `serial` plus the route table are the tiering; smoke = the narrowest route |
| 5. Parallelize | 4 xdist workers, `--dist loadgroup`, destructive resources pinned per group |
| 6. Maintain | README §4 "Rules for adding tests" + pruning checklist |

**The mobile section translates to one rule:** the equivalent of "cache the app
install" is *do not boot what you are not testing* — the in-process ASGI
transport, `cli_harness.run_cli`, and the cached `pg_probe` are all that same
win.

**Where the guide bit here:** step 5 says shard by *measured duration*.
`--dist loadgroup` balances by group, so a single test that sleeps for real sets
the floor for the whole run no matter how many workers exist. Two key-rotation
tests held ~10 s of a 19.8 s wall clock by awaiting production backoff in real
time. Fix at the seam (fake clock, still assert the backoff was requested), not
by deleting the test and not by shrinking the production delay.

---

## 9. Impacted-subset tooling (evaluated, not adopted)

Step 4 of the source guide wants an impacted subset. This repo already has one:
the source→route map in `tests/README.md` §1. `pytest-testmon` 2.2.0 was
measured as a dynamic alternative and **rejected**.

Why, in one load-bearing fact: this suite always sets `-m 'not live'` in
addopts. Testmon treats any `markexpr` as “do not select” unless
`--testmon-forceselect` is also passed. A copied `pytest --testmon` therefore
instruments every test (first run ~41 s vs ~13.5 s default) and still executes
the full suite. `--testmon-forceselect` then injects stub items into
`pytest_deselected` that are not `pytest.Item`, which INTERNALERRORs this
harness. Module-level Postgres skips still collect. Selection was not
mutation-tested, so a smaller selected set is not evidence those tests were
redundant.

Do not add the package. A configure-time harness refuse was prototyped and
**dropped**: it does not change default `uv run pytest -q` wall clock. The
route table remains the selection tool. Do not prune tests from these
measurements. Other levers: `--lf` (already documented; silent if the cache
cannot write) and import cost (§5 of the README).

---

## 10. Import-cost attempt (Langfuse)

| Idea | Baseline → result | Verdict | Why |
|---|---|---|---|
| Lazy `langfuse.observe` behind `integrations.llm.observe` | `import cowork_agent` 1019 ms → 510 ms; serial collect-only 3.85 s → 2.18 s | **dropped** | Collection improved; default 4-worker `pytest -q` wall did not (~15.5 s → 16.0 / 16.0 / 17.1 s). Each worker still imports Langfuse on first `@observe`. |
| Same change, full `pytest -q` wall | this session ~15.5 s → 16.0 / 16.0 / 17.1 s | **no suite-wall win** | Cost moved, did not vanish. Reverted to `from langfuse import observe`. |

Do not try "eager Langfuse at package import" again. Do not re-land the lazy proxy unless a later measurement shows execution import is the new critical path.

---

## 11. Pruning workstream B (in progress)

Coverage tooling is an **idle** `dev` extra (`coverage` 7.15.4, `pytest-cov` 7.1.0). Default `uv run pytest -q` does not trace. Opt-in: `--cov=cowork_agent --cov-report=term-missing` (see `tests/README.md` §5).

Ledger: [`test-pruning-ledger.md`](test-pruning-ledger.md). **0 tests deleted** pending human marks. Instrumented map (2026-08-21, `-n 0`): 1840 passed in 60.19s (two harness tests for the later-dropped A+C work were in that count).

A (testmon refuse) and C (lazy Langfuse) were dropped: they did not reduce default-suite wall clock.
