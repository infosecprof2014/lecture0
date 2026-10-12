# LocalForge AI - Phase 2 prompt

## Assumed 6-phase roadmap

LocalForge AI is assumed to be a private AI assistant that runs fully on one
Windows laptop, with no cloud. Phases 3-6 are a draft, not yet agreed.

1. Environment check and installation - done (see Phase 1 report)
2. **Core engine** - local service on port 8000 that talks to the model and
   remembers conversations
3. Agents - planner, coder and reviewer on top of the core engine
4. Projects and tools - work on files in `D:\LocalForgeAI\projects`, run and
   test code safely
5. Web interface in the browser (Node.js installed here)
6. Hardening - backups, auto-start, full test run, user guide

## Prompt

```
PHASE 2 OF 6: Core engine

Task: build the LocalForge AI core service that sits
between the user and Ollama. Build nothing else (no
agents, no UI).

CONTEXT (from Phase 1, real measurements)
- Windows 10, i7-5500U 2 cores/4 threads, CPU-only
- 15.9 GB RAM, often only 2-4 GB free
- llama3.2:3b at ~7.5 tokens/s, ~2.5 GB RAM
- Python 3.11.9, venv at D:\LocalForgeAI\.venv
- Port 8000 free, Ollama on 127.0.0.1:11434

STEP 1: Design (no code yet)
Show me, in under one page:
- The files you will create under D:\LocalForgeAI\core
  and what each does
- The API endpoints
- The database tables
- The Python packages needed, with size and source
Wait for my yes.

STEP 2: Install packages
Install only what Step 1 listed, into the .venv only,
from PyPI. Pin exact versions in
D:\LocalForgeAI\config\requirements.txt.
Tell me name, source, and size first; wait for my yes.

STEP 3: Build the core service
A Python web service that:
- Listens on 127.0.0.1:8000 only (not the network)
- GET  /health  -> service status, Ollama status,
  loaded model, free RAM
- GET  /models  -> installed Ollama models
- POST /chat    -> sends a message to the model and
  streams the answer back word by word
- Saves every conversation and message in SQLite at
  D:\LocalForgeAI\database\localforge.db
- GET  /conversations and /conversations/{id} to read
  history back
- Reads settings (model name, port, limits) from
  D:\LocalForgeAI\config\settings.json, not hardcoded
- Writes logs to D:\LocalForgeAI\logs\core.log,
  rotating, max 5 MB per file
- Refuses a request politely if free RAM is below a
  set limit, instead of freezing the PC
- Keeps only one model loaded at a time
- Has a clear error message when Ollama is not running
Add start-core.ps1 in D:\LocalForgeAI\scripts that
activates the venv and starts the service.

STEP 4: Tests
Write automated tests in D:\LocalForgeAI\tests that run
with one command and do not need the real model for the
fast tests (use a fake Ollama). Add one slow test that
uses the real llama3.2:3b.

ACCEPTANCE TESTS (all must pass):
1. start-core.ps1 starts the service with no errors.
2. GET /health returns status ok and Ollama reachable.
3. POST /chat "Say OK" streams back a response.
4. After a restart, the conversation from test 3 is
   still returned by /conversations.
5. With Ollama stopped, /chat returns a clear error,
   and the service does not crash.
6. The service cannot be reached from another device
   (bound to 127.0.0.1 only).
7. All automated tests pass.
8. Results saved to
   D:\LocalForgeAI\docs\phase2-report.md

DELIVER: the code, requirements.txt, settings.json,
start-core.ps1, the tests, phase2-report.md (what was
built, test results, response time for /chat, known
limits), and the exact commands to repeat each test.
Then stop.
```

## Why it is shaped this way

- RAM guard and one-model limit: Phase 1 measured only 2-4 GB free RAM.
- Streaming: at ~7.5 tokens/s a full answer takes 10-20 s.
- 127.0.0.1 only: nothing on the network can reach the service.
- Fake Ollama in fast tests: real-model tests are slow on this CPU.
