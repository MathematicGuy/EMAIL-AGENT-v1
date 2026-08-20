# Golden answer contract

Rules for authoring reference answers for `v2_four_scopes_wide`. Every scope
prompt in this directory inherits this file. Read it before the scope prompt.

## What you are producing

One **reference answer per probe per arm** — the answer the product *should*
give — so that a judge can later grade a real run's reply against it instead of
matching phrases.

You are not running the evaluation. You are not calling the product. You are
reading two files and writing down what a correct answer contains.

## Read these two, and only these

- `evaluations/MEMORIES/probes/v2-four-scopes-wide.json` — the seed and the 20
  probes. Your scope prompt tells you which probe ids are yours.
- `tests/fixtures/memory_eval/corpus-v2/` — the five company documents that
  `semantic` reads.

Do not read `runs/`, `baselines/`, or any model output. A golden answer is a
specification written *before* a run, not a summary of one.

## Rule 1 — the seed is the ground truth; cite it or do not answer

Every correct answer to a v2 probe is derivable **by reading** the seed and the
corpus. Nothing here requires judgement about the world.

So every answer you write carries the exact seed line or corpus line it came
from, in a `grounding` field. Content traceable to neither is forbidden, however
plausible it looks.

If you cannot ground an answer, **do not write one.** Add the probe to
`defects` with what is missing, and move on. A golden file full of confident
guesses would make a judge measure agreement between two models, which is worth
less than nothing because it looks like a measurement.

## Rule 2 — three arms, not one answer

Each probe is asked three times under three conditions, and the correct answer
is **not the same in all three**:

| arm | what is different | reference answer |
|---|---|---|
| `full` | everything filled and readable | the content answer |
| `ablated` | the probe's own scope cannot be read | **a decline** |
| `control` | memory was never filled at all | **a decline** |

Two cases override that table:

- **`restraint` probes decline on all three arms.** The fact was never stored,
  so no arm can produce it. That is the probe's whole point.
- **A probe you can answer from the question itself** — without any seed line —
  is broken (it would pass with memory switched off, and prove nothing). Do not
  write a content answer for `control`. Report it in `defects`.

## Rule 3 — write Vietnamese, accented

The product answers in Vietnamese always. Comparison in this harness uses
`casefold()`, which folds capitals but **does not strip accents**, so `khong ro`
is a different string from `không rõ`. Write full diacritics.

Keep answers short and plain, in the register the seeded `response_tone`
("ngắn gọn") asks for. A reference answer is the *content* a correct reply must
carry, not a style sample.

## Rule 4 — a decline is a reference too

For every arm whose reference is a decline, write the decline you would expect,
and list in `refusal_markers` the Vietnamese phrases that mark it as one.
Include a phrasing that names **the thing asked about** ("tôi không có số điện
thoại của bạn"), not only ones naming a kind of knowledge ("không có thông
tin"). The existing scorer's phrase list misses the first shape and grades it as
invention; the golden data should not repeat that gap.

## Output

Write exactly one file: `evaluations/MEMORIES/golden/parts/<scope>.json`.

```json
{
  "probe_set_id": "v2_four_scopes_wide",
  "scope": "short_term",
  "answers": [
    {
      "probe": "st_recall_01",
      "test": "recall",
      "arms": {
        "full": {
          "reference": "Yêu cầu gia hạn CCCD đó là cho văn phòng Đà Nẵng.",
          "must_contain": ["Đà Nẵng"],
          "grounding": "seed.short_term[0]: \"Tôi đang xử lý yêu cầu gia hạn CCCD cho văn phòng Đà Nẵng.\""
        },
        "ablated": {
          "reference": "Tôi không có thông tin về văn phòng của yêu cầu đó.",
          "refusal_markers": ["không có thông tin", "không rõ văn phòng"],
          "grounding": "short_term is masked on this arm; nothing else holds the office"
        },
        "control": {
          "reference": "Tôi không có thông tin về yêu cầu gia hạn nào.",
          "refusal_markers": ["không có thông tin"],
          "grounding": "memory is never seeded on this arm"
        }
      }
    }
  ],
  "defects": []
}
```

`must_contain` is the minimum a correct reply must carry — the fact itself, not
a phrasing of it. Put one entry per accepted spelling only where the seed
genuinely offers more than one.

A `defects` entry is `{"probe": "<id>", "problem": "<what is missing or wrong>"}`.
An empty list is a real answer; do not invent defects to fill it.

## Do not

- Do not edit the probe file, the corpus, or anything under `src/`.
- Do not add, remove or reword a probe. If a question looks wrong, that is a
  `defects` entry.
- Do not run the evaluation, call a model, or touch the network.
- Do not write any file other than your one part file.
