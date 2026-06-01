#Requires AutoHotkey v2.0
#SingleInstance Force

; Studio One DAWproject snapshot helper for ATRI.
; This is not true headless control: it activates Studio One and drives the
; DAWproject export UI with a configured shortcut.

requestDir := A_WorkingDir "\data\music_workstation\host_sync_requests"
inboxDir := A_WorkingDir "\data\music_workstation\host_sync_inbox"
requestPath := requestDir "\latest.json"
outputPath := inboxDir "\studio-one-latest.dawproject"
studioOneTitle := "ahk_exe Studio One.exe"
exportShortcut := "^!d" ; Ctrl+Alt+D
pollMs := 1000
lastRequestId := ""

DirCreate(requestDir)
DirCreate(inboxDir)

SetTimer(PollAtriDawprojectRequest, pollMs)

PollAtriDawprojectRequest() {
    global requestPath, lastRequestId
    if !FileExist(requestPath) {
        return
    }
    raw := FileRead(requestPath, "UTF-8")
    requestId := JsonString(raw, "id")
    host := JsonString(raw, "host")
    if !requestId || requestId = lastRequestId || host != "studio_one" {
        return
    }
    lastRequestId := requestId
    ExportStudioOneSnapshot()
}

ExportStudioOneSnapshot() {
    global studioOneTitle, exportShortcut, outputPath
    if !WinExist(studioOneTitle) {
        TrayTip("ATRI DAWproject", "Studio One window not found.", 5)
        return
    }
    WinActivate(studioOneTitle)
    if !WinWaitActive(studioOneTitle, , 3) {
        TrayTip("ATRI DAWproject", "Studio One did not become active.", 5)
        return
    }

    ; Ctrl+Alt+D should be bound in Studio One to Convert To > DAWproject File...
    Send(exportShortcut)
    Sleep(900)

    ; Save As dialogs vary by Windows language and Studio One version. This
    ; fallback types the target path and presses Enter when the dialog is active.
    SendText(outputPath)
    Sleep(100)
    Send("{Enter}")
}

JsonString(source, key) {
    pattern := '"' key '"\s*:\s*"([^"]*)"'
    if RegExMatch(source, pattern, &match) {
        return StrReplace(match[1], "\\", "\")
    }
    return ""
}
