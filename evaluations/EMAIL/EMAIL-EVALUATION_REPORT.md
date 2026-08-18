# Email Intent Routing Evaluation Report

> **Evaluation target**: Current production Email Intent Router
> **Dataset**: 50 Gmail candidate messages from `gmail_candidates.json`
> **Golden artifact**: `golden_dataset.json`
> **Provider/model**: `openrouter` / `deepseek/deepseek-v4-flash`
> **Run date**: 2026-08-18

## Executive Summary

The current production classifier prompt was evaluated once over 50 messages.
Predictions are compared only with human-reviewed ground truth.

### Coverage and accuracy

- Predictions persisted: **50/50** — All 50 email evaluation results were successfully saved. No prediction records were lost or missing.

- Model predictions: **45/50** — The AI model directly produced predictions for 45 of the 50 emails.

- Classifier fallbacks: **5/50** — For 5 emails, the normal model prediction was unavailable or invalid, so the system used its fallback classifier instead.

- Reviewed route accuracy: **24/31 (77.4%)** — Among the 31 emails reviewed by a human, the system assigned the correct route for 24. This means its route decisions were correct 77.4% of the time.

- Reviewed actionability accuracy: **11/31 (35.5%)** — Among the 31 human-reviewed emails, the system correctly identified whether an email required, suggested, or did not require an action for 11 cases. This represents an accuracy of 35.5%.

⚠️ **5/50 decisions used classifier fallback.** Those cases are persisted for
audit but are not model-quality evidence.

### Resolved route distribution

| Route | Count | Share |
|---|---:|---:|
| NO_ACTION | 36 | 72.0% |
| DIRECT_PLAN | 3 | 6.0% |
| RETRIEVE_RAG | 11 | 22.0% |

### Actionability distribution

| Actionability | Meaning | Count | Share |
|---|---|---:|---:|
| `action_required` | The email explicitly requires the user to do something. | 7 | 14.0% |
| `action_suggested` | An action may be useful, but it is optional rather than required. | 2 | 4.0% |
| `informational` | The email provides information and does not request an action. | 31 | 62.0% |
| `irrelevant` | The email is not relevant enough to create an action or plan. | 5 | 10.0% |
| `unclear` | The email's intent or required action cannot be determined confidently. | 5 | 10.0% |

## Methodology and limitations

1. Each Gmail snippet was converted into the temporary email format used by the project.
2. The email classifier used the current production prompt.
3. The Route Resolver used the classifier's results to choose the final route.
4. Accuracy was measured using only the 31 emails that had been reviewed by a person.
5. The evaluation files do not include the original email snippets, and telemetry export was turned off.

## Reproduce

```powershell
uv run python scripts/evaluate_email_golden.py --limit 50
```

Artifacts: `gmail_candidates.json`, `golden_dataset.json`, and this report.
