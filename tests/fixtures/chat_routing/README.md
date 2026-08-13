# AI Chat Routing Fixture Set (V3-M4)

Synthetic labeled cases for the user-document intent classifier. The 60 cases
are evenly split across `obvious_rag`, `obvious_chat`, `ambiguous`, and
`distractor`. They contain no real user data or document content.

The evaluation report may persist case IDs, labels, routes, reason codes, model,
prompt version, and latency. It must never persist current messages, recent-turn
text, document titles, or rendered prompts.
