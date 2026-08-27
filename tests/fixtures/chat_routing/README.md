# AI Chat Routing Fixture Set (V3-M4)

Synthetic labeled cases for the user-document intent classifier. The 64 cases
are evenly split across `obvious_rag`, `obvious_chat`, `ambiguous`, and
`distractor`. They contain no real user data or document content.

`cr-061` to `cr-064` are the `SPEC-chat-tools-registry` §11 block: two cases
that expect the `create_calendar_event` tool, a calendar-mention distractor
that must *not* route to it, and a retrieval case to keep the groups balanced.
Scoring them requires `scripts/evaluate_chat_routing.py` to route with the tool
axis on — see [`../tool_intent/README.md`](../tool_intent/README.md) for why the
evaluator and its gate had to change first.

The evaluation report may persist case IDs, labels, routes, reason codes, model,
prompt version, and latency. It must never persist current messages, recent-turn
text, document titles, or rendered prompts.
