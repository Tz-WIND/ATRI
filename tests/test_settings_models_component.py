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
