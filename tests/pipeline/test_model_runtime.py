from infra.pipeline.model_runtime import infer_agent_tech_from_model, resolve_stage8_runtime


def test_infer_agent_tech_from_model_family():
    assert infer_agent_tech_from_model("gpt-5.3-codex-spark") == "codex"
    assert infer_agent_tech_from_model("gpt-5.4") == "openai"
    assert infer_agent_tech_from_model("gemini-2.5-pro") == "gemini"
    assert infer_agent_tech_from_model("grok-code-fast-1") == "xai"
    assert infer_agent_tech_from_model("deepseek-reasoner") == "deepseek"
    assert infer_agent_tech_from_model("qwen2.5:3b") == "ollama"


def test_resolve_stage8_runtime_defaults_to_gpt54_openai():
    runtime = resolve_stage8_runtime({"_stage": {}, "planning": {}, "models": {}})
    assert runtime["requested_model"] == "gpt-5.4"
    assert runtime["selected_agent_tech"] == "openai"


def test_resolve_stage8_runtime_uses_deepseek_provider_for_deepseek_models():
    runtime = resolve_stage8_runtime(
        {
            "_stage": {"requested_agent_tech": "deepseek", "requested_model": "deepseek-reasoner"},
            "planning": {},
            "models": {"default_remote_provider": "openai"},
        }
    )
    assert runtime["selected_agent_tech"] == "deepseek"
    assert runtime["provider"] == "deepseek"
    assert runtime["selected_model"] == "deepseek-reasoner"


def test_resolve_stage8_runtime_prefers_model_family_over_conflicting_provider_and_agent_tech():
    runtime = resolve_stage8_runtime(
        {
            "_stage": {"requested_agent_tech": "openai", "requested_model": "deepseek-reasoner", "provider": "openai"},
            "planning": {},
            "models": {"default_remote_provider": "openai"},
        }
    )
    assert runtime["selected_agent_tech"] == "deepseek"
    assert runtime["agent_tech"] == "deepseek"
    assert runtime["provider"] == "deepseek"
    assert runtime["selected_via"] == "model_family_inference"


def test_resolve_stage8_runtime_keeps_codex_for_codex_family_models():
    runtime = resolve_stage8_runtime(
        {
            "_stage": {"requested_agent_tech": "codex", "requested_model": "gpt-5.3-codex-spark"},
            "planning": {},
            "models": {"default_remote_provider": "openai"},
        }
    )
    assert runtime["selected_agent_tech"] == "codex"
    assert runtime["provider"] == "openai"
    assert runtime["selected_via"] == "explicit_agent_tech"
