# Code discovery

Use **codebase-memory** for structural exploration (`list_projects`,
`search_code` / `search_graph`, `get_code_snippet`, `trace_path`,
`query_graph`, `get_architecture`, `check_index_coverage`).

Do not use Serena. Do not call `serena__*` tools or re-enable that MCP
server unless the user explicitly asks.

Literal or non-code text, or files the graph does not cover: `grep` /
`read_file` after `check_index_coverage`.
