# Local AI Engineer

A Windows-based Python CLI for running coding tasks with a local Ollama model. Task state is stored in SQLite so a paused task can resume. The workflow stages changes and can run tests with user approval.

## Run locally

Requires Python and Ollama. Run from PowerShell in this directory:

```powershell
.\setup.ps1
.\start.ps1
```

Follow the setup prompts for the local model. Run the offline test suite with:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

The test suite uses simulated model responses. Live Ollama checks are separate. Local task data, model files and API credentials are excluded.
