# LocalForge AI

A private AI assistant that runs entirely on one Windows PC: a local model (Ollama, `llama3.2:3b`),
a core service on `http://127.0.0.1:8000`, agents that plan, write, review and test Python code in
a sandbox, and a web page for chat and tasks. Nothing leaves the computer.

| Path | Contents |
|---|---|
| `inspect.ps1`, `install.ps1`, `benchmark.ps1` | Phase 1: machine check, install, benchmark |
| `localforge/` | Everything copied to `D:\LocalForgeAI` (core, agents, ui, scripts, tests, config) |
| `localforge/docs/USER-GUIDE.md` | How to use it |
| `docs/phase*-prompt.md` | The prompt for each phase |

Install or update on the PC (Git and the Phase 1 venv must exist):

```powershell
git clone --depth 1 -b claude/localforge-env-setup-tgrkrl https://github.com/infosecprof2014/lecture0 $HOME\LocalForge-src
robocopy $HOME\LocalForge-src\localforge D:\LocalForgeAI /E /XF settings.json /NJH /NJS /NP /NFL /NDL
D:\LocalForgeAI\.venv\Scripts\python.exe -m pip install -r D:\LocalForgeAI\config\requirements.txt
```

Later updates: `D:\LocalForgeAI\scripts\update.ps1`.
