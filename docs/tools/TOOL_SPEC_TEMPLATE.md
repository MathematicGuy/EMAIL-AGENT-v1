# [Tool Name] Standard Specification

| Field | Value |
|---|---|
| Tool Name | [Tool Name] |
| Category | [Observability / Vector DB / LLM Provider / OAuth] |
| Target Layer | [src/cowork_agent/integrations/...] |
| Specification Status | Approved Standard |

---

## 1. Purpose & Scope
- **Primary Utility:** [What does this tool do?]
- **In-Scope:** [Where MUST this tool be used?]
- **Out-of-Scope:** [Where MUST this tool NOT be used?]

## 2. Architecture Boundary & Dependency Rules
- **Permitted Imports:** `src/cowork_agent/integrations/...`, `src/cowork_agent/features/...`
- **Forbidden Imports:** `src/cowork_agent/domain/...`

## 3. Environment Variables (.env)
| Variable Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `TOOL_API_KEY` | Secret | Yes | None | API Key for accessing the service |

## 4. Golden Code Snippet (Mẫu Code Chuẩn)
```python
# Standard syntax pattern for Humans and AI Agents
```

## 5. Failure Handling & Fallback Policy
- Failure behavior details.

## 6. Verification Commands
```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/... -q
```

## 7. Anti-Patterns & Privacy Rules
- ❌ Do NOT log sensitive user PII or auth credentials.
- ❌ Do NOT invoke blocking sync calls on main loops.
