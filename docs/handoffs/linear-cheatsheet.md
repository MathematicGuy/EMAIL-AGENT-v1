# Linear learning sandbox cheat-sheet

Last setup attempt: 2026-08-15

## Safety boundary

- Linear is a learning sandbox only.
- GitHub Issues in `MathematicGuy/EMAIL-AGENT-v1` and
  `tasks/ISSUES-qdrant-retirement.md` remain the source of truth for real work.
- Do not mirror, edit, or close GitHub issues `#4`–`#11` from Linear.
- Do not enable GitHub or Slack synchronization for this sandbox.

## Verified workspace baseline

- Workspace: **Heval1st** — <https://linear.app/heval1st>
- Team: **Heval1st** (`HEV` prefix)
- Existing issues: `HEV-1` through `HEV-4`, Linear's stock onboarding issues.
  Leave these unchanged.
- Workflow states: Backlog, Todo, In Progress, In Review, Done, Canceled,
  Duplicate.
- Type labels: Bug, Feature, Improvement.
- Triage labels: `needs-triage`, `needs-info`, `ready-for-agent`,
  `ready-for-human`.

## Recommended disposable demo

Create a project named **Linear Demo** (not `EMAIL-AGENT-v1`) and populate it
with fake issues that demonstrate:

1. The lifecycle Backlog → Todo → In Progress → In Review → Done.
2. Assignment to yourself and priorities 0–4 (No priority, Urgent, High,
   Medium, Low).
3. A parent issue with two sub-issues.
4. One fully specified `ready-for-agent` issue, one `needs-info` issue, and one
   `ready-for-human` issue.
5. A milestone, a cycle if enabled, and a Linear Document.

Suggested fake titles:

- Demo: triage an incoming request (`needs-triage`, Backlog)
- Demo: clarify acceptance criteria (`needs-info`, Todo)
- Demo: implement a disposable automation (`ready-for-agent`, In Progress)
- Demo: review the automation output (`ready-for-human`, In Review)
- Demo: completed walkthrough (Done)

## Concepts and GitHub equivalents

| Linear concept | Important fields | GitHub Issues equivalent |
| --- | --- | --- |
| Team | name, key/prefix, workflow | Repository or organization team |
| Project | name, summary, lead, dates | Milestone or project board |
| Issue | title, description, state, assignee, priority | Issue |
| Sub-issue | `parentId` | Task list / linked child issue |
| Status | state | Status field or open/closed state |
| Label | label names | Labels |
| Priority | 0–4 | Priority field or priority label |
| Cycle | repeating timebox | Sprint/iteration |
| Milestone | project checkpoint | Milestone (closest match) |
| Document | project/team knowledge | Repository Markdown, wiki, or discussion |
| Comment | issue update | Issue comment |
| Blocking relation | `blockedBy` / `blocks` | Issue dependency link |

Projects group work toward an outcome; cycles are repeating delivery windows;
milestones are checkpoints inside a project.

## Connector operation map

| Goal | Linear operation | Fields that matter |
| --- | --- | --- |
| Discover teams | `list_teams` | name, id, key |
| Discover projects | `list_projects` | team, status |
| Create/update project | `save_project` | name, team, summary, dates |
| Search/filter issues | `list_issues` | team, query, label, state, assignee |
| Read one issue | `get_issue` | identifier such as `HEV-5` |
| Create/update issue | `save_issue` | team, title, description, state, assignee, priority, labels, parentId |
| Comment | `save_comment` | issueId, Markdown body |
| Inspect workflow | `list_issue_statuses` | team |
| Inspect labels | `list_issue_labels` | team |
| Add a label | `create_issue_label` | name, team, color, description |
| Inspect cycles | `list_cycles` | team |
| Create/update milestone | `save_milestone` | project, name, target date |
| Create/update document | `save_document` | title, content, project |

When updating labels, send the complete desired label set because the update
replaces existing labels. Use real newline characters in Markdown descriptions
and comments.

## Useful searches

- `assignee: me` — your assigned issues.
- `label: ready-for-agent` — sufficiently specified work an agent can claim.
- `label: needs-info` — work blocked on clarification.
- `label: ready-for-human` — work awaiting human judgment or execution.
- `state: In Progress` — active work.
- A text `query` — title/description search.

## Triage flow

1. New work starts with `needs-triage`.
2. If requirements are incomplete, replace it with `needs-info` and state the
   exact question in the issue.
3. If the work is fully specified and safe for an agent, use
   `ready-for-agent`.
4. If a human decision or action is required, use `ready-for-human`.
5. Move through the workflow as work advances; close as Done or Canceled with
   a short explanatory comment.

## Live sandbox created

Created on 2026-08-15 after the Linear browser session was authenticated:

- Project: **Linear Demo**
- Lifecycle issues: `HEV-5` through `HEV-9`
- Sub-issues under `HEV-5`: `HEV-10` and `HEV-11`
- Milestone: **Sandbox walkthrough complete**
- Document: **How to use the Linear Demo sandbox**

The workspace currently exposes **Loops**, which are agent automations, but no
Cycles interface was available. No cycle was created. GitHub import and sync
remain disabled, and `HEV-1` through `HEV-4` were left unchanged.
