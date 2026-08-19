# Email Intent Annotation Rubric

Rubric version: `email-intent-annotation-v1`.

## Actionability

- `action_required`: the email explicitly obligates or directly asks the user
  to act.
- `action_suggested`: action could benefit the user, but it is optional.
- `informational`: useful information with no requested or necessary action.
- `irrelevant`: unrelated, promotional, noisy, or not useful enough to create
  an action.
- `unclear`: the intent or required action cannot be determined confidently
  from the email.

## Sufficiency and expected route

- Informational or irrelevant -> `no_action`.
- Actionable and fully executable from the email alone -> `direct_plan`.
- Actionable but dependent on missing company knowledge -> `retrieve_rag`.
- Unclear -> `retrieve_rag`.
- Required policy, procedure, governance document, guideline, template,
  product documentation, or unresolved internal term -> `retrieve_rag`.
- Informational and irrelevant cases use `email_is_sufficient: true`; no
  additional knowledge is needed to make the no-action decision.

## Expected document types

Allowed expected document types are exactly:

```text
company_policy
governance_document
procedure
guideline
template
product_documentation
```

Use an empty array when no company document is required.

`retrieval_query` is not a ground-truth field because multiple query phrasings
can be equally valid.
