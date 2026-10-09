' Starts DropTray in the tray with no console window. Double-click to run.
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
CreateObject("WScript.Shell").Run """" & here & "\.venv\Scripts\pythonw.exe"" """ & here & "\droptray.py"" --show", 0, False
