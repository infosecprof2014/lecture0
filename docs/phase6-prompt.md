# LocalForge AI - Phase 6 prompt

```
PHASE 6 OF 6: Hardening

Task: backups and restore, auto-start, a status check, an update script and a
user guide. No new packages.

ACCEPTANCE TESTS (all must pass):
1. All automated tests pass (fast + real model).
2. A backup is made and passes verification.
3. Restore brings back database, prompts and projects.
4. Old backups are pruned.
5. Auto-start can be turned on (hidden, with backup) and off.
6. start-core.ps1 -Backup starts everything; page and chat work.
7. status.ps1 reports every part healthy.
8. update.ps1 can check for a new version.
9. The user guide covers starting, using, every script and troubleshooting.
10. Results saved to D:\LocalForgeAI\docs\phase6-report.md
```
