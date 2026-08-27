# ADR-016 — Report artifacts are validated domain values behind one store

- Status: Accepted (C01 complete at `9c4e5fc`; record added 2026-08-26)
- Date: 2026-08-26
- Decision makers: Product/Engineering team
- Relates to: C01/C08 in [SPEC-architecture-improvement-program](../specs/SPEC-architecture-improvement-program.md); partners [ADR-013](ADR-013-composition-as-typed-value.md) and [ADR-015](ADR-015-routers-own-their-transport.md)

## Context

Report creation originally spread three literal `data/reports` paths across the
application. One writer accepted a model-supplied filename and wrote it inside a broad
`except Exception`, while frontend-called download and PDF routes had no server-side
implementation. A filename was therefore an unchecked string until it reached the filesystem,
and no interface described what the application could do with a stored report.

The first C01 implementation fixed the defect before this architecture program adopted an ADR
habit. This record makes the two lasting contracts explicit so later report work does not
reintroduce path handling or bypass the store.

## Decision

**A report filename is a domain value, and all report persistence crosses one store interface.**

1. `ReportFilename` is the only value accepted by report persistence. `parse()` normalizes
   external input to its basename under both POSIX and Windows rules, then rejects an unusable
   basename; `sanitize()` handles provider-generated text and deterministically degrades it to a
   safe filename. Direct construction validates too.
2. `ReportArtifactStore` owns every application operation on the report collection: save, list,
   read, delete, path lookup, and the collection location. Routes and chat orchestration do not
   construct filesystem paths.
3. `FileSystemReportArtifactStore` is the local adapter. It resolves every target as a direct
   child of its configured root and rejects a symlink or other resolution that escapes it.
4. `ReportPdfRenderer` is a separate optional port. The transport may report that the capability
   is unavailable, but must not embed rendering policy or choose a dependency. C08 owns that
   decision.

## Rationale

- **Deletion test.** Delete the domain value and store interface, and path normalization,
  traversal rejection, file I/O, listing resilience, and test setup spread back across every
  writer and route.
- **Depth.** Callers learn one filename type and one store interface; the implementation hides
  platform-specific path parsing, containment checks, thread offloading, encoding, and metadata.
- **Locality.** A filename-policy change is made once in the domain module. A persistence change
  is made once in the adapter. Transport remains about HTTP status and serialization.
- **Different failure policies are intentional.** User-addressed reads/deletes fail on invalid
  names; provider-generated report names degrade because discarding requested content over a bad
  title would be the worse outcome.

## Alternatives considered

### Keep filename checks in each route and writer

Rejected. It duplicates two-platform path parsing and lets non-route writers bypass the checks.
The interface would remain a string plus undocumented conventions.

### Expose the reports directory as a `Path`

Rejected. A directory is implementation detail, not the application interface. Exposing it makes
every caller responsible for containment, encoding, and I/O behavior.

### Ship a PDF implementation as part of C01

Rejected for C01. Faithful Vietnamese Markdown rendering needs an embedded Unicode font and a
dependency choice. The port preserves the seam without pretending that decision was made; C08
tracks it explicitly.

## Consequences

- The report root is configured once and injected as `ReportArtifactStore` through
  `CoworkRuntime.reports`.
- New report writers must construct `ReportArtifact` with `ReportFilename`; raw path strings do
  not cross the store interface.
- Filesystem containment remains defense in depth even though `ReportFilename` rejects directory
  parts.
- PDF export remains an explicit unavailable capability until C08 is decided. Implementing it
  must not weaken filename or store ownership.
