' ============================================================================
' VIDNETS STUDIO - Silent Launcher (No Console Window)
' Double-click this to start the app silently!
' ============================================================================

Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)

' Launch START.bat minimized/hidden
WshShell.Run "START.bat", 0, False
