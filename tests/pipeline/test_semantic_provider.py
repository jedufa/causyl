from __future__ import annotations

from infra.pipeline.semantic import OllamaSemanticProvider


def test_ollama_provider_chunks_and_falls_back_per_text(monkeypatch) -> None:
    provider = OllamaSemanticProvider(base_url="http://example.invalid:11434")

    def fake_post_embed(base_url: str, payload_obj: dict, *, timeout_seconds: int):
        item = payload_obj["input"]
        if isinstance(item, list):
            raise RuntimeError("batch rejected")
        if item == "bad":
            raise RuntimeError("single text rejected")
        return {"embedding": [1.0, 0.0]}

    monkeypatch.setattr(provider, "_post_embed", fake_post_embed)
    vectors = provider.embed_texts(["ok one", "bad", "ok two"], model_name="all-minilm", timeout_seconds=1)

    assert len(vectors) == 3
    assert vectors[0]
    assert not vectors[1]
    assert vectors[2]
    assert provider.last_embed_stats["mode"] == "chunked_batch_with_fallback"
    assert provider.last_embed_stats["failed_count"] == 1
    assert provider.last_embed_stats["fallback_chunk_count"] >= 1
