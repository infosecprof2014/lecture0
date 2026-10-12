# LocalForge AI - Phase 3 prompt (approved)

```
PHASE 3 OF 6: Agents

Task: add three agents that work together through the core
engine. Do not run generated code (Phase 4). No UI.

- Planner: turns a request into a plan of 1-5 steps (JSON)
- Coder: writes Python code for each step
- Reviewer: approves each step's code or asks for a
  revision (max 2 revisions per step)
- Live progress events; the user can cancel a task
- Every task, step and model call saved with time and tokens
- No new packages

ACCEPTANCE TESTS (all must pass):
1. All automated tests pass (fast + real-model).
2. A real request produces a valid plan of 1-5 steps.
3. Every step's code is valid Python (parsed, not run).
4. Every step has a reviewer verdict; max 2 revisions.
5. Progress shows live while the task runs.
6. Cancel stops a task within 10 s.
7. A task and all its model calls survive a restart.
8. While a task runs, /chat answers "busy".
9. The report shows time and tokens per agent.
10. Results saved to D:\LocalForgeAI\docs\phase3-report.md

DELIVER: the code, tests, phase3-acceptance.ps1,
phase3-report.md, and the commands to repeat each test.
Then stop.
```
