Option Explicit
' Stable headless production launcher. Resolves CURRENT exactly like jarvis-supervisor.cmd.
' Invoked by Task Scheduler via wscript.exe //B //Nologo (no console host).

Dim fso, shell, installRoot, currentFile, sha, release, pyw, args, i, arg, cmd

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

installRoot = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\JARVIS"

If Not fso.FileExists(installRoot & "\CURRENT") Then
  WScript.Echo "FAIL_CLOSED: CURRENT pointer missing"
  WScript.Quit 2
End If

Set currentFile = fso.OpenTextFile(installRoot & "\CURRENT", 1)
sha = Trim(currentFile.ReadLine())
currentFile.Close

release = installRoot & "\releases\" & sha
pyw = release & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(pyw) Then
  WScript.Echo "FAIL_CLOSED: release pythonw missing"
  WScript.Quit 2
End If

args = ""
For i = 0 To WScript.Arguments.Count - 1
  arg = WScript.Arguments(i)
  If InStr(arg, " ") > 0 Or InStr(arg, """") > 0 Then
    args = args & " " & """" & Replace(arg, """", """""") & """"
  Else
    args = args & " " & arg
  End If
Next

cmd = """" & pyw & """ -m jarvis_foundry.deployment.headless_launcher" & args
' Keep the scheduled action alive for the actual Supervisor process.  A
' detached child makes Task Scheduler believe the instance finished, so a
' repeating trigger can launch overlapping Supervisors even with IgnoreNew.
' Waiting preserves the scheduler's instance identity and makes restart
' policy deterministic; the Supervisor itself owns the singleton lease.
shell.Run cmd, 0, True
WScript.Quit 0
