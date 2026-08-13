# Issue tracker: Linear

Issues and specs for this repo live in Linear, team **Heval1st** (issue prefix `HEV-`). Use the Linear MCP tools for all operations. Do not use `gh` for this repo's tickets.

Workspace: https://linear.app/heval1st  
Team id: `c848ea25-6bf8-40d9-998d-27ae11170ccd`

## Conventions

- **Create an issue**: `linear__save_issue` with `team: Heval1st`, `title`, and Markdown `description`. Do not pass `id` when creating.
- **Read an issue**: `linear__get_issue` (or `linear__list_issues` with `query` / identifier) using `HEV-<n>`. Include comments via `linear__list_comments` with `issueId: HEV-<n>`.
- **List issues**: `linear__list_issues` with `team: Heval1st` and optional `state`, `label`, `assignee`.
- **Comment**: `linear__save_comment` with `issueId: HEV-<n>` and Markdown `body`.
- **Apply / replace labels**: `linear__save_issue` with `id: HEV-<n>` and `labels: [...]`. This replaces the full label set — include every label that should remain.
- **Close**: `linear__save_issue` with `id: HEV-<n>` and `state: Done`. Cancel with `state: Canceled`.
- **Assign**: `linear__save_issue` with `id: HEV-<n>` and `assignee: me` (or a user name / email).

Default workflow states on this team: `Backlog`, `Todo`, `In Progress`, `Done`, `Canceled`, `Duplicate`. Type labels `Feature`, `Improvement`, and `Bug` are separate from triage labels.

## Pull requests as a triage surface

**PRs as a request surface: no.**

GitHub PRs for `MathematicGuy/EMAIL-AGENT-v1` are implementation, not incoming feature requests. Do not run `/triage` against them unless this flag is flipped later.

## When a skill says "publish to the issue tracker"

Create a Linear issue on team **Heval1st**.

## When a skill says "fetch the relevant ticket"

Load `HEV-<n>` with `linear__get_issue` / `linear__list_issues`, then `linear__list_comments`.

## Wayfinding operations

Used by `/wayfinder`. The **map** is a single parent issue; **child** issues are tickets.

- **Map**: one issue labelled `wayfinder:map`, holding Notes / Decisions-so-far / Fog in the description. Create with `linear__save_issue` (`team: Heval1st`, `labels: ["wayfinder:map"]`).
- **Child ticket**: `linear__save_issue` with `parentId` set to the map identifier (`HEV-<n>`). Labels: `wayfinder:<type>` (`research` / `prototype` / `grilling` / `task`). Once claimed, assign the ticket to the driving dev.
- **Blocking**: Linear native relations. Set `blockedBy: ["HEV-<n>"]` on the child and/or `blocks: ["HEV-<n>"]` on the blocker. A ticket is unblocked when every blocker is `Done` or `Canceled`.
- **Frontier query**: list the map's open children (`linear__list_issues` with `parentId` of the map, excluding `Done`/`Canceled`), drop any with an open blocker or an assignee; first in map order wins.
- **Claim**: `linear__save_issue` with `id: HEV-<n>` and `assignee: me` — the session's first write.
- **Resolve**: `linear__save_comment` with the answer, then `state: Done`, then append a context pointer to the map's Decisions-so-far.
