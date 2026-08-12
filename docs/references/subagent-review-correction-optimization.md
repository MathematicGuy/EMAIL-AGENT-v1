# Subagent Review and Correction Optimization

**Status:** Proposed orchestration improvement

**Date:** 2026-08-11
**Workspace:** `E:\VIN-INTERNSHIP\EMAIL-AGENT-v1` (`dev`; the former `.worktree\v2-m3-chat-summary` is no longer active after merge)

## 1. Current communication path

The reviewer does not automatically hand context directly to the implementation agent.
The primary orchestrator owns the handoff:

```text
implementation worker
    -> structured implementation report
primary orchestrator
    -> inspects the real diff and reruns scoped proof
fresh reviewer
    -> ship | fix-first | rethink, with concrete findings
primary orchestrator
    -> validates findings and sends a correction packet
same implementation worker
    -> applies the correction and reports new proof
primary orchestrator
    -> verifies the corrected diff
fresh final reviewer
```

Agents can exchange messages, but direct reviewer-to-implementer delegation is intentionally
not the default. The orchestrator remains responsible for deciding whether a reviewer finding
is valid, in scope, and acceptance-blocking.

## 2. Context format used today

An implementation packet contains:

1. objective and observable outcome;
2. exact owned and excluded files;
3. interfaces and locked decisions;
4. safety and scope constraints;
5. RED, GREEN, regression, lint, type, and diff-check commands;
6. a structured return format.

A final-review packet contains:

1. stated product goal and acceptance criteria;
2. base revision and exact accumulated file set;
3. authoritative contracts and excluded scope;
4. primary-session verification evidence;
5. pre-review hashes;
6. threat cases and review focus;
7. the required `ship | fix-first | rethink` verdict.

A correction packet contains only the accepted reviewer findings, the required behavior,
expanded ownership when necessary, and the smallest new verification ladder.

### Quality assessment

The packets are generally source-grounded and clean enough to prevent silent redesign, but
they are currently too verbose at repeated review boundaries. The first fresh review benefits
from the full acceptance packet. A corrected re-review usually does not need the entire product
history again; it needs the previous findings, changed files and hashes, correction design, and
new proof.

The main inefficiency is therefore not the existence of a handoff. It is repeating stable
context instead of sending a compact delta packet.

## 3. Why not let the reviewer always implement its own fixes?

There is a real speed advantage:

- the reviewer already understands the defect;
- no correction explanation needs to be transferred;
- small edits could be applied immediately;
- one agent can keep the failure model in working context.

However, letting the same reviewer both edit and approve its edit has important costs:

- **Loss of independent judgment.** After implementing a fix, the reviewer is more likely to
  defend its chosen solution or miss a new defect it introduced.
- **Role ambiguity.** A read-only reviewer answers “is this safe to ship?” An implementer
  answers “how should I make this pass?” Combining them weakens both prompts.
- **Evidence contamination.** Pre/post hashes and behavioral read-only checks no longer prove
  that the review itself did not mutate the artifact.
- **The final review is still required.** If the reviewer edits production behavior, an
  independent reviewer must inspect that new change. Direct fixing removes one handoff but does
  not safely remove the final independent verdict.
- **Routing mismatch.** The configured reviewer is GPT-5.6 Sol/medium with read-only intent;
  implementation is routed to GPT-5.6 Terra/high. Making the reviewer edit silently changes the
  selected role and risk profile.

This is not an argument that reviewer-written fixes are always bad. It is an argument against
allowing an agent to implement a behavioral fix and then issue the authoritative `ship` verdict
on that same fix.

## 4. Recommended hybrid

Use reviewer findings as a delta, not as a full restart.

### A. Mechanical correction lane

Examples: import order, test filename collision, typo, exact literal or annotation change with
no runtime behavior change.

- Reviewer returns the exact finding and optional patch sketch.
- The same implementation worker applies it through a short follow-up turn.
- Run only the directly affected check.
- Fold it into the pending final review; do not create a separate advisor round.

If a future orchestration profile explicitly permits reviewer edits, the reviewer may apply a
mechanical patch, but it must not approve that patch. A fresh independent reviewer still owns
the final verdict.

### B. Behavioral correction lane

Examples: retry state machine, authorization logic, persistence exception handling, tenant or
session scoping.

- Primary validates the reviewer finding against source and contract.
- Send a compact correction packet to the same implementation worker.
- Require one focused RED/GREEN and one impacted regression scope.
- Obtain a fresh independent verdict because runtime behavior changed.

### C. Architecture correction lane

Examples: public contract change, new persistence authority, dependency-direction change.

- Primary revises the architecture/specification first.
- Implementation worker applies the revised packet.
- Fresh final reviewer evaluates the full boundary.

## 5. Compact correction packet

Use this format after `fix-first`:

```text
CORRECTION REVIEW ID
<stable short identifier>

PREVIOUS VERDICT
fix-first

ACCEPTED FINDINGS
1. <file:line, failing behavior, violated acceptance criterion>
2. <file:line, failing behavior, violated acceptance criterion>

REQUIRED DELTA
- <observable corrected behavior>
- <what must remain unchanged>

OWNERSHIP
- <exact files allowed>

PROOF
- RED: <focused command and expected failure>
- GREEN: <same focused command>
- REGRESSION: <one impacted scope>
- STATIC: <scoped lint/type/diff>

RETURN
- changed files and hashes
- exact outputs
- gaps or none
```

The corrected fresh reviewer should then receive:

```text
PREVIOUS FINDINGS
<the accepted findings only>

CORRECTION CLAIMS
<how each finding was addressed>

CHANGED SINCE REVIEW
<only files and hashes changed after the prior verdict>

NEW EVIDENCE
<focused and impacted proof>

REVIEW QUESTION
Are the previous blockers closed without a new acceptance blocker?
```

It should not reread unrelated milestones, old handoffs, or the entire documentation set.

## 6. Optimized orchestration rules

1. Use no pre-implementation advisor when architecture and contracts are already settled.
2. Use one implementation worker per serial dependency chain; send corrections to that same
   worker.
3. Use one fresh reviewer at the final behavioral boundary.
4. If verdict is `fix-first`, primary validates each finding before routing a correction.
5. Do not create a new implementation agent for a correction unless the original worker is
   unavailable or ownership must be isolated.
6. Do not repeat unchanged tests. Re-run only checks invalidated by the latest edit.
7. Re-review with a compact delta packet; do not resend stable project history.
8. Never let an agent both implement a behavioral correction and issue the authoritative final
   verdict for that correction.
9. Reviewer runtime longer than expected is not an interruption reason. Inspect status, hashes,
   active tests, and checkpoints first.
10. Known unrelated suite failures remain excluded unless the current change touches their
    dependency boundary.

## 7. Decision

Keep the orchestrator-mediated correction handoff and independent final verdict, because they
preserve scope control and honest acceptance. Reduce their cost by replacing repeated full
packets with correction deltas, reusing the same implementation worker, removing intermediate
advisor rounds, and scaling verification to the files and behavior changed.

The reviewer may propose exact fixes and may be used as an implementer only in an explicitly
separate role. Once it edits behavior, it is no longer the independent final reviewer for that
change.
