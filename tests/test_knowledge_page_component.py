from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_app_registers_knowledge_page_navigation():
    source = _read("frontend/src/App.vue")

    assert "KnowledgePage" in source
    assert "components/pages/KnowledgePage.vue" in source
    assert "{ id: 'knowledge', label: 'Knowledge', icon: 'knowledge' }" in source
    assert "knowledge: markRaw(KnowledgePage)" in source


def test_activity_bar_has_knowledge_icon():
    source = _read("frontend/src/components/activity/ActivityBar.vue")

    assert "knowledge:" in source


def test_api_exposes_knowledge_routes():
    source = _read("frontend/src/composables/useApi.js")

    expected_methods = [
        "getKnowledgeBases",
        "createKnowledgeBase",
        "getKnowledgeBase",
        "updateKnowledgeBase",
        "deleteKnowledgeBase",
        "getKnowledgeDocuments",
        "importKnowledgeDocument",
        "uploadKnowledgeDocument",
        "deleteKnowledgeDocument",
        "getKnowledgeChunks",
        "deleteKnowledgeChunk",
        "retrieveKnowledge",
        "getKnowledgeTask",
        "getKnowledgeIndexStatus",
        "rebuildKnowledgeBaseIndexes",
        "rebuildKnowledgeDocumentIndexes",
        "getDocumentSupport",
    ]
    for method in expected_methods:
        assert method in source

    expected_paths = [
        "/api/knowledge/bases",
        "/api/knowledge/bases/${encodeURIComponent(kbId)}",
        "/api/knowledge/bases/${encodeURIComponent(kbId)}/documents",
        "/api/knowledge/bases/${encodeURIComponent(kbId)}/documents/import",
        "/api/knowledge/bases/${encodeURIComponent(kbId)}/documents/upload",
        "/api/knowledge/documents/${encodeURIComponent(docId)}",
        "/api/knowledge/documents/${encodeURIComponent(docId)}/chunks",
        "/api/knowledge/chunks/${encodeURIComponent(chunkId)}",
        "/api/knowledge/retrieve",
        "/api/knowledge/tasks/${encodeURIComponent(taskId)}",
        "/api/knowledge/bases/${encodeURIComponent(kbId)}/indexes",
        "/api/knowledge/bases/${encodeURIComponent(kbId)}/indexes/rebuild",
        "/api/knowledge/documents/${encodeURIComponent(docId)}/indexes/rebuild",
        "/api/knowledge/document-support",
    ]
    for path in expected_paths:
        assert path in source


def test_knowledge_page_supports_complete_workflow():
    source = _read("frontend/src/components/pages/KnowledgePage.vue")

    expected_symbols = [
        'PageHeader title="Knowledge"',
        "activeEmbeddingModels",
        "activeRerankModels",
        "createKnowledgeBase",
        "getKnowledgeDocuments",
        "importKnowledgeDocument",
        "uploadKnowledgeDocument",
        "getKnowledgeChunks",
        "deleteKnowledgeDocument",
        "deleteKnowledgeChunk",
        "retrieveKnowledge",
        "getKnowledgeTask",
        "getSettings",
        "saveSettings",
        "embedding_provider",
        "embedding_model",
        "rerank_provider",
        "rerank_model",
        "chunk_size",
        "chunk_overlap",
        "top_k_dense",
        "top_k_sparse",
        "top_m_final",
        "taskStatus",
        "indexStatus",
        "loadIndexStatus",
        "rebuildSelectedBaseIndexes",
        "rebuildDocumentIndexes",
        "Index Status",
        "Rebuild Base",
        "Rebuild Index",
        "source_missing",
        "untracked",
        "retrievalResults",
        "knowledgeConfig",
        "toggleSelectedBaseForChat",
        "saveKnowledgeContext",
        "Use in Chat",
        "useDocumentSupport",
        "await loadDocumentSupport()",
        ':accept="documentAccept"',
    ]
    for symbol in expected_symbols:
        assert symbol in source

    assert ".pdf,.docx,.pptx,.xlsx" not in source


def test_document_support_composable_uses_backend_endpoint():
    source = _read("frontend/src/composables/useDocumentSupport.js")

    assert "getDocumentSupport" in source
    assert "documentAccept" in source
    assert ".pdf,.docx,.pptx,.xlsx" not in source


def test_knowledge_page_can_collapse_and_scroll_chunks():
    source = _read("frontend/src/components/pages/KnowledgePage.vue")

    assert "if (selectedDocId.value === docId)" in source
    assert "selectedDocId.value = ''" in source
    assert "chunks.value = []" in source
    assert "max-height:" in source
    assert "overflow-y: auto" in source


def test_knowledge_page_uploads_selected_files_as_capped_queue():
    source = _read("frontend/src/components/pages/KnowledgePage.vue")
    ref_index = source.index('ref="fileInput"')
    input_start = source.rindex("<input", 0, ref_index)
    input_end = source.index(">", ref_index)
    file_input = source[input_start:input_end]

    assert '@change="onFileSelected"' in file_input
    assert "multiple" in file_input
    assert "const MAX_KNOWLEDGE_UPLOAD_FILES = 1000" in source
    assert "Array.from(event.target.files || [])" in source
    assert ".slice(0, MAX_KNOWLEDGE_UPLOAD_FILES)" in source
    assert "for (const file of queuedFiles)" in source
    assert "failedUploads.push" in source
    assert "continue" in source
    assert "files?.[0]" not in source


def test_knowledge_page_upload_queue_uses_initially_selected_base():
    source = _read("frontend/src/components/pages/KnowledgePage.vue")
    function_start = source.index("async function onFileSelected")
    function_end = source.index("\nasync function removeDocument", function_start)
    body = source[function_start:function_end]

    assert "const uploadKbId = selectedKb.value.kb_id" in body
    capture_index = body.index("const uploadKbId = selectedKb.value.kb_id")
    loop_index = body.index("for (const file of queuedFiles)")

    assert capture_index < loop_index
    assert "api.uploadKnowledgeDocument(uploadKbId, file)" in body
    assert "api.uploadKnowledgeDocument(selectedKb.value.kb_id" not in body
