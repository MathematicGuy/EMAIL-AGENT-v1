# Grok self-profile and working style

Session note: when the user says “save it” in this conversation, default destination is
`E:\VIN-INTERNSHIP\EMAIL-AGENT-v1\docs\references\understand\`.

I'm **Grok 4.5**, built by **xAI**, running inside the **Grok Build** coding harness (interactive CLI/TUI). In this environment I'm not a chatbot that only talks — I'm an agent that can read/edit your repo, run shell commands, spawn subagents, use skills/MCP tools, search the web, and drive long multi-step engineering work.

What follows is an honest operating profile: strengths, limits, how I differ from Codex and Claude Code, how I approach internship-scale vs enterprise work, and whether I can learn your taste over a project.

---

## Reasoning: Grok vs Codex vs Claude Code (latest)

These products change fast, so treat this as **practical engineering comparison**, not a lab leaderboard.

| Dimension | **Grok 4.5 (me)** | **Codex (OpenAI coding agents)** | **Claude Code (Anthropic, latest Opus/Sonnet class)** |
|---|---|---|---|
| **Default posture** | Direct, exploratory, willing to push back; less ceremonial | Task-execution oriented; strong when scoped tightly | Very strong at careful, multi-file reasoning and constraint following |
| **Long-horizon reasoning** | Strong; good at synthesis and “what should we actually do?” | Strong on well-bounded implementation; can overthink when scope is fuzzy (especially high-effort modes) | Often best-in-class at deep, structured multi-step planning and keeping many constraints in mind |
| **Code generation volume** | Fast and fluent | Excellent for scaffolding, services, tests, bulk code | Excellent; often more conservative/correct on subtle edge cases |
| **Judgment under ambiguity** | High willingness to surface tradeoffs and ask | Depends on mode; RTK notes: Sol for hard judgment, Terra for standard coding, Luna for mechanical | Strong; often pauses / designs before coding |
| **Tool-use discipline** | Built around tools, skills, subagents, verification loops | Strong CLI/agent loop | Very strong agent loop; mature ecosystem of skills/hooks |
| **Risk profile** | Can move fast; needs your project rules to stay surgical | Can loop or overbuild if scope is unclear | Can be verbose/over-careful; often safer on architecture |
| **Where I often win** | Exploration + synthesis + “ship something real now” | High-throughput implementation once the target is clear | Deep correctness, architecture, review, complex refactors |
| **Where I often lose** | Ultra-strict multi-constraint enterprise review without enough process | Fuzzy product/architecture calls if not delegated to the right tier | Sometimes slower / more process-heavy for tiny MVP tasks |

### Plain-language take

- **Claude Code** is often the strongest “senior staff engineer in the loop” for hard reasoning, subtle bugs, and keeping a large design coherent.
- **Codex** is often the strongest “implementation factory” when the task is clear: generate services, tests, migrations, mechanical refactors.
- **I (Grok)** tend to excel at **fast, opinionated product/engineering synthesis**: figure out the smallest real path, explore the codebase, explain tradeoffs bluntly, and execute without ceremony — especially when you want momentum on a real repo, not a perfect paper design.

I will **not** claim I always beat either on pure reasoning benchmarks. On hard correctness work, I deliberately use process (tests, review skills, subagents, adversarial checks) to compensate.

---

## What I excel at (relative to them)

### Relative strengths

1. **Blunt prioritization** — I push for the smallest thing that proves value. Good for internships and MVPs.
2. **Codebase exploration → action** — read layout, ports, ADRs, then implement in the existing shape.
3. **Cross-domain synthesis** — product + architecture + RAG/LLM quirks + “what breaks in demos.”
4. **Interactive collaboration** — I surface assumptions, push back when something smells, and don’t pretend a bad plan is fine.
5. **Harness-native multi-agent work** — explore/plan/implement/review as separate context windows instead of stuffing everything into one brain dump.

### Where Codex often beats me

- Pure bulk generation once the contract is fixed
- Very mechanical, high-volume test scaffolding
- “Just implement the spec exactly” sprints with minimal discussion

### Where Claude Code often beats me

- Deep multi-file architectural consistency under many constraints
- Careful adversarial review of subtle security/correctness issues
- Long sessions where instruction-following and patience matter more than speed

### Best combo in practice

- **Claude** for hard judgment / rescue when something is looping
- **Codex** for medium/high-volume implementation when scoped
- **Me** for driving the loop: clarify → plan thin slices → implement → verify → integrate, and call the others when the task shape fits them

---

## How I build: internship / capstone / small MVP

Default philosophy: **demoable truth over abstract purity**.

1. **Clarify the real win**  
   For an internship: what must work in the demo? What can be fake, in-memory, or single-user?

2. **Read the project’s own rules first**  
   In this repo that means `Agents.md`, ADRs, PRDs, layering (`domain ← features ← integrations ← app`), and verification commands.

3. **Vertical slice, not horizontal layers**  
   Example for email-to-action-plan:
   - one mailbox connection path
   - one unread fetch path
   - one LLM call with a fake for tests
   - one API response shape
   - one GUI/happy path  
   Not: full multi-tenant auth + queue + observability + every provider before anything works.

4. **Deterministic fakes early**  
   This project already does this (Gmail fakes, LLM fakes). Lean hard on that so tests don’t need live APIs.

5. **Small verification scope**  
   Smallest pytest path first; ruff/mypy when `src/` changes. Full suite when contracts move.

6. **Document only decisions that will bite later**  
   Short ADR / note when a choice freezes an interface; no essay for every helper.

7. **Demo script as acceptance criteria**  
   “Connect → fetch → plan appears → error path shows something sensible.”

**Anti-goals on internship MVP:** premature microservices, over-abstract ports for one adapter, enterprise observability before the happy path works, “future-proof” config forests.

---

## How I build: enterprise-level projects

Different game. Correctness, operability, and change safety dominate.

1. **Spec and boundaries first**  
   Public APIs, domain model, authn/z, data ownership, failure modes, SLOs.

2. **Architecture that survives people**  
   Clear module boundaries, dependency direction, migration strategy, deprecation plan.

3. **Threat model where it matters**  
   Auth, tokens, multi-tenant data isolation, LLM prompt injection / data exfil, secrets handling.

4. **Observability as a feature**  
   Structured logs, traces, metrics, alerts tied to user symptoms — not afterthought prints.

5. **Incremental delivery with safety rails**  
   Feature flags, dual-write/read when migrating, rollback plan, CI gates (tests, types, lint, security).

6. **Review and adversarial checks**  
   Multi-axis code review, security pass, performance only after measurement.

7. **Change management**  
   Atomic commits, ADR for irreversible decisions, migration docs, compatibility windows.

On enterprise work, slow down intentionally: more plan mode, more subagent isolation, more verification, less “just ship the clever path.”

---

## Harness (what surrounds the model)

The model is only the brain. The **harness** is what makes it useful on a real repo.

### Core loop

1. Read project rules (`Agents.md`, skills, ADRs)
2. Explore code with tools (read/grep/list)
3. Plan if ambiguous
4. Edit surgically
5. Run verification (tests, lint, types, browser when UI)
6. Report with evidence, not vibes

### Tooling surface

| Layer | What it does |
|---|---|
| **File + shell tools** | Read/edit code, run pytest/ruff/mypy, git, servers |
| **Skills** | Encoded engineering workflows (TDD, security, review, handoff, RAG eval, etc.) |
| **MCP** | External systems (docs via Context7, tasks, etc.) |
| **Subagents** | Parallel specialized children with separate context |
| **Workflows** | Scripted multi-agent orchestration (Rhai) for larger fan-out |
| **Plan mode** | Read-only exploration + implementation plan before edits |
| **Memory** | Optional cross-session recall (experimental; off by default) |
| **Project rules** | Always-on constraints from the repo |
| **Permissions/safety** | Ask vs auto-approve; deny rules still apply |
| **Browser verification** | For UI work: exercise real flows, not screenshot-only |

### Skills as process, not decoration

This environment has a full skill lifecycle: interview → spec → plan → implement → test → review → ship. Skills are **checklists that prevent common failure modes**, not optional flavor text.

### Project-specific harness (EMAIL-AGENT-v1)

- Layered architecture and fakes
- Verification rule: smallest pytest, then ruff/mypy on `src/`
- Handoff before context compaction
- Authoritative docs: ADRs, target architecture, PRDs

That means work here should behave like someone who already joined the team, not a greenfield vibe coder.

---

## How subagents are used

Subagents are **child sessions with their own context window**. They exist so the main agent doesn’t drown in raw exploration or parallel work.

### Built-in shapes

| Type | Role | Typical use |
|---|---|---|
| **`explore`** | Read-only research | “Map how Gmail OAuth is wired” |
| **`plan`** | Read-only design | “Propose the implementation plan for RAG retrieval” |
| **`general-purpose`** | Full capability | Implement a bounded slice, write tests, etc. |

There are also specialized plugin/role agents depending on what’s installed (code review, security audit, test engineer, performance, Codex rescue, Antigravity/Gemini delegation, etc.).

### When to delegate

After a **written implementation plan** exists, default to subagents for
implementation. Do not wait to be asked.

Spawn a subagent when:

- A plan task is **file-disjoint** from the other in-flight tasks (TDD slice)
- The work is **independent** of what the main session is holding
- Exploration would **blow up** the main conversation with noise
- A **fresh adversarial view** is needed (review/security)
- Parallelism helps (tests + research + scaffold)

Do **not** delegate when:

- The edit is tiny and local
- The task needs continuous judgment across the whole design
- Round-trip cost exceeds the savings
- The work is planning, spec/map edits, a later capability-map module, or
  the final suite / Definition of Done (parent keeps those)

### Operating pattern

```text
Main agent (conductor)
  ├─ explore: map codebase / contracts
  ├─ plan: produce implementation blueprint (must finish first)
  ├─ Wave N: general-purpose implementers, file-disjoint TDD tasks only
  ├─ next wave only after that wave's dependencies landed
  ├─ parent: integrate, ruff/mypy, focused then full verify
  └─ reviewer / security auditor before merge
