Option Explicit
Dim fso, shell, root, bat, cmd
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)
bat = fso.BuildPath(root, "Chatterbox.bat")

If Not fso.FileExists(bat) Then
    MsgBox "Chatterbox.bat was not found:" & vbCrLf & bat, vbCritical, "Chatterbox Studio"
    WScript.Quit 1
End If

' The --silent flag prevents a hidden console from waiting at a pause prompt.
' launcher.py writes diagnostic output to logs\launcher.log.
cmd = """" & bat & """ --silent"
shell.CurrentDirectory = root
shell.Run cmd, 0, False
