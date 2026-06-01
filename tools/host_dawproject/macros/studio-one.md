# Studio One DAWproject snapshot macro

Studio One supports exporting the current song through the menu path:

`File > Convert To > DAWproject File...`

Recommended setup:

1. In Studio One, bind a keyboard shortcut to the DAWproject export command.
2. Use `Ctrl+Alt+D` for the ATRI helper default, or edit the helper script.
3. Export to ATRI's snapshot inbox:
   `data/music_workstation/host_sync_inbox`.
4. Use a stable project-oriented filename when possible, for example
   `Studio Cue.dawproject`.

ATRI normalizes common export variants such as:

- `Studio Cue.dawproject`
- `Studio Cue (1).dawproject`
- `Studio Cue 2026-06-01 09-30-00.dawproject`

Those names are treated as the same source snapshot and overwrite the same ATRI
project archive. A different project name creates a different archive.

The included AutoHotkey helper is not true headless control. It activates the
Studio One window and drives the export UI using the configured shortcut.