```

Parent integrates results, verifies, and owns the final call. Subagents report summaries; they don’t replace integration judgment. One task per implementer, closed file list, focused `uv run pytest`.

Children can be isolated in a **git worktree** so experimental edits don’t dirty the main workspace until merged.

---

## Learning code taste over a project

**Yes — partially and practically.** Not like a human mentor who permanently rewires identity, but through durable project memory and feedback loops.

### What can be learned

| Signal | How it sticks |
|---|---|
| Explicit prefs (“no service locator”, “prefer ports/adapters”, “short commits”) | Project rules, `Agents.md`, ADRs, skills |
| Patterns in the code | Copy existing style: naming, layering, test fakes, error shapes |
| Corrections mid-project (“don’t abstract that”, “this is too clever”) | Immediate compliance; write into rules/docs so future sessions keep it |
| Review comments / rejected PRs | Strong taste signals if pointed at |
| Cross-session memory (if enabled) | Experimental recall of decisions/conventions across sessions |

### What works best

1. **Correct once, then freeze the rule**
2. **Show, don’t only tell** — point at a PR or file and say “match this density/style.”
3. **Keep taste in the repo, not only chat** — chat dies; `Agents.md`, ADRs, lint config, and examples survive.
4. **Enable memory if you want continuity across sessions** — memory is experimental and off by default; rules files are more reliable.

### Limits

- Aesthetic taste is not absorbed from silence.
- Long sessions can lose nuance unless we hand off / write it down.
- If two maintainers disagree, a written tie-breaker is needed.
- Drift toward generic “AI code” is possible if unconstrained — feedback is the fix.

### Practical taste-learning contract

Highest leverage for this internship project:

1. State 5 non-negotiables (architecture, testing, error handling, comments, PR size).
2. Treat existing good modules as golden examples.
3. Correct early and sharply.
4. Promote stable prefs into `Agents.md` / ADRs so future agent sessions inherit them.

---

## How to use Grok on EMAIL-AGENT-v1

Given this repo (FastAPI email-to-action-plan, local MVP, SQLite, fakes, RAG milestone):

- Use Grok as **primary driver** for feature slices, debugging, test tightening, and demo readiness.
- Pull **Codex** for bulk tests/scaffolding when the interface is already fixed.
- Pull **Claude** when architecture is contested, something is looping, or a subtle integration is broken.
- Keep **project rules + verification** as the real source of truth; models are interchangeable, harness + taste are not.

---

## Short self-summary

Grok is a **fast, tool-heavy engineering agent** optimized for shipping real work in a real repo with clear constraints. Often better than pure chat models at *doing*, competitive with Codex on implementation when scoped, and sometimes behind Claude on the hardest multi-constraint reasoning — unless process, tests, and review compensate.

- Internship MVP: optimize for **thin vertical truth**.
- Enterprise: optimize for **boundaries, safety, and operability**.
- Subagents are **specialized parallel brains**, not abdication of ownership.
- Taste can be learned **if made explicit and durable**.
