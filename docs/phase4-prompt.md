# LocalForge AI - Phase 4 prompt

```
PHASE 4 OF 6: Run generated code safely

Task: after the agents finish, save the code as a project, have a Tester
agent write tests from the request (without seeing the code), run them in
a sandbox, and send failures back to be fixed (max 2 rounds).

Sandbox: static safety check before running; runtime block on file writes,
network and starting programs; isolated process; time limit; memory cap.

ACCEPTANCE TESTS (all must pass):
1. All automated tests pass (fast + real model).
2. Dangerous code is blocked before it runs.
3. At runtime, writing files and network access are blocked.
4. An endless loop is stopped at the time limit.
5. Memory cap applied; a memory hog is stopped.
6. Tests are written and run; project files saved.
7. The final code passes the Tester's tests.
8. Independent reference tests pass on the final code.
9. Results saved to D:\LocalForgeAI\docs\phase4-report.md
```
