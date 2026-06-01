from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT / "tools" / "host_dawproject"
README = TOOLS_DIR / "README.md"
STUDIO_ONE_DOC = TOOLS_DIR / "macros" / "studio-one.md"
STUDIO_ONE_AHK = TOOLS_DIR / "windows" / "studio_one_export_snapshot.ahk"


def test_host_dawproject_tools_document_snapshot_workflow():
    source = README.read_text(encoding="utf-8")

    assert "DAWproject snapshot" in source
    assert "host_sync_inbox" in source
    assert "host_sync_requests" in source
    assert "not true headless" in source


def test_studio_one_macro_doc_names_supported_workflow_and_shortcut():
    source = STUDIO_ONE_DOC.read_text(encoding="utf-8")

    assert "Studio One" in source
    assert "Convert To" in source
    assert "DAWproject" in source
    assert "Ctrl+Alt+D" in source


def test_studio_one_ahk_helper_polls_atri_request_and_exports_snapshot():
    source = STUDIO_ONE_AHK.read_text(encoding="utf-8")

    assert "#Requires AutoHotkey v2.0" in source
    assert "host_sync_requests" in source
    assert "host_sync_inbox" in source
    assert "studio-one-latest.dawproject" in source
    assert "WinActivate" in source
    assert "Ctrl+Alt+D" in source
    assert "not true headless" in source
