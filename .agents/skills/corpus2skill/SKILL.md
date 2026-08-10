---
name: corpus2skill
description: Navigates and retrieves information from the Company-knowledge-RAG codebase, architecture specs, SOTA RAG handbook, and evaluation benchmarks using a hierarchical Skill Tree.
---

# Corpus2Skill Knowledge Navigation Skill (Company-knowledge-RAG)

## Purpose

This skill enables an AI agent to navigate and retrieve information from the entire **Company-knowledge-RAG** project workspace using a hierarchical Skill Tree ([`SKILL_TREE.md`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/.agents/skills/corpus2skill/SKILL_TREE.md)).

---

## Master Branches

The project knowledge base is structured into 5 master branches in [`SKILL_TREE.md`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/.agents/skills/corpus2skill/SKILL_TREE.md):

1. **Architecture & System Overview** ([`RAG-ARCHITECTURE.md`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/RAG-ARCHITECTURE.md), [`README.md`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/README.md))
2. **SOTA RAG Knowledge Base** ([`RAG-KNOWLEDGE-SUMMARY.md`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/RAG-KNOWLEDGE-SUMMARY.md), [`docs/`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/docs/))
3. **Core Pipeline Code** ([`src/ingestion/`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/src/ingestion/), [`src/retrieval/`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/src/retrieval/), [`src/generation/`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/src/generation/), [`src/api/`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/src/api/))
4. **Evaluation & Golden Set** ([`evaluation/`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/evaluation/), [`src/evaluation/`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/src/evaluation/))
5. **MLOps, Deploy & Observability** ([`Dockerfile`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/Dockerfile), [`docker-compose.yml`](file:///D:/User/ProjectGithub/hiepnguyenn-99/Company-knowledge-RAG/docker-compose.yml), Langfuse)

---

## Agent Workflow

```
User Query -> Match Node in SKILL_TREE.md -> Read Specific Target File -> Answer with Grounded Evidence + Clickable Links
```
