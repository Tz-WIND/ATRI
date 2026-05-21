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
        "retrievalResults",
        "knowledgeConfig",
        "toggleSelectedBaseForChat",
        "saveKnowledgeContext",
        "Use in Chat",
    ]
    for symbol in expected_symbols:
        assert symbol in source


def test_knowledge_page_can_collapse_and_scroll_chunks():
    source = _read("frontend/src/components/pages/KnowledgePage.vue")

    assert "if (selectedDocId.value === docId)" in source
    assert "selectedDocId.value = ''" in source
    assert "chunks.value = []" in source
    assert "max-height:" in source
    assert "overflow-y: auto" in source
