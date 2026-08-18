"""Load runtime configuration before Langfuse-decorated providers import."""

from cowork_agent.config import load_runtime_environment

# ``@observe`` binds the process-wide Langfuse client when provider modules are
# imported. Keep this bootstrap import-only so app and worker entry points load
# `.env` before importing those providers, while unit tests can still import a
# provider without an implicit network-enabled configuration.
load_runtime_environment()
