# Supabase Postgres + Qdrant Deployment

This runtime requires no Redis service or `REDIS_URL`.

## Required infrastructure

- Supabase Postgres with a server-side connection string in `DATABASE_URL`.
- A private Supabase Storage bucket named `project-documents`.
- Qdrant Cloud URL/API key for the `project_documents` collection.

## Required server environment

```dotenv
DATABASE_URL=postgresql://...
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_SECRET_KEY=<server-only-secret>
SUPABASE_STORAGE_BUCKET=project-documents
QDRANT_URL=https://<qdrant-cluster>
QDRANT_API_KEY=<server-only-key>
QDRANT_ENABLED=true
QDRANT_PROJECT_COLLECTION=project_documents
LLM_PROVIDER=gemini
GEMINI_API_KEY_1=<embedding-and-generation-key>
```

The normal Gmail OAuth and opaque session variables are still required. Never
send `DATABASE_URL`, Supabase secret keys, or Qdrant API keys to a browser.

## Start order

```powershell
py -3.11 -m pip install -e ".[dev,gui,postgres]"
mail-todo-api
# another terminal
mail-todo-worker
```

The API and worker both apply pending Postgres migrations at startup. The
worker polls `digest_runs` and `document_ingestion_jobs` once per second;
there is no message broker. Chat short-term turns are bounded in-process state
and are intentionally lost when the API process restarts.
