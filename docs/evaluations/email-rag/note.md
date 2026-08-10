7. So when exactly should Luna xHigh replace Terra?

Here's the rule I would use:

Use Luna xHigh when the difficulty comes from doing a lot of work.
Use Terra when the difficulty comes from figuring out what work needs to be done.

That sounds subtle, but it's extremely useful.

Luna xHigh territory

A task says:

Implement the OAuth callback according to this spec.
Modify these 5 files.
Run tests.
Fix failures.
Don't change public APIs.

The task may take 30 minutes of agent work.

But uncertainty is low.

Luna xHigh.

Or:

Upgrade this Docker Compose setup from Redis 7.2 to 8.x, adjust configuration, run integration tests and fix incompatibilities.

Lots of commands and iteration.

But there's a clear verifier.

Luna xHigh.

Or:

Refactor these repositories to use the new interface. All tests must pass.

Long horizon.

But mechanical and testable.

Luna xHigh.

This lines up particularly well with Luna's strong Terminal-Bench, DeepSWE, SWE-Bench and tool-use results.

8. When I would immediately switch back to Terra

The important triggers are:

The model must discover the task rather than execute it.
The relevant evidence is scattered across huge context.
There is no strong automatic verifier/test suite.
One wrong architectural assumption creates lots of downstream work.
You're debugging an unknown rather than implementing a known solution.
The problem involves ML/research reasoning rather than normal application code.