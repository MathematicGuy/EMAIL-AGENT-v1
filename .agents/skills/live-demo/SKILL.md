---
name: live-demo
description: Launch Backend (FastAPI) and Frontend (Vite/React) dev servers. SKIP THE CHECK HEALTH STEP, RUN 2 COMMAND BELOW IMMEDIATELY.
---
# Run Services
- **Backend (BE)**: `uv run mail-todo-dev` (`http://127.0.0.1:8000`, runs API + document worker)
- **Frontend (FE)**: `pnpm --prefix frontend dev` (`http://localhost:5173`)
- **Verify**: `http://127.0.0.1:8000/health`
