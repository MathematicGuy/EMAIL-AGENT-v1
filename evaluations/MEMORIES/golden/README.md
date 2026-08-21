# Golden answers for `v2_four_scopes_wide`

Reference answers — what the product *should* reply to each of the 20 probes —
so a judge can grade a run's reply against a specification instead of matching
phrases.

**Nothing in the harness reads this yet.** `score()` still grades by phrase
matching. This directory holds the prompts that produce the golden data and,
once they are run, the data itself. Wiring a judge into the harness is a later
change and a separate decision.

## What is here

```
prompts/CONTRACT.md    the rules every scope prompt inherits — read first
prompts/short-term.md  one prompt per scope, so four can run in parallel
prompts/long-term.md
prompts/episodic.md
prompts/semantic.md
parts/<scope>.json     what a subagent writes
v2-four-scopes-wide.golden.json   the four parts merged
```

## Running them

Hand one scope prompt to a subagent, together with `CONTRACT.md`. The four
scopes are independent, so run them in parallel; a scope that comes back wrong
is regenerated on its own without touching the other three.

Each subagent writes exactly one file, `parts/<scope>.json`. Merge the four
`answers` arrays into `v2-four-scopes-wide.golden.json`, concatenating their
`defects` alongside.

Before merging, check the parts cover all 20 probes:

```bash
.venv/Scripts/python.exe - <<'PY'
import json, pathlib
probes = {p["id"] for p in json.loads(
    pathlib.Path("evaluations/MEMORIES/probes/v2-four-scopes-wide.json")
    .read_text(encoding="utf-8"))["probes"]}
answered, reported = set(), set()
for part in pathlib.Path("evaluations/MEMORIES/golden/parts").glob("*.json"):
    data = json.loads(part.read_text(encoding="utf-8"))
    answered |= {a["probe"] for a in data["answers"]}
    reported |= {d["probe"] for d in data.get("defects", [])}
print("missing entirely:", sorted(probes - answered - reported))
print("reported as defective:", sorted(reported))
PY
```

A probe in neither list is a silent hole: the golden file would simply have
nothing to say about it, and a judge reading it would have nothing to notice.

## Why the golden answers are committable when `runs/` is not

`runs/` is gitignored because it holds **model output about a run**. A golden
answer is written before any run exists, from invented fixture text, so it is a
specification — the same category as the probe file itself.
`evaluations/HARNESS-GUIDE.md` §3 permits invented fixture text and forbids real
user content; nothing here is real.

## Why a judge at all

`SPEC-memory-evaluation.md` §6.3 designed an LLM judge and deliberately did not
build it, then named its own expiry: *"Revisit this if the probe set ever grows
past what a person will read by hand."* v2 is 20 probes × 3 arms = 60 replies
per run, and §7.3 requires repeated runs before any comparison between two runs
is defensible. Hand-adjudicating the uncertain rows stops happening at that
size.

§6.3's objections are **not** answered by this directory — a judge still adds a
provider dependency, still costs a call per uncertain row, and still degrades to
`certain=false` when it cannot be reached. Producing the golden data does not
commit us to running one. It removes the reason we could not.

Full reasoning: [SPEC-memory-eval-probe-set-v2.md](../../../tasks/specs/SPEC-memory-eval-probe-set-v2.md) §8.
