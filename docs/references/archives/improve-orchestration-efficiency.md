# Sol Advisor Orchestration Efficiency Playbook

Use this with `PRD2-chat-memory-orchestration.md`. The dashboard is the current
state; this file records the durable operating method and lessons. It does not
define a fixed team size or force a particular implementation lane.

## 1. Routing and ownership

The parent is architect, evidence owner, and final acceptor. It resolves
ambiguity, writes complete packets, inspects actual diffs, reruns checks, and
updates trackers. A worker report is evidence to verify, not acceptance.

| Work shape | Route |
|---|---|
| Complex, multi-file, migration, security, concurrency, or uncertain debugging | Configured native Terra/high implementation lane, then fresh Sol review |
| Independent, narrow task only when the user explicitly authorizes the Luna task lane | User-visible Luna/max app task; parent monitors, reads handoff, inspects its worktree, and reviews it itself |
| Architecture/migration commitment or final native acceptance | Fresh Sol reviewer; behaviorally read-only, with observed sandbox state recorded |

Luna/max is a separate app-task lane, not a native subagent or fallback. Parallel
work requires non-overlapping ownership and independent worktrees; migrations,
shared files, and dependency chains are serial.

## 2. Packet standard

Every implementation packet must state objective, exact owned files, interfaces,
constraints, verification commands with expected evidence, and structured return.
Add the affected PRD2 §6 acceptance IDs, excluded files, current base/status, and
the one decision the worker must not invent. For a correction, send the exact
finding and use the same worker/task rather than starting a replacement.

For an acceptance review, include allowed files, complete diff or base/head,
affected AC IDs, parent command output, threat cases, residual gaps, and
before-review hashes. Require exactly `ship`, `fix-first`, or `rethink`.

## 3. Acceptance-first slicing

Do not give one worker an entire broad milestone. Create a small vertical slice
with one observable acceptance boundary, then update the dashboard only after it
is verified.

Before a fresh review, run an acceptance-indexed adversarial checklist. For
TaskEpisodes that includes unrelated-query exclusion, `min_score`, expiry and
server bounds, direct-SQL bypass, exact citation keys, lifecycle eligibility,
cross-scope denial, and deletion non-retrievability.

## 4. Monitoring and interruption

Require a short message at four boundaries: RED, patch complete, focused GREEN,
and final handoff. Before a long command, report its scope; immediately after,
report exit status and counts. The parent may inspect public status, messages,
shared files/hashes, process command lines, and test output, but never hidden
reasoning or a private continuous tool stream.

Do not interrupt an identified active test process. If no process is active and
two bounded waits yield no useful update, interrupt the stale turn, inspect the
diff, run the smallest parent check, and return only concrete findings to the
same worker.

## 5. Verification and environment discipline

Preflight Docker/`cowork-pg`, migration baseline, writable `TEMP`/`TMP` and
pytest base temp, and no competing PostgreSQL test process before delegating a
database slice. If Docker is unavailable, notify the user and pause the live
database lane; skipped tests are not acceptance evidence.

Run one PostgreSQL actor at a time. The verification ladder is targeted RED/GREEN,
persistence, domain + AI Chat for shared contracts, lint/type/diff, then the full
suite once after the final correction. A later fix invalidates the earlier Sol
verdict and requires a new fresh review.

## 6. Evidence and tracker discipline

Keep the PRD2 dashboard small: current milestone, grouped AC status, next queue,
blockers, and latest verification snapshot. Put exact command output in the task
handoff/review packet; Git retains historical change evidence. Update
`tasks/todo.md` and `tasks/plan.md` only from a verified PRD2 boundary.

### Pruning maintenance rule

At every verified milestone transition and before a session handoff, the parent
must replace obsolete dashboard rows rather than append history. Keep in PRD2
only the current status, next decision, active blocker, and newest proof needed
for that decision; move reusable operating guidance here and historical detail
to Git or a task handoff. Prune duplicate or project-specific lessons from this
playbook unless they change future routing, verification, or interruption
decisions. Do not date-bump either document for formatting-only edits or
unverified reports.

For a reviewer without enforced read-only isolation, capture before/after status
and hashes. Stop the review if unexpected mutation occurs; never describe a
requested sandbox as OS-enforced without host evidence.

## 7. Session lessons to retain

| Observation | Standing response |
|---|---|
| Final review found missing query/min-score behavior, then permissive citation keys | Use the acceptance-indexed checklist before the final review and include direct-storage tampering tests |
| Worker turns stalled after substantive edits or focused tests | Use checkpoint reports and bounded interruption rather than waiting for an unreported broad run |
| PostgreSQL work shares migration/schema state | Serialize database actors and keep parent verification outside a worker's active DB run |
| Windows pytest temp teardown can fail independently of test bodies | Use an explicit writable external temp/base-temp for broad evidence and label the default-temp failure as environmental |
| Dashboard history grew faster than actionable context | Keep only latest proof in PRD2; retain methods here and historical detail in Git |
