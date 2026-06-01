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


def test_settings_page_exposes_graph_knowledge_settings():
    source = _read("frontend/src/components/settings/SettingsPage.vue")
    api_source = _read("frontend/src/composables/useApi.js")

    assert "id: 'graph'" in source
    assert "Graph Knowledge" in source
    assert "form.value.knowledge.graph" in source
    assert "testGraphConnection" in source
    assert "testKnowledgeGraphConnection" in api_source
    assert "Extraction Model" in source
    assert "graphExtractionModelOptions" in source
    assert "graph-extraction-model-field" in source
    assert "Retrieval Depth" in source
    assert "retrieval_depth" in source
    assert "normalizeGraphSources" in source
    assert "graphSourceLocked" in source
    assert "sources.length <= 1" in source
    assert "extraction_model" in source
    assert "extraction_provider" in source


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
