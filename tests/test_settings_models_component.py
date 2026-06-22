from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_settings_models_page_has_embedding_and_rerank_pool_sections():
    source = _read("frontend/src/components/settings/SettingsPage.vue")

    assert "Chat Models" in source
    assert "Embedding Models" in source
    assert "Rerank Models" in source
    assert "activeModelProvider" in source
    assert "activeEmbeddingModels" in source
    assert "activeEmbeddingModel" in source
    assert "activeEmbeddingProvider" in source
    assert "activeRerankModels" in source
    assert "activeRerankModel" in source
    assert "activeRerankProvider" in source
    assert "ModelPoolSection" in source
    assert "Generation Parameters" not in source


def test_settings_page_preserves_knowledge_config_when_saving():
    source = _read("frontend/src/components/settings/SettingsPage.vue")

    assert "knowledge:" in source
    assert "normalizeKnowledge" in source
    assert "form.value.knowledge" in source
    assert "const knowledge = normalizeKnowledge(form.value.knowledge)" in source
    assert "knowledge," in source


def test_settings_page_exposes_vector_knowledge_cache_limit():
    source = _read("frontend/src/components/settings/SettingsPage.vue")

    assert "id: 'retrieval'" in source
    assert "Knowledge Retrieval" in source
    assert "Vector Retrieval" in source
    assert "Embedding Cache Limit" in source
    assert "form.knowledge.embedding_cache_max_size" in source
    assert "normalizeEmbeddingCacheMaxSize" in source
    assert "embedding_cache_max_size: normalizeEmbeddingCacheMaxSize" in source


def test_settings_page_exposes_graph_knowledge_settings():
    source = _read("frontend/src/components/settings/SettingsPage.vue")
    api_source = _read("frontend/src/composables/useApi.js")

    assert "id: 'graph'" in source
    assert "Graph Knowledge" in source
    assert "form.value.knowledge.graph" in source
    assert "testGraphConnection" in source
    assert "testKnowledgeGraphConnection" in api_source
    assert "Graph Query" in source
    assert "graphQueryForm" in source
    assert "runGraphQuery" in source
    assert "retrieveKnowledgeGraph" in api_source
    assert "/api/knowledge/graph/retrieve" in api_source
    assert "Manual Graph Ingest" in source
    assert "manualGraphIngestForm" in source
    assert "runManualGraphIngest" in source
    assert "handleManualGraphFile" in source
    assert "manualGraphIngestFiles" in source
    assert "selectedManualGraphFileLabel" in source
    assert "useDocumentSupport" in source
    assert "await loadDocumentSupport()" in source
    assert ':accept="documentAccept"' in source
    assert ".pdf,.docx,.pptx,.xlsx" not in source
    assert "onManualGraphContentInput" in source
    assert "ingestKnowledgeGraph" in api_source
    assert "/api/knowledge/graph/ingest" in api_source
    assert "getDocumentSupport" in api_source
    assert "/api/knowledge/document-support" in api_source
    assert "Source Name" not in source
    assert "graph-query-result" in source
    assert "graphQueryDiagnostics" in source
    assert "graphQueryDiagnosticItems" in source
    assert "graph-query-diagnostics" in source
    assert "graph-query-metric" in source
    assert "graph_cache_hit" in source
    assert "graph_multihop_cache_hit" in source
    assert "graph_multihop_seed_count" in source
    assert "graph_multihop_cached_seed_count" in source
    assert "graph_multihop_live_seed_limit" in source
    assert "graph_multihop_partial_cache_hit" in source
    assert "graph_multihop_persistent_cache_hit_count" in source
    assert "graph_used_fulltext" in source
    assert "graph_used_scan_fallback" in source
    assert "graph_total_ms" in source
    assert "graph_multi_hop_ms" in source
    assert "result.diagnostics || null" in source
    assert "Extraction Model" in source
    assert "graphExtractionModelOptions" in source
    assert "graph-extraction-model-field" in source
    assert "Retrieval Depth" in source
    assert "retrieval_depth" in source
    assert "7 hops" in source
    assert "Math.min(7" in source
    assert "Expansion Candidates" in source
    assert "expansion_candidate_limit" in source
    assert "graphExpansionCandidateMax" in source
    assert "getConfigSchema" in source
    assert "loadConfigLimits" in source
    assert "Multi-hop Cache" in source
    assert "multi_hop_expansion_cache_mode" in source
    assert "normalizeGraphCacheMode" in source
    assert "Off" in source
    assert "Memory" in source
    assert "Persistent" in source
    assert "Ranking Policy" in source
    assert "ranking_policy" in source
    assert "normalizeGraphRankingPolicy" in source
    assert "ranking_policy: normalizeGraphRankingPolicy" in source
    assert "normalizeGraphSources" in source
    assert "graphSourceLocked" in source
    assert "sources.length <= 1" in source
    assert "extraction_model" in source
    assert "extraction_provider" in source


def test_manual_graph_ingest_supports_batch_file_selection():
    source = _read("frontend/src/components/settings/SettingsPage.vue")

    assert "multiple" in source
    assert "Array.from(event.target.files || [])" in source
    assert "for (const file of files)" in source
    assert "queued.push(result.task_id || file.name)" in source
    assert "Queued ${queued.length} graph ingest task${queued.length === 1 ? '' : 's'}" in source
    assert "manualGraphIngestFiles.value = []" in source


def test_manual_graph_ingest_retains_only_failed_files_for_retry():
    source = _read("frontend/src/components/settings/SettingsPage.vue")

    assert "const failedFiles = []" in source
    assert "failedFiles.push(file)" in source
    assert "manualGraphIngestFiles.value = failedFiles" in source


def test_workspace_and_music_directory_saves_confirm_external_trust():
    workspace_source = _read("frontend/src/components/pages/WorkspacePage.vue")
    settings_source = _read("frontend/src/components/settings/SettingsPage.vue")
    api_source = _read("frontend/src/composables/useApi.js")
    trust_source = _read("frontend/src/composables/directoryTrust.js")

    assert "retryAfterDirectoryTrust" in workspace_source
    assert "trust: true" in workspace_source
    assert "retryAfterDirectoryTrust" in settings_source
    assert "trust: true" in settings_source
    assert "error.body" in api_source
    assert "saveWorkspace: (workspace, options = {})" in api_source
    assert "saveMusicDirs: (directories, options = {})" in api_source
    assert "requires_trust" in trust_source
    assert "directoryTrustMessage" in trust_source


def test_model_pool_section_uses_pool_activation_api():
    source = _read("frontend/src/components/settings/ModelPoolSection.vue")

    assert "activatePoolModel" in source
    assert "deactivatePoolModel" in source
    assert "selectPoolModel" in source
    assert "savePoolModelConfig" in source
    assert "config-modal" in source
    assert "max_context_tokens" in source
    assert "encoding_format" in source
    assert "score_threshold" in source
    assert "providerList" in source
    assert "pool" in source
