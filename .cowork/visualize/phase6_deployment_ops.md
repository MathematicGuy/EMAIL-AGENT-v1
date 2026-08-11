# Phase 6: Deployment, APIs & System Evolution

## Modular Monolith to Scaled Architecture Diagram

```mermaid
flowchart TD
    subgraph MVP ["Local MVP Architecture (Current EMAIL-AGENT-v1)"]
        GUI["Streamlit GUI (gui/app.py)"]
        API["FastAPI App Root (app.py)"]
        Worker["In-Process Worker (orchestration/local.py)"]
        DB1[("SQLite Database\n(persistence/)")]
        Mem1["Local Hybrid Semantic Memory"]
        
        GUI --> API
        API --> Worker
        Worker --> DB1
        Worker --> Mem1
    end

    subgraph Scale ["Production Target Architecture (docs/architectures/)"]
        WebUI["Production React / Next.js Web Client"]
        Gateway["API Gateway / FastAPI Service"]
        Queue["Durable Task Queue\n(Redis / RabbitMQ / Celery)"]
        WorkerPool["Distributed Worker Nodes"]
        DB2[("PostgreSQL Source of Truth")]
        VectorDB[("Qdrant / Cloud Vector DB")]
        
        WebUI --> Gateway
        Gateway --> Queue
        Queue --> WorkerPool
        WorkerPool --> DB2
        WorkerPool --> VectorDB
    end

    MVP -.->|Evolutionary Scaling Path| Scale
```

## Key Architectural Principles

1. **Start as a Modular Monolith**: Begin with a clean, single-process app (FastAPI + SQLite + In-Process Worker). Don't over-engineer microservices on Day 1!
2. **Clean Boundaries Enable Easy Scaling**: Because Phase 3 used Ports & Adapters, migrating from SQLite to PostgreSQL or from In-Process Worker to Celery/Redis requires swapping adapters in `persistence/` without rewriting core domain workflows.
3. **Observability & Outbox Pattern**: Production AI jobs track status (`QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`) and publish events via a Transactional Outbox to handle failures gracefully.
