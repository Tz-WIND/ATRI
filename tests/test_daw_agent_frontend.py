from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHAT_INPUT = ROOT / "frontend" / "src" / "components" / "chat" / "ChatInput.vue"
USE_API = ROOT / "frontend" / "src" / "composables" / "useApi.js"
APP = ROOT / "frontend" / "src" / "App.vue"
DAW_AGENT_PAGE = ROOT / "frontend" / "src" / "components" / "chat" / "DawAgentPage.vue"
TOOL_CARD = ROOT / "frontend" / "src" / "components" / "chat" / "ToolCard.vue"


def test_chat_input_has_daw_workspace_picker_variant():
    source = CHAT_INPUT.read_text(encoding="utf-8")

    assert "dawWorkspacePicker" in source
    assert "workspace-picker-trigger" in source
    assert "workspace-menu" in source
    assert "Workspace" in source
    assert "Host Project" in source
    assert "ATRI Studio" in source


def test_daw_workspace_picker_replaces_stash_button_without_context_chips():
    source = CHAT_INPUT.read_text(encoding="utf-8")

    assert 'v-if="!dawWorkspacePicker"' in source
    assert 'v-if="dawWorkspacePicker"' in source
    assert "optional-stash" in source
    assert "workspace-picker-trigger" in source
    assert "Workspace:" not in source
    assert "Studio One connected" not in source


def test_use_api_exposes_daw_agent_chat_endpoint():
    source = USE_API.read_text(encoding="utf-8")

    assert "sendDawAgentMessage" in source
    assert "/api/daw-agent/chat" in source
    assert "project_session_id" in source
    assert "instance_id" in source
    assert "host_context" in source
    assert "model_provider" in source


def test_daw_agent_page_passes_selected_model_to_chat_api():
    source = DAW_AGENT_PAGE.read_text(encoding="utf-8")

    assert "useProviders" in source
    assert "activeModel" in source
    assert "activeModelProvider" in source
    assert "loadStatus" in source
    assert "model: activeModel.value" in source
    assert "modelProvider: activeModelProvider.value" in source


def test_daw_model_selector_uses_local_only_mode_in_daw_chat_input():
    source = CHAT_INPUT.read_text(encoding="utf-8")

    assert ':local-only="dawWorkspacePicker"' in source

    model_selector = (
        ROOT / "frontend" / "src" / "components" / "chat" / "ModelSelector.vue"
    ).read_text(encoding="utf-8")
    assert "localOnly" in model_selector
    assert "setLocalModel" in model_selector


def test_daw_agent_page_reuses_chat_components_and_daw_api():
    source = DAW_AGENT_PAGE.read_text(encoding="utf-8")

    assert "ChatMessage" in source
    assert "ChatInput" in source
    assert "ToolCard" in source
    assert "sendDawAgentMessage" in source
    assert "daw-workspace-picker" in source
    assert '@set-workspace="setWorkspace"' in source


def test_daw_agent_page_reuses_http_assistant_response_dedupe():
    source = DAW_AGENT_PAGE.read_text(encoding="utf-8")

    assert "addAssistantHttpResponse" in source
    assert "shouldAppendHttpAssistantResponse" not in source


def test_daw_agent_page_preserves_http_response_chain_fallback():
    source = DAW_AGENT_PAGE.read_text(encoding="utf-8")

    assert "addAssistantHttpResponse" in source
    assert "addAssistantHttpResponse(result)" in source
    assert "result.chain" not in source


def test_daw_agent_page_restores_project_thread_transcript_on_mount():
    source = DAW_AGENT_PAGE.read_text(encoding="utf-8")

    assert "useSession" in source
    assert "loadSessionMessages" in source
    assert "loadTranscript" in source
    assert "loadSessionMessages(currentThreadId.value)" in source
    assert "loadTranscript(transcript)" in source
    assert "resetMessages()" not in source


def test_use_chat_reuses_http_assistant_response_dedupe():
    source = (ROOT / "frontend" / "src" / "composables" / "useChat.js").read_text(encoding="utf-8")

    assert "shouldAppendHttpAssistantResponse" in source
    assert "new Promise(r => setTimeout(r, 120))" not in source


def test_use_chat_exposes_shared_http_assistant_response_handler():
    source = (ROOT / "frontend" / "src" / "composables" / "useChat.js").read_text(encoding="utf-8")

    assert "normalizeAssistantChain" in source
    assert "addAssistantHttpResponse" in source


def test_daw_agent_page_has_no_persistent_workspace_status_row():
    source = DAW_AGENT_PAGE.read_text(encoding="utf-8")

    assert "header-workspace" not in source
    assert "headerStatus" not in source
    assert "Studio One connected" not in source


def test_daw_agent_page_marks_websocket_as_daw_surface():
    source = DAW_AGENT_PAGE.read_text(encoding="utf-8")

    assert "useWebSocket(currentThreadId, { surface: 'daw-agent' })" in source


def test_daw_agent_page_shows_thinking_indicator_while_waiting():
    source = DAW_AGENT_PAGE.read_text(encoding="utf-8")

    assert "thinkingBlock" in source
    assert "toolCards" in source
    assert "showThinkingIndicator" in source
    assert 'class="thinking-indicator"' in source
    assert "hasExecutingTool" in source
    assert "tool.status === 'executing'" in source


def test_daw_agent_page_applies_music_project_broadcasts():
    source = DAW_AGENT_PAGE.read_text(encoding="utf-8")

    assert "handleProjectBroadcast" in source
    assert "event.type === 'music_project'" in source


def test_midi_artifact_card_uses_shared_host_project_and_tool_args_preview():
    source = (ROOT / "frontend" / "src" / "components" / "chat" / "MidiArtifactCard.vue").read_text(
        encoding="utf-8"
    )

    assert "useDawHost" in source
    assert "buildMidiArtifactPreview" in source
    assert "projectRevision" in source
    assert "studioProject()" not in source


def test_tool_card_embeds_midi_artifact_renderer_for_midi_tools():
    source = TOOL_CARD.read_text(encoding="utf-8")

    assert "MidiArtifactCard" in source
    assert "isMidiArtifactTool(activeTool.value.tool)" in source
    assert ':tool-data="activeTool"' in source


def test_midi_artifact_card_auto_exports_bridge_midi_for_daw_agent_surface():
    source = (ROOT / "frontend" / "src" / "components" / "chat" / "MidiArtifactCard.vue").read_text(
        encoding="utf-8"
    )

    assert "isDawAgentSurfaceLocation" in source
    assert "bridgeAutoExportKeyForArtifact" in source
    assert "autoExportBridgeMidi" in source
    assert "lastAutoExportKey" in source
    assert "bridgeStatusLabel" in source
    assert "bridgeInstanceIdFromLocation()" in source


def test_app_exposes_daw_agent_surface_without_main_shell():
    source = APP.read_text(encoding="utf-8")

    assert "DawAgentPage" in source
    assert "isDawAgentSurface" in source
    assert "daw-agent-surface" in source


def test_daw_agent_surface_is_rendered_before_auth_gate_for_plugin_entry():
    source = APP.read_text(encoding="utf-8")

    assert source.index("isDawAgentSurface") < source.index("AuthGate")


def test_daw_agent_surface_skips_auth_loading_and_init_for_plugin_entry():
    source = APP.read_text(encoding="utf-8")

    assert source.index("isDawAgentSurface") < source.index("auth.loading")
    assert "if (isDawAgentSurface.value) return" in source
