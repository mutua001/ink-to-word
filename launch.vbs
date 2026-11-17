Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
appFolder = files.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = appFolder
exitCode = shell.Run("cmd /c run_app.bat hidden", 0, True)
If exitCode <> 0 Then
    MsgBox "Ink to Word could not start. Run run_app.bat directly to see the error.", vbExclamation, "Ink to Word"
End If
