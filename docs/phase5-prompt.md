# LocalForge AI - Phase 5 prompt

```
PHASE 5 OF 6: Web interface

Task: one web page at http://127.0.0.1:8000 for chat and tasks, served by
the core. Plain HTML/CSS/JS: no Node.js, no build step, nothing loaded
from the internet. Light/dark follows Windows. Model output shown as text.
Protect the API now that a browser can reach it: refuse unknown Host names
(DNS rebinding) and POSTs from other websites.

ACCEPTANCE TESTS (all must pass):
1. All automated tests pass (fast + real model).
2. The page and its files are served.
3. Microsoft Edge loads the page and its script runs.
4. Requests addressed to another host name are refused.
5. Other websites cannot start chats; this page can.
6. The page loads nothing from the internet.
7. The service still listens on 127.0.0.1 only.
8. Results saved to D:\LocalForgeAI\docs\phase5-report.md
```
