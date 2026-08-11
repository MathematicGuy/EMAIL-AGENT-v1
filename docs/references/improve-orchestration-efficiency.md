## Improvements for faster PRD-v2 acceptance closure

This session was safe, but it spent too much time in silent verification tails
and found acceptance gaps only during final review. The next orchestration should
use these changes:

| Improvement | Why it is faster | Application to PRD2 §6 |
|---|---|---|
| Work by acceptance cluster, not by a broad milestone packet | Small dependency-ordered slices avoid reopening unrelated work | Accept M3.4a storage evidence for AC-07–AC-12, AC-14, and AC-15; split M3.4b into producer (AC-06/AC-07), lifecycle/deletion (AC-08/AC-09/AC-15), and retrieval/model-context (AC-10–AC-12); handle live reply consumption for AC-01, AC-05, AC-13, and AC-17 afterward |
| Run an adversarial acceptance checklist before requesting Sol review | It catches contract-shaped omissions before an expensive fresh-review cycle | Require tests for unrelated-query exclusion, `min_score`, expiry/bounds, direct-SQL bypass, exact citation keys, lifecycle eligibility, cross-scope denial, and deletion non-retrievability |
| Split implementation, focused verification, and reporting into explicit checkpoints | The parent can see which phase stalled and does not wait for an unreported broad command | The worker reports RED, patch complete, focused GREEN, and handoff separately; the parent owns persistence/domain/full-suite gates |
| Require a command heartbeat | Long silence becomes distinguishable from legitimate test execution | Before a long command, report its exact scope; immediately afterward, report exit status and counts. If no process is active and two bounded waits return nothing, interrupt and reuse the same agent with a narrower correction |
| Preflight the environment before delegation | It avoids skip-only evidence and agents waiting on infrastructure | Confirm Docker and `cowork-pg`, writable `TEMP`/`TMP` and pytest base temp, migration baseline, and no competing pytest process. If Docker is off, notify the user and pause the PostgreSQL lane |
| Serialize shared PostgreSQL work while parallelizing only non-overlapping reads | It removes migration/schema races without wasting all concurrency | Only one parent or worker runs PostgreSQL tests at a time; status/hash capture, acceptance mapping, and read-only review-packet preparation may proceed independently |
| Use a verification ladder and reuse valid evidence | Repeating the full suite after every small correction adds latency without improving the affected proof | Run the new RED/GREEN test first, then the persistence suite, then domain + AI Chat for shared contracts, and run the full suite once after the last correction before final review |
| Make the final-review packet acceptance-indexed | A reviewer can target missing behavior instead of rediscovering the product contract | List affected AC IDs, allowed files, threat cases, exact parent evidence, residual gaps, and before-review hashes; require exactly `ship`, `fix-first`, or `rethink` |
| Reserve capacity for the mandatory fresh reviewer | Correction cycles cannot finish if every child slot is already occupied | Reuse one implementer for every correction, retire stalled turns promptly, and avoid optional agents once parent gates are green |
| Update trackers only at verified boundaries | It prevents repeated reconciliation of stale percentages and next actions | Keep PRD2 §6 authoritative; update `tasks/todo.md` and `tasks/plan.md` from it after a slice is accepted or explicitly marked `VERIFY` |

The most important change is to perform the acceptance-indexed adversarial
check before the fresh Sol review. In this session, that would likely have found
both late `fix-first` issues before consuming two reviewer cycles.
