# Windows Psycopg Event Loop Design

## Goal

Start the FastAPI server on Windows with a Supabase PostgreSQL URL in `.env`
without psycopg's `ProactorEventLoop` incompatibility.

## Design

`cowork_agent.app.main()` will load `.env` before deciding uvicorn's loop
factory. When `DATABASE_URL` is configured on Windows, it will retain the
existing `asyncio:SelectorEventLoop` factory; all other platforms and local
SQLite mode keep uvicorn's `auto` loop. The worker already selects the
Windows selector policy before creating its async pool and is out of scope.

## Error Handling and Verification

The change does not hide database connection errors: startup will continue to
fail with the actual database error if the URL or credentials are invalid. A
unit test will construct a temporary `.env`, invoke `main()` with uvicorn
stubbed at the process boundary, and verify the Windows PostgreSQL path passes
the selector loop to uvicorn.
