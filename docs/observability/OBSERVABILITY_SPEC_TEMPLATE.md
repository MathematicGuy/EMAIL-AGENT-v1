# [Observability Component Name] Standard Specification

| Field | Value |
|---|---|
| Component Name | [Component / Provider Name] |
| Telemetry Domain | [AI Tracing / Infrastructure Logging / LLM Cost Tracking / Metrics] |
| Target Layer | [src/cowork_agent/integrations/..., src/cowork_agent/api/...] |
| Specification Status | Approved Standard |

---

## 1. Purpose & Scope
- **Primary Utility:** [What telemetry capability does this component provide?]
- **In-Scope:** [Where MUST this component be configured/used?]
- **Out-of-Scope:** [Where MUST this component NOT be used?]

## 2. Architecture Boundary & Dependency Rules
- **Permitted Imports:** `src/cowork_agent/integrations/...`, `src/cowork_agent/features/...`, `src/cowork_agent/api/...`
- **Forbidden Imports:** `src/cowork_agent/domain/...`

## 3. Environment Variables (.env)
| Variable Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `COMPONENT_API_KEY` | Secret | Yes | None | API Key / Secret for telemetry export |

## 4. Golden Code Snippet (Mẫu Code Chuẩn)
```python
# Standard telemetry pattern for Humans and AI Agents
```

## 5. Failure Handling & Fallback Policy
- Fallback / Silent mode execution rules to ensure non-blocking telemetry.

## 6. Verification Commands
```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/... -q
```

## 7. Anti-Patterns & Privacy Rules
- ❌ Do NOT log sensitive user PII or auth credentials.
- ❌ Do NOT make blocking sync calls on main loops.
