# Phase 3: Hexagonal Architecture (Ports & Adapters)

## Overview & Architecture Diagram

```mermaid
graph TD
    subgraph Core ["Core Application & Domain Layer (Pure Python)"]
        D["Domain Models\n(domain/models.py)"]
        W["Workflow & Use Cases\n(features/email_action_plan/workflow.py)"]
        
        subgraph Ports ["Abstract Protocols / Ports (features/email_action_plan/ports.py)"]
            P1["MailboxPort Protocol"]
            P2["ActionGeneratorPort Protocol"]
            P3["RunRepository Protocol"]
            P4["CompletionOutboxPort Protocol"]
        end
    end

    subgraph Adapters ["Infrastructure & Integration Layer (Adapters)"]
        A1["Gmail OAuth Adapter\n(integrations/gmail/)"]
        A2["Gemini LLM Provider\n(integrations/llm/)"]
        A3["Groq LLaMA Provider\n(integrations/llm/)"]
        A4["SQLite / Postgres Repo\n(persistence/)"]
        A5["Deterministic Fake Mailbox & LLM\n(Used in Unit Tests)"]
    end

    W --> D
    W --> Ports
    A1 -->|Implements| P1
    A2 -->|Implements| P2
    A3 -->|Implements| P2
    A4 -->|Implements| P3
    A4 -->|Implements| P4
    A5 -->|Implements| P1
    A5 -->|Implements| P2
```

## Key Architectural Takeaways

1. **Dependency Inversion Principle (DIP)**:
   High-level workflow logic ([workflow.py](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/features/email_action_plan/workflow.py)) depends only on abstract interfaces ([ports.py](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/features/email_action_plan/ports.py)), never on concrete external SDKs (like Google API client or Groq SDK).

2. **Ports using Python Protocols**:
   In Python, `typing.Protocol` is used to define compile-time and runtime duck-typing contracts for ports (e.g. `MailboxPort`, `RunRepository`).

3. **Seamless Provider Swapping & Testability**:
   Because LLMs and Mailboxes are hidden behind Ports, unit tests run against `FakeMailbox` and `FakeLLM` without calling external APIs or burning token credits.
