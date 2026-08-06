You are analyzing an existing Gmail integration module for an AI Agent system.

Your goal is to extract its current architecture without redesigning it.

Analyze all provided code and documentation and produce:

## 1. Module purpose

Explain what the Gmail module currently does.

## 2. Entry points

Identify:

- scheduled jobs
- API endpoints
- commands
- event handlers
- cron triggers
- background workers

## 3. Authentication

Identify:

- OAuth flow
- token storage
- token refresh
- scopes
- tenant and user ownership

## 4. Gmail retrieval flow

Trace the full path:

Trigger
→ Authentication
→ Gmail API
→ Message selection
→ Pagination
→ Fetch
→ Parsing
→ Normalization
→ Returned payload

## 5. Returned data contract

Document every field returned to the caller.

Examples:

- Gmail message ID
- thread ID
- Gmail URL
- sender
- recipients
- subject
- body
- date
- labels
- attachment metadata
- fetch status

## 6. Persistence

Identify whether any of these are stored:

- raw email
- normalized email
- Gmail message ID
- thread ID
- sender
- generated output
- errors
- traces

For each persisted field, identify:

- database or file
- table or collection
- retention
- purpose

## 7. Reliability

Identify:

- timeout handling
- retries
- exponential backoff
- rate-limit handling
- token refresh
- partial batch behavior
- failed-message handling
- dead-letter behavior

## 8. Observability

Identify:

- logs
- traces
- metrics
- alerts
- whether full email content appears in logs

## 9. State ownership

State which component owns:

- OAuth credentials
- message content
- normalized email
- run state
- output data

## 10. Mermaid diagram

Generate one production-focused Mermaid flowchart.

Requirements:

- use `flowchart LR`
- create bounded subgraphs:
  - CALLER
  - EMAIL MODULE
  - GOOGLE
  - STORAGE
  - RELIABILITY
  - OBSERVABILITY
- show APIs, services, databases and retries
- show where raw email enters or leaves persistence
- use solid arrows for normal flow
- use dotted arrows for errors and observability

## 11. Unknowns

List anything that cannot be confirmed from the source.

Do not redesign the module.
Do not assume missing components exist.