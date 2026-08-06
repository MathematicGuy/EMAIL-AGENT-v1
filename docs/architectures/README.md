# Architecture Extraction Workflow

The files in this directory are prompt templates used to extract, compare, and redesign the current system architecture.

Generated architecture documents must be saved under:

`docs/architectures/current-architectures/`

## Prompt files

- `extract-email.md`
- `extract-rag.md`
- `extract-overall-architecture.md`
- `master-comparison-architecture.md`

## Best workflow

Use the prompts in this order:

1. Run `extract-email.md` to analyze the current Gmail or Email module.
2. Save the result to:

   `docs/architectures/current-architectures/current-email-architecture.md`

3. Run `extract-rag.md` to analyze the current RAG module.
4. Save the result to:

   `docs/architectures/current-architectures/current-rag-architecture.md`

5. Run `extract-overall-architecture.md` to analyze the current overall system.
6. Save the result to:

   `docs/architectures/current-architectures/current-overall-architecture.md`

7. Review and correct the extracted descriptions and Mermaid diagrams.

8. Optionally record corrections or unresolved questions in:

   `docs/architectures/current-architectures/current-architecture-review.md`

9. Provide the three reviewed architecture documents to `master-comparison-architecture.md`.

10. Use the master comparison prompt to:

    - compare the current architecture with the target architecture;
    - identify reusable components;
    - identify missing or conflicting responsibilities;
    - simplify the system before adding new components;
    - recommend only the changes that are necessary.

## Expected generated package

The generated package should contain:

```text
docs/architectures/current-architectures/
├── current-email-architecture.md
├── current-rag-architecture.md
├── current-overall-architecture.md
└── current-architecture-review.md
```

## Most important extraction question

Where does the final Action Plan generation currently happen: inside the RAG module, inside the Email workflow, or in a separate Agent or LLM service?

This answer determines whether the target architecture requires:

a small interface adjustment;
a change in orchestration ownership; or
a larger separation between retrieval and generation responsibilities.

## One important improvement to the master workflow

The three generated files should be **reviewed before** they are supplied to the master comparison prompt.

Otherwise, a mistaken extraction may be treated as the real architecture and influence every later recommendation.

The correct boundary is:

```text
Source code
→ extracted architecture
→ human review and correction
→ master comparison
→ target architecture
```

Your existing four large prompts do not need to be rewritten. Only their output-path instructions should eventually point to the corresponding files under: 
docs/architectures/current-architectures/