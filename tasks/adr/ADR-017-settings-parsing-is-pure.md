# ADR-017 — Settings parsing is pure and executable boundaries load dotenv

- Status: Accepted (C05 complete)
- Date: 2026-08-26
- Decision makers: Product/Engineering team
- Relates to: C05 in [SPEC-architecture-improvement-program](../specs/SPEC-architecture-improvement-program.md)

## Context

The settings types in `config.py` previously called `load_runtime_environment()` whenever
`from_env()` received no explicit mapping. A value parser therefore performed hidden disk I/O,
and deleting a process environment variable did not make it absent when a nearby `.env` file
contained the same name. This caused an offline test to make a real, billed provider call.

Callers could pass `load_env_file=False`, but that flag duplicated an operational concern across
settings types and call sites. It also made the safe behavior opt-in while preserving a surprising
default.

## Decision

**Settings parsing is pure. Executable composition boundaries own dotenv loading.**

1. Every `Settings.from_env()` method and related parser reads only the supplied mapping or the
   current `os.environ`. It does not read files and exposes no `load_env_file` switch.
2. `load_runtime_environment()` remains the single dotenv I/O seam.
3. Long-lived executables call that seam before composing settings: the FastAPI application,
   worker, ingestion CLI, and live evaluation or Gmail-candidate commands.
4. Library code, providers, feature modules, and tests pass mappings or read the already-composed
   process environment. They never trigger dotenv loading implicitly.
5. Langfuse bootstrap keeps its intentional import-time environment initialization because it is
   a dedicated integration boundary, not a settings parser.

## Rationale

- **Locality.** Disk I/O is visible where a process starts, while settings classes own only
  validation and conversion.
- **Testability.** A test can control the full input with a mapping or `monkeypatch` without a
  repository `.env` silently replenishing deleted values.
- **Deletion test.** Removing the `load_env_file` parameter deletes a cross-cutting branch from
  every settings type and from dozens of call sites; the remaining loader has one operational
  responsibility.
- **Safety.** Offline code cannot become credentialed merely by parsing settings in a directory
  containing `.env`.

## Alternatives considered

### Keep `load_env_file=True` as the default

Rejected. It preserves the hidden-I/O and billed-call failure mode and requires every safe caller
to remember a negative flag.

### Default the flag to false

Rejected. The parser would still expose an I/O policy and callers could reintroduce the unsafe
behavior. A separate boundary function expresses the lifecycle more directly.

### Load dotenv once at module import

Rejected. Import order would control configuration, and importing library code would mutate the
process environment. Executable entry points are the explicit lifecycle boundary.

## Consequences

- Direct `Settings.from_env()` calls no longer discover `.env`; callers that are executable entry
  points must invoke `load_runtime_environment()` first.
- Tests use explicit mappings or patched `os.environ` without an extra escape hatch.
- New commands that expect project `.env` values must load them once before the first settings
  parse, and the settings-parser invariant remains owned by `tests/unit/test_config.py`.
