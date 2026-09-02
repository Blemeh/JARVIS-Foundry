Option Explicit
' Stable headless production launcher. Resolves CURRENT exactly like jarvis-supervisor.cmd.
' Invoked by Task Scheduler via wscript.exe //B //Nologo (no console host).

Dim fso, shell, environment, installRoot, scriptInstallRoot, configuredRoot
Dim currentFile, sha, release, pyw, args, i, arg, cmd, exitCode

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
Set environment = shell.Environment("Process")

' The task action is bound to this exact VBS file.  Derive the install root
' from that path so a stale process environment can never redirect a Scheduler
' trigger to another release tree.  An explicitly configured root is accepted
' only when it agrees with the bound path.
scriptInstallRoot = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
scriptInstallRoot = fso.GetAbsolutePathName(scriptInstallRoot)
configuredRoot = shell.ExpandEnvironmentStrings("%JARVIS_INSTALL_ROOT%")
If configuredRoot <> "%JARVIS_INSTALL_ROOT%" And Len(Trim(configuredRoot)) > 0 Then
  configuredRoot = fso.GetAbsolutePathName(configuredRoot)
  If LCase(configuredRoot) <> LCase(scriptInstallRoot) Then
    WScript.Echo "FAIL_CLOSED: JARVIS_INSTALL_ROOT does not match task launcher"
    WScript.Quit 2
  End If
End If
installRoot = scriptInstallRoot

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

' Make the source identity explicit to the child.  The launcher still
' re-reads CURRENT and validates sys.prefix, so this is an observation and
' not an authority that can override the immutable release pointer.
environment("JARVIS_INSTALL_ROOT") = installRoot
environment("JARVIS_RELEASE_ID") = sha
environment("JARVIS_RELEASE_SHA") = sha
environment("PLAYWRIGHT_BROWSERS_PATH") = release & "\.pw"

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
exitCode = shell.Run(cmd, 0, True)
WScript.Quit exitCode
