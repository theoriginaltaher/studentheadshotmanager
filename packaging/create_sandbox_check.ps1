# Generates a reproducible offline Windows Sandbox check; it does not enable or launch Sandbox.
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$kit=Join-Path $PSScriptRoot 'sandbox-kit'
$evidence=Join-Path $PSScriptRoot 'reports\sandbox-evidence'
New-Item -ItemType Directory -Force -Path $kit,$evidence | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'test_installer.ps1') -Destination $kit
$release=[Security.SecurityElement]::Escape((Join-Path $root 'release'))
$kitXml=[Security.SecurityElement]::Escape($kit)
$evidenceXml=[Security.SecurityElement]::Escape($evidence)
@"
<Configuration>
  <Networking>Disable</Networking>
  <ClipboardRedirection>Disable</ClipboardRedirection>
  <MappedFolders>
    <MappedFolder><HostFolder>$release</HostFolder><SandboxFolder>C:\Delivery</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>$kitXml</HostFolder><SandboxFolder>C:\Checks</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>$evidenceXml</HostFolder><SandboxFolder>C:\Evidence</SandboxFolder><ReadOnly>false</ReadOnly></MappedFolder>
  </MappedFolders>
  <LogonCommand><Command>powershell.exe -NoExit -ExecutionPolicy Bypass -File C:\Checks\test_installer.ps1 -Installer C:\Delivery\StudentHeadshotManager-Setup.exe -EvidenceRoot C:\Evidence</Command></LogonCommand>
</Configuration>
"@ | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $kit 'Offline validation.wsb')
Write-Host "Prepared $kit\Offline validation.wsb. Run only on a machine with Windows Sandbox enabled. This is not a completed test."
