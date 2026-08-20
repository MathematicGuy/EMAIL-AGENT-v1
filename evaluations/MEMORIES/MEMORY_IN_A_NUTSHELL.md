# Memory in a Nutshell

**Start here.** What the memory evaluation measures, and how to read what it
says. [README.md](./README.md) is how to run it, [RUNBOOK.md](./RUNBOOK.md) is
how to run it properly, and
[SPEC-memory-evaluation.md](../../tasks/specs/SPEC-memory-evaluation.md) is why
every design decision was made. This file does not repeat those; it links.

---

## 1. The question this answers

An ordinary benchmark asks *"did the reply contain the right words?"* and prints
PASS or FAIL. In a system with four separate memories, that is blind. Ours asks:

> **Does memory make the answer better, and which of the four memories did it?**

Three illusions make that hard, and all three have burned us:

- **The model already knew.** Ask which timezone to schedule in, get
  `Asia/Ho_Chi_Minh`. That came from pre-training, not from the user's profile.
  Graded naively it is a memory success that measured nothing.
- **The prompt gave it away.** If the answer sits in the conversation history,
  the memory under test was never consulted at all.
- **A different memory answered.** A past task and a policy document can hold
  the same fact. Without switching one off, you cannot say which one delivered.

Section 4 is how all three are ruled out.

---

## 2. The four memories

| | `short_term` | `long_term` | `episodic` | `semantic` |
|---|---|---|---|---|
| **Analogy** | the meeting whiteboard | your employee settings card | the cabinet of signed receipts | the company policy manual |
| **Holds** | the last turns of *this* conversation | language, timezone, persona, tone | tasks the user asked for **and approved** | company policy documents |
| **Who may write** | the system, every turn | **only** an explicit user settings action | an explicit task request, then an approval | admin ingestion; read-only at runtime |
| **When it is read** | always, while the session is alive | always, injected into the system prompt | only when the question carries an episodic cue | only with the RAG flag **and** a policy cue |
| **Lifespan** | 20 turns, 30 minutes idle | 90 days | 90 days | as long as the documents live |

### `short_term` — the whiteboard

Notes from a meeting in progress. Someone says "move the deadline to Wednesday",
you wipe Tuesday and write Wednesday. Everyone leaves, the whiteboard is wiped.

```
Turn 1  "Tôi đang xử lý yêu cầu gia hạn CCCD cho văn phòng Đà Nẵng."
Turn 2  "Hạn chót của việc đó là thứ Ba."           (old deadline)
Turn 3  "Đính chính: hạn chót đã dời sang thứ Tư."  (correction)
Ask     "Hạn chót của yêu cầu gia hạn CCCD là khi nào?"
Want    "thứ Tư".  Answering "thứ Ba" is worse than not answering at all.
```

### `long_term` — the settings card

Durable preferences attached to the account across every session: language,
timezone, tone, and the assistant's persona nickname. The model **cannot write
here**. Saying "I feel like French today" does not rewrite the stored language.

```
Stored     { language: "vi", assistant_persona: "trợ lý biệt danh Hải Âu", tone: "ngắn gọn" }
Recall     "Tôi đã đặt bạn ở vai trò nào khi trả lời tôi?"  → "Hải Âu"
Restraint  "Chức danh của tôi là gì?"                       → must decline
           No job title was ever stored. Inventing one is the worst thing it can do.
```

### `episodic` — the signed receipts

A draft proposal on your desk is not company history. It becomes a filed record
only after you sign it. A newly created task is written
`retrieval_eligible=false` and is **unfindable until approved** — that gate is
part of what is being measured, not an obstacle to it.

```
Approved   task "Gia hạn CCCD cho văn phòng Đà Nẵng"
Recall     "Tác vụ trước về gia hạn CCCD là cho văn phòng nào?"  → "Đà Nẵng"
Restraint  "Số hồ sơ trên tác vụ trước ... là bao nhiêu?"        → must decline
           The task is real; no case number was ever recorded.
```

### `semantic` — the policy manual

The company handbook: overtime, travel, remote work. Shared by everyone, and
deliberately **not partitioned per tenant** — which is why no isolation question
may ever point at it (SPEC §5.2).

```
Indexed    "Đề nghị làm thêm giờ phải nộp qua biểu mẫu OT-114."
Recall     "Chính sách công ty ... qua biểu mẫu nào?"       → "OT-114"
Restraint  "Chính sách công ty nói gì về nghỉ sabbatical?"  → must decline
           There is no sabbatical policy. Improvising one from the leave rules is a failure.
```

---

## 3. Two rules that shape every result

**The model cannot promote its own guesses into permanent memory.**
`long_term` needs an explicit settings action; `episodic` needs a human
approval. So a run cannot fill those two by chatting, and a memory that only
works when the harness writes to it directly does not work.

**Retrieval is gated by Vietnamese cue phrases, and diacritics are
load-bearing.** `episodic` fires only on cues like *"tác vụ trước"*; `semantic`
needs both `CHAT_COMPANY_RAG_ENABLED=true` and a cue like *"chính sách công
ty"*. Matching is case-folded but **not** accent-folded, so `khong ro` never
meets `không rõ`. While these cue lists were English, four of eight questions
looked up nothing at all and reported a memory failure for it. Keep new probe
text accented.

---

## 4. Three arms — how we know which memory did the work

Every question is asked **three times**. One thing changes between them.

