[CmdletBinding()]
param()
# The clean delivery is the original editable app, separate from dist/Vidnets/App.
& (Join-Path $PSScriptRoot 'create-original-working-folder.ps1')
