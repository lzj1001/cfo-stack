$utf8 = [Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$projectRoot = $env:PA_PROJECT_ROOT
if ([string]::IsNullOrWhiteSpace($projectRoot)) {
    throw 'PA_PROJECT_ROOT is required'
}
$python = if ($env:PA_PYTHON) { $env:PA_PYTHON } else { Join-Path $projectRoot '.venv\Scripts\python.exe' }
$env:PYTHONPATH = Join-Path $projectRoot 'src'
& $python -m personal_assistant.hermes_entry
exit $LASTEXITCODE
