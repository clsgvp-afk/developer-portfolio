$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw 'Run .\setup.ps1 first' }
$offlineCommand = $args.Count -gt 0 -and $args[0] -in @('pause','stop','cancel','inspect','history','cloud','memory','export','init','search')
if ($offlineCommand) {
    & $python main.py @args
    exit $LASTEXITCODE
}
try { $null = Invoke-RestMethod http://127.0.0.1:11434/api/tags -TimeoutSec 3 }
catch {
    $ollama = Get-Command ollama -ErrorAction SilentlyContinue
    $exe = if ($ollama) { $ollama.Source } else { Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe' }
    if (-not (Test-Path $exe)) { throw 'Ollama missing. Run .\setup.ps1 -WithOllama' }
    $env:OLLAMA_HOST = '127.0.0.1:11434'
    $env:OLLAMA_NO_CLOUD = '1'
    $env:OLLAMA_NUM_PARALLEL = '1'
    $env:OLLAMA_MAX_LOADED_MODELS = '1'
    Start-Process -FilePath $exe -ArgumentList 'serve' -WindowStyle Hidden -RedirectStandardOutput 'data\ollama.stdout.log' -RedirectStandardError 'data\ollama.stderr.log'
    Start-Sleep -Seconds 3
    try { $null = Invoke-RestMethod http://127.0.0.1:11434/api/tags -TimeoutSec 10 } catch { throw 'Ollama startup failed: see data\ollama.stderr.log' }
}
& $python main.py @args
exit $LASTEXITCODE
