# Chat routing baselines

Committed, metadata-only reports from `scripts/evaluate_chat_routing.py`. The
labelled fixture set lives in `tests/fixtures/chat_routing/`; only case ids,
labels, routes, reason codes, model, prompt version and latency are persisted —
never messages, turn text, document titles or rendered prompts.

Naming: `chat-routing-eval-YYYY-MM-DD.json`. The script writes here by default,
so pass `--output-dir` for a run you do not want recorded.

```bash
uv run python scripts/evaluate_chat_routing.py --dry-run --output-dir <scratch>
```
