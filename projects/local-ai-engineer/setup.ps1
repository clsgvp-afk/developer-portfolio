param([switch]$WithOllama)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not $IsWindows -and $env:OS -ne 'Windows_NT') { throw 'Windows required' }
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw 'Install Python 3.11+ first' }
python -c "import sys; assert sys.version_info >= (3,11), 'Python 3.11+ required'"
if ($LASTEXITCODE) { throw 'Python check failed' }
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Write-Warning 'Git unavailable' }
$os = Get-CimInstance Win32_OperatingSystem
Write-Host "RAM: $([math]::Round($os.TotalVisibleMemorySize/1MB,1)) GB; free: $([math]::Round($os.FreePhysicalMemory/1MB,1)) GB"
Get-CimInstance Win32_Processor | Select-Object -ExpandProperty Name
Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion
Get-PSDrive -PSProvider FileSystem | Select-Object Name,@{n='FreeGB';e={[math]::Round($_.Free/1GB,1)}}
foreach ($d in 'data','workspace') { New-Item -ItemType Directory -Force $d | Out-Null }
if (-not (Test-Path .venv\Scripts\python.exe)) { python -m venv .venv }
if ($LASTEXITCODE) { throw 'Virtual environment creation failed' }
& .venv\Scripts\python.exe main.py init
& .venv\Scripts\python.exe -m unittest discover -s tests -v
if ($LASTEXITCODE) { throw 'Deterministic acceptance failed' }
$ollama = Get-Command ollama -ErrorAction SilentlyContinue
$localOllama = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
if (-not $ollama -and (Test-Path $localOllama)) { $ollama = Get-Item $localOllama }
if (-not $ollama -and $WithOllama) {
    if ((Read-Host 'Download and install Ollama for current user (several GB disk)? Type YES') -eq 'YES') {
        $installer = Join-Path $PSScriptRoot 'data\OllamaSetup.exe'
        if (-not (Test-Path $installer)) { Invoke-WebRequest 'https://ollama.com/download/OllamaSetup.exe' -OutFile $installer }
        $sig = Get-AuthenticodeSignature -LiteralPath $installer
        if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch 'Ollama') { throw 'Ollama installer signature not verified' }
        Start-Process -FilePath $installer -ArgumentList '/VERYSILENT','/NORESTART' -WindowStyle Hidden -Wait
        if (Test-Path $localOllama) { $ollama = Get-Item $localOllama }
    }
}
if ($ollama) {
    $exe = if ($ollama.Source) { $ollama.Source } else { $ollama.FullName }
    try { $null = Invoke-RestMethod http://127.0.0.1:11434/api/tags -TimeoutSec 3 }
    catch {
        $env:OLLAMA_HOST = '127.0.0.1:11434'
        $env:OLLAMA_NO_CLOUD = '1'
        $env:OLLAMA_NUM_PARALLEL = '1'
        $env:OLLAMA_MAX_LOADED_MODELS = '1'
        Start-Process -FilePath $exe -ArgumentList 'serve' -WindowStyle Hidden -RedirectStandardOutput 'data\ollama.stdout.log' -RedirectStandardError 'data\ollama.stderr.log'
        Start-Sleep -Seconds 3
    }
    $tags = Invoke-RestMethod http://127.0.0.1:11434/api/tags -TimeoutSec 10
    $cfg = Get-Content config.json -Raw | ConvertFrom-Json
    if ($cfg.model -notin @($tags.models.name)) {
        if ((Read-Host "Download $($cfg.model), about 1.9 GB? Type YES") -eq 'YES') {
            & $exe pull $cfg.model
            if ($LASTEXITCODE) { throw 'Model download failed; rerun to resume' }
        }
    }
    & .venv\Scripts\python.exe tests\live_check.py
} else {
    Write-Warning 'Ollama missing. Deterministic core installed; live inference not verified. Run .\setup.ps1 -WithOllama to install with confirmation.'
}
Write-Host 'Start: .\start.ps1'
