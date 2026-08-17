# Handoff — Linear issue tracking setup (learning sandbox)

**Status:** secondary work. Runs in parallel with
[HANDOFF-adr-008-turbovec-migration.md](HANDOFF-adr-008-turbovec-migration.md).

**Your job:** set up Linear so the user can learn it. **Not** to migrate the project's real issue
tracking onto it.

---

## Hard boundary — read before anything else

The user's decision, verbatim: *"Keep all Issues local and on github for now. I just want to
check what I could do with Linear."*

- **Source of truth for real work is GitHub Issues** on `MathematicGuy/EMAIL-AGENT-v1`
  (currently `#4`–`#11`) plus `tasks/ISSUES-qdrant-retirement.md` as the in-repo index.
- **Do not** close, edit, or mirror those GitHub issues into Linear.
- **Do not** edit any file under `src/`, `tests/`, `scripts/`, or `tasks/`. If a Linear
  experiment seems to need a repo change, stop and ask.
- The only repo path you may write is `docs/handoffs/` and any new doc you're explicitly asked
  for.

This keeps you non-conflicting with the main agent, which is rewriting the RAG layer.

## Current Linear state (verified 2026-08-14)

| Thing | Value |
| :--- | :--- |
| Workspace | `Heval1st` — https://linear.app/heval1st |
| Teams | one: `Heval1st`, issue prefix `HEV` |
| Projects | none |
| Issues | 4, all Linear's stock onboarding: `HEV-1` Get familiar with Linear, `HEV-2` Connect your tools, `HEV-3` Import your data, `HEV-4` Set up your teams — all `Todo` |
| Statuses | Backlog, Todo, In Progress, In Review, Done, Canceled, Duplicate |
| Labels | `Bug`, `Feature`, `Improvement`, `ready-for-agent`, `ready-for-human`, `needs-triage`, `needs-info` |

The `ready-for-agent` / `ready-for-human` / `needs-triage` / `needs-info` set is Linear's
agent-session workflow. The user set it up deliberately — build the demo around it, it's the
most interesting thing in the workspace.

## Access

Linear MCP tools are available through the `plugin:linear:linear` server and already
authenticated. Tool schemas are deferred — load them with
`ToolSearch("select:mcp__plugin_linear_linear__<name>")` before calling. The ones you'll want:
`list_teams`, `list_projects`, `save_project`, `save_issue`, `list_issues`, `get_issue`,
`list_issue_statuses`, `list_issue_labels`, `create_issue_label`, `save_comment`,
`list_cycles`, `save_milestone`, `save_document`.

Note the server's own instruction: pass real newlines in markdown strings, not literal `\n`.

## What to build

A throwaway sandbox that demonstrates each Linear concept on fake data, so the user can see the
mechanics without polluting real tracking.

1. **A Linear Project** named something obviously disposable — `Sandbox` or `Linear Demo`. Not
   `EMAIL-AGENT-v1`; that name implies real tracking and invites confusion later.
2. **Issues covering the lifecycle**: create → assign → move Backlog → Todo → In Progress → In
   Review → Done. Use throwaway titles. Show sub-issues via `parentId`, and priorities 0–4.
3. **The agent workflow**: one issue labelled `ready-for-agent` with a fully specified
   description, one labelled `needs-info` and one `ready-for-human`, so the user can see how
   triage labels gate delegation.
4. **A milestone and a cycle**, if the workspace has cycles enabled — show how they differ from
   projects.
5. **A Linear Document** (`save_document`) — most people don't know Linear has these.
6. **Search**: demonstrate `list_issues` filtering by `query`, `label`, `state`, `assignee=me`.

Leave `HEV-1`…`HEV-4` alone unless the user asks — they're Linear's own tutorial content.

## Deliverable

A short markdown cheat-sheet the user can keep: which MCP tool does what, which fields matter,
and what the GitHub-Issues equivalent is for each Linear concept. They're learning Linear
*against* a GitHub baseline they already understand, so the mapping is the valuable part.

Write it to `docs/handoffs/linear-cheatsheet.md`. Nowhere else.

## Escalate, don't guess

- If the user asks to move real issues to Linear, that reverses the decision above. Confirm
  explicitly before touching `#4`–`#11` or `tasks/ISSUES-qdrant-retirement.md`.
- If a Linear action would be visible outside the workspace (Slack, GitHub sync integration),
  confirm first. Enabling the GitHub sync integration in particular would start mirroring the
  real issues — exactly what the user said not to do.