| Arm | What is different | What it proves | Should |
|---|---|---|---|
| `full` | nothing — all four filled and readable | the system as it ships | **pass** |
| `ablated` | the target memory cannot be read; everything else identical | that memory was **necessary** | **fail** |
| `control` | **nothing is filled**; all four reads stay on, over an empty store | the question **needed** memory | **fail** |

Right / wrong / wrong means the target memory is what produced the answer.
Nothing else changed, so nothing else can be credited.

> **The thing everyone gets backwards:** `control` empties the **store**, not
> the **reads**. If it switched reading off it would just be a second `ablated`
> arm, and a question the model can answer from its own training would sail
> through `full` and look like a memory success.

8 questions × 3 arms = 24 asks. Seeding roughly doubles it — budget about **52
model calls** per run.

---

## 5. Grades, and the verdicts they collapse into

Each reply gets one **grade**. Pass/fail is not enough: a system that says "I
don't know" is behaving correctly, while one that confidently returns last
month's answer is dangerous, and both are "fail" on a boolean.

| Grade | Meaning |
|---|---|
| `pass` | the expected answer is there, or an expected refusal landed |
| `miss` | the answer is absent — an honest gap |
| `stale` | a superseded value was asserted. Worse than forgetting. |
| `invented` | a refusal was required and a confident answer came instead |
| *(no answer)* | **not a grade.** Empty reply or provider outage. Says nothing about memory in either direction. |

The three grades for one question collapse into one **verdict**:

| `full` | `ablated` | `control` | Verdict |
|---|---|---|---|
| no answer anywhere | — | — | **`unreadable`** — checked first, overrides everything |
| `invented` / `stale` anywhere | — | — | **`dangerous`** — overrides everything below |
| pass | not pass | not pass | **`scope_earned_it`** |
| pass | pass | not pass | **`scope_did_nothing`** |
| any | any | pass | **`leaked`** |
| not pass | — | not pass | **`broken`** |

Rows are sorted **worst first** — `unreadable` → `dangerous` → `broken` →
`leaked` → `scope_did_nothing` → `scope_earned_it` — so the row that needs you
is never buried under a wall of passes.

`unreadable` sorts above `dangerous` because it is not a finding about the
product at all: the run failed for that question, and a failed run cannot
support any claim. Run it again.

### Four traps when reading a verdict

1. **Check `seed_failures` first.** A memory that could not be filled did not
   "find nothing" — it was never asked. A `broken` verdict on a memory listed
   there says nothing about memory.
2. **`broken` may be the lookup, not the store.** A record can be present and
   still not be found by a particular search (SPEC §7.5).
3. **`leaked` and `scope_did_nothing` usually accuse the *question*.** They mean
   the answer was reachable without the memory. Rewrite the question.
4. **A restraint question that behaves perfectly reports
   `scope_did_nothing`.** It declines on all three arms — the desired behaviour —
   and falls through to the second-worst label. Read it as "declined
   everywhere" (SPEC §15.1 item 9).

And `needs_reading` counts the rows the harness **will not decide on its own** —
refusals phrased in a way the phrase list does not cover. It publishes the doubt
rather than a conclusion it cannot defend. Open the reply in `runs/` and decide.

---

## 6. One question, end to end

`st_update_01`, targeting `short_term`. Expects *thứ Tư*; *thứ Ba* is the stale
answer.

| Arm | Setup | Reply | Grade |
|---|---|---|---|
| `full` | 3 turns seeded, read on | *"Thứ Tư — đã dời từ thứ Ba."* | **pass** |
| `ablated` | seeded, session history masked | *"Tôi không có thông tin về hạn chót đó."* | **miss** |
| `control` | nothing seeded, reads on | *"Tôi không có thông tin đó."* | **miss** |

→ **`scope_earned_it`**. Short-term memory produced that answer, and we know it
was short-term specifically, because it is the only thing that moved.

Note the `full` reply mentions Tuesday *as history* and is still `pass`. The
stale check only fires when the right answer is **absent** — grading it stale
would punish the most helpful phrasing available.

What each arm ruled out:

- If `control` had said *thứ Tư* → the model guessed. Not a memory question.
- If `ablated` had said *thứ Tư* → something else supplied it. Wrong target.
- If `full` had said *thứ Ba* → **stale**, and worse than forgetting.

---

## 7. What this does not measure

- **Cross-tenant isolation.** Seeding a second tenant is not wired. A question
  asking for material nobody seeded gets a refusal from an empty store — which
  would read as a passing tenancy check while proving nothing. Isolation is
  covered strictly, and offline, by the memory-policy unit tests (SPEC §5.2).
- **Conversation summaries.** "Episodic" here means **task records only**;
  `write_chat_summary` has no production caller.
- **Multi-step reasoning, token cost, and whether looking something up was the
  right call.** Each is a separate experiment.
- **Whether memory is good.** A completed run means the harness ran. A person
  reads the verdicts. This harness reports; it does not gate.

**One run is one sample.** Two runs at identical settings have disagreed on 2 of
8 questions — including the `control` arm, which has nothing in it, changing its
answer. Treat a difference between two reports as a hypothesis, never a finding.

---

## 8. Where to go next

| | |
|---|---|
| [README.md](./README.md) | the commands, and which store is under test |
| [RUNBOOK.md](./RUNBOOK.md) | the procedure, and how to triage a bad result |
| [SPEC-memory-evaluation.md](../../tasks/specs/SPEC-memory-evaluation.md) | the reason behind every decision above, and the list of known limits |
| `probes/v1-four-scopes.json` | the eight questions themselves |
