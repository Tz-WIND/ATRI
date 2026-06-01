# ATRI DAWproject snapshot helpers

This folder contains optional helper material for DAWs that can export
DAWproject but do not expose a stable direct-control API to ATRI.

The workflow is a DAWproject snapshot import:

1. ATRI writes export requests to `data/music_workstation/host_sync_requests`.
2. A DAW macro or helper script exports a `.dawproject` file into
   `data/music_workstation/host_sync_inbox`.
3. The DAW agent Host Project workspace imports the newest snapshot before
   responding.

This is not true headless control of the DAW. The Windows helpers use UI
automation and depend on the DAW window, menu language, shortcuts, modal
dialogs, and current song state.

Open-host integrations such as future REAPER direct adapters should not use
this path as their final architecture.
