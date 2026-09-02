' Windows Script Host test payload disguised as PDF
' Used for testing Cowork Agent attachment security triage & extension inspection
Dim objFSO, objShell
Set objShell = CreateObject("WScript.Shell")
WScript.Echo "Simulated payload execution: Test attachment only."
