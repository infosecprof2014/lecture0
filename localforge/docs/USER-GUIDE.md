# LocalForge AI - User guide

LocalForge AI is a private AI assistant that runs entirely on this PC. Nothing is sent to the
internet: the model runs in Ollama, and LocalForge only listens on `127.0.0.1` (this computer).

## Start and open it

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\LocalForgeAI\scripts\start-core.ps1
```

Then open **http://127.0.0.1:8000** in your browser. Leave the PowerShell window open; press
Ctrl+C in it to stop LocalForge.

Ollama must be running too (it normally starts with Windows; otherwise open "Ollama" from the
Start menu).

## Chat

Type a message and press **Send** (or Ctrl+Enter). The answer appears word by word. **Stop**
ends an answer early; what arrived is kept. Earlier conversations are listed on the left.

- The first message after a few idle minutes takes about 30 s: the model is loading from disk.
  After that, answers start in about 1 s and arrive at roughly 7-8 tokens (5-6 words) per second.
- Only one chat or task runs at a time. If you see "busy", wait for the current one to finish.

## Talk to it (voice)

Install once: `scripts\install-phase7.ps1` (asks before each download).

- In **Chat**, click **🎤 Speak**, talk, then click **■ Stop recording**. Your words are turned into
  text on this PC (Whisper; nothing is sent anywhere) and sent as your message.
- Tick **Speak answers** to hear replies read aloud with the Windows voices.
- The browser asks once for microphone permission for `127.0.0.1`.

## Create files, images and videos

Open **Create**, pick 📄 File, 🖼️ Image or 🎬 Video, describe what you want and press **Create**.

| Kind | What you get | Time on this PC |
|---|---|---|
| File | One `.md .txt .csv .json .html .py .xml .yaml .ini .sql` file in `projects\files` | under a minute |
| Image | A 512x512 picture (SD-Turbo) in `projects\images` | about 30 s - 2 min |
| Video | A narrated slideshow MP4 (one picture per scene, slow zoom, fades, captions) in `projects\videos` | several minutes |

- Files are never overwritten (`report.md`, `report-2.md`, ...) and created web pages are only downloaded,
  never opened inside LocalForge.
- Making pictures needs about 2.5 GB of free memory: the chat model is unloaded first, so the next
  chat takes about 30 s to start.
- Realistic AI-generated motion video is **not** possible on this laptop (it needs a graphics card with
  12-24 GB of memory). Videos here are slideshows of generated pictures.
- **Cancel** stops a creation straight away.

## Tasks (agents that write code)

Open **Tasks**, describe the Python code you want, and press **Run task**. You will see:

1. **Planner** splits the request into 1-5 steps.
2. **Coder** writes code for each step; **Reviewer** approves it or asks for changes (max 2).
3. **Tester** writes tests from your request (without seeing the code). The tests run in a
   locked-down sandbox; if they fail, the **Fixer** rewrites the code (max 2 rounds).

A task takes a few minutes on this PC. **Cancel** stops it straight away. The finished code is in
`D:\LocalForgeAI\projects\task-<number>\solution.py`, with its tests in `test_solution.py`.

Check the code before you use it: the model is small and can make mistakes, and the tests are
written by the same model. Generated code only runs inside the sandbox, which blocks writing
files, network access and starting programs, and limits time (10 s) and memory (512 MB).

## Everyday commands

All commands are run as `powershell -NoProfile -ExecutionPolicy Bypass -File <script>` from
`D:\LocalForgeAI\scripts`:

| Script | What it does |
|---|---|
| `start-core.ps1` | Start LocalForge (add `-Backup` to back up first if the last backup is over 24 h old) |
| `status.ps1` | Health check: service, Ollama, model, database, backups, disk, memory, auto-start |
| `backup.ps1` | Back up now (`-List` to list, `-Verify <zip>` to check one) |
| `restore.ps1 -File <zip>` | Restore a backup (stop LocalForge first; your current data is backed up first) |
| `register-autostart.ps1` | Start LocalForge automatically (hidden) when you sign in |
| `unregister-autostart.ps1` | Turn auto-start off |
| `update.ps1` | Get the latest version (`-CheckOnly` to just check); your data and settings are kept |
| `install-phase7.ps1` | Install voice input, image and video tools (asks before each download) |

## Backups

A backup is one zip in `D:\LocalForgeAI\backups` containing the database (chats, tasks, every
model call), `config\settings.json`, the agents' instructions and all task projects. The newest
10 are kept. With auto-start on, a backup is made at sign-in if the last one is over 24 h old.

## Settings

`D:\LocalForgeAI\config\settings.json`. Restart LocalForge after editing. Useful ones:

| Setting | Default | Meaning |
|---|---|---|
| `model` | `llama3.2:3b` | Ollama model to use (`qwen3:4b` is also installed: better code, slower) |
| `ram_reserve_mb` | 500 | Memory kept free; requests are refused politely below this |
| `max_plan_steps` / `max_revisions` / `max_fix_rounds` | 5 / 2 / 2 | Limits for tasks |
| `test_timeout_s` / `sandbox_memory_mb` | 10 / 512 | Sandbox limits for generated code |
| `keep_backups` / `auto_backup_hours` | 10 / 24 | Backup retention and frequency |

The agents' instructions are plain text in `D:\LocalForgeAI\agents\prompts\` and can be edited.

## Where things are

| Folder | Contents |
|---|---|
| `core\` | The service (API, database, sandbox, backups) |
| `agents\` | Planner, Coder, Reviewer, Tester, Fixer and their prompts |
| `ui\` | The web page |
| `database\localforge.db` | All chats and tasks |
| `projects\` | Code produced by tasks |
| `logs\core.log` | Log (5 MB x 5 files) |
| `backups\` | Backups |
| `docs\` | This guide and the phase reports |

## Troubleshooting

| Problem | Fix |
|---|---|
| Page does not open | Run `status.ps1`; start the service with `start-core.ps1` |
| "Ollama is not running" | Open Ollama from the Start menu |
| "Not enough free memory" | Close some programs (a 3B model needs about 2.5 GB free) |
| "busy" | Wait for the running chat/task, or cancel the task |
| "Port 8000 is already in use" | LocalForge is already running (check the taskbar / auto-start) |
| Something broke after a change | `restore.ps1 -File <newest backup>` |

## Privacy and safety

- Listens on 127.0.0.1 only; other devices cannot connect. Requests addressed to other host
  names, and actions started by other websites, are refused.
- No account or password: anyone signed in to this Windows account can use it.
- Generated code is never run outside the sandbox. The sandbox is layered protection, not a
  virtual machine.
