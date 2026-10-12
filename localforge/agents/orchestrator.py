"""Runs one task: plan -> for each step: code -> (syntax check) -> review -> revise."""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

from core.config import Settings
from core.db import Database
from core.ollama_client import OllamaClient, OllamaUnavailable
from core.sandbox import check_code, run_tests

from .base import AgentError
from .coder import Coder, syntax_problem
from .planner import Planner
from .reviewer import Reviewer
from .tester import Fixer, Tester, signatures

log = logging.getLogger("localforge.agents")


class Orchestrator:
    def __init__(self, db: Database, ollama: OllamaClient, settings: Settings, model: str,
                 emit: Callable[[dict], None], touch: Callable[[], None] = lambda: None):
        self.db, self.settings, self.emit = db, settings, emit
        self.task_id = 0
        self.step_id: int | None = None
        self.step_number: int | None = None
        common = dict(temperature=settings.agent_temperature, record=self._record, before_call=touch)
        self.planner = Planner(ollama, model, max_tokens=settings.planner_max_tokens, **common)
        self.coder = Coder(ollama, model, max_tokens=settings.coder_max_tokens, **common)
        self.reviewer = Reviewer(ollama, model, max_tokens=settings.reviewer_max_tokens, **common)
        self.tester = Tester(ollama, model, max_tokens=settings.tester_max_tokens, **common)
        self.fixer = Fixer(ollama, model, max_tokens=settings.fixer_max_tokens, **common)

    def _record(self, agent: str, input_text: str):
        run_id = self.db.start_agent_run(self.task_id, self.step_id, agent, input_text)
        self.emit({"type": "agent_start", "agent": agent, "step": self.step_number, "run_id": run_id})

        def finish(status: str, output: str, tokens: int | None, duration_ms: int) -> None:
            self.db.finish_agent_run(run_id, status, output, tokens, duration_ms)
            self.emit({"type": "agent_done", "agent": agent, "step": self.step_number, "run_id": run_id,
                       "status": status, "tokens": tokens, "duration_ms": duration_ms})
        return finish

    async def run(self, task_id: int, request: str) -> str:
        """Returns the final status: done, failed or cancelled."""
        self.task_id = task_id
        self.emit({"type": "task_start", "task_id": task_id})
        try:
            steps, truncated = await self.planner.plan(request, self.settings.max_plan_steps)
            self.db.set_task_plan(task_id, steps)
            self.emit({"type": "plan", "steps": steps, "truncated": truncated})

            written: list[str] = []
            for index, step in enumerate(steps):
                self.step_number = index + 1
                self.step_id = self.db.add_step(task_id, index + 1, step["title"], step["description"])
                code, verdict, issues, revisions = await self._do_step(request, steps, index, written)
                self.db.update_step(self.step_id, code=code, verdict=verdict, revisions=revisions, issues=issues)
                self.emit({"type": "step_done", "step": index + 1, "title": step["title"], "verdict": verdict,
                           "revisions": revisions, "issues": issues})
                if verdict != "syntax_error":
                    written.append(code)
            self.step_id = self.step_number = None
            await self._test_phase(task_id, request, written)
            status, error = "done", None
        except asyncio.CancelledError:
            self.db.finish_task(task_id, "cancelled", "Cancelled by the user")
            self.emit({"type": "task_end", "task_id": task_id, "status": "cancelled"})
            log.info("Task %d cancelled", task_id)
            raise
        except (AgentError, OllamaUnavailable) as exc:
            status, error = "failed", str(exc)
        except Exception as exc:  # noqa: BLE001 - a task must always end with a status
            log.exception("Task %d crashed", task_id)
            status, error = "failed", f"Unexpected error: {exc}"
        self.db.finish_task(task_id, status, error)
        self.emit({"type": "task_end", "task_id": task_id, "status": status, "error": error})
        log.info("Task %d %s%s", task_id, status, f": {error}" if error else "")
        return status

    async def _do_step(self, request: str, steps: list[dict], index: int,
                       written: list[str]) -> tuple[str, str, list[str], int]:
        code: str | None = None
        issues: list[str] = []
        revisions = 0
        while True:
            code = await self.coder.write(request, steps, index, written, issues, code)
            problem = syntax_problem(code)
            if problem:
                # Not valid Python: send straight back without spending a reviewer call.
                verdict, issues = "syntax_error", [problem]
            else:
                verdict, issues = await self.reviewer.review(request, steps[index], code)
            if verdict == "approve" or revisions >= self.settings.max_revisions:
                return code, verdict, issues, revisions
            revisions += 1
            self.emit({"type": "revise", "step": index + 1, "revision": revisions, "issues": issues})

    async def _test_phase(self, task_id: int, request: str, written: list[str]) -> None:
        """Write the project files, have the Tester write tests, run them safely, fix and re-run."""
        solution = "\n\n".join(written).strip()
        project = self.settings.projects_dir / f"task-{task_id}"
        project.mkdir(parents=True, exist_ok=True)
        self.db.set_task_project(task_id, str(project))
        (project / "solution.py").write_text(solution + "\n", encoding="utf-8")
        self.emit({"type": "testing_start", "project_dir": str(project)})

        def finish(status: str, note: str = "") -> None:
            self.db.set_test_status(task_id, status)
            self.emit({"type": "testing_done", "test_status": status, "note": note})

        if not solution:
            return finish("no_code", "no step produced valid code")
        problems = check_code(solution)
        if problems:
            self.db.add_test_run(task_id, 0, "blocked", 0, 0, "\n".join(problems), 0)
            return finish("blocked", "the code failed the safety check: " + "; ".join(problems[:3]))
        sigs = signatures(solution)
        if not sigs:
            return finish("no_tests", "the solution has no public functions or classes to test")
        tests, why = await self.tester.write_tests(request, sigs)
        if tests is None:
            return finish("no_tests", f"the Tester could not write usable tests: {why}")
        (project / "test_solution.py").write_text(tests + "\n", encoding="utf-8")

        for round_no in range(self.settings.max_fix_rounds + 1):
            result = await asyncio.to_thread(run_tests, project,
                                             timeout_s=self.settings.test_timeout_s,
                                             memory_mb=self.settings.sandbox_memory_mb)
            self.db.add_test_run(task_id, round_no, result.status, result.passed, result.failed,
                                 result.summary(), result.duration_ms)
            self.emit({"type": "test_run", "round": round_no, "status": result.status, "passed": result.passed,
                       "failed": result.failed, "memory_cap": result.memory_cap, "summary": result.summary()})
            if result.status in ("passed", "blocked") or round_no == self.settings.max_fix_rounds:
                break
            fixed = await self.fixer.fix(request, solution, tests, result.summary())
            problems = check_code(fixed)
            if problems:
                self.emit({"type": "fix_rejected", "round": round_no + 1, "problems": problems[:3]})
                break
            solution = fixed
            (project / "solution.py").write_text(solution + "\n", encoding="utf-8")
            self.emit({"type": "fix", "round": round_no + 1})
        finish(result.status)
