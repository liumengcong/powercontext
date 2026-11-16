$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
npm ci
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
npm run models
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
npm run build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
npm start
