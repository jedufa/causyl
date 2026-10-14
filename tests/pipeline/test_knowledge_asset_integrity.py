from pathlib import Path

from infra.pipeline.knowledge_assets import _load_asset_with_meta


def test_knowledge_directory_yaml_files_are_parseable_and_non_empty() -> None:
    knowledge_dir = Path("infra/pipeline/knowledge")
    yaml_files = sorted(path for path in knowledge_dir.glob("*.yaml") if path.name != "many_files_to_split.yaml")

    assert yaml_files

    for path in yaml_files:
        data, meta = _load_asset_with_meta(path)
        assert meta.get("parse_error") is None, f"{path.name}: {meta}"
        assert data, f"{path.name} loaded empty"


def test_knowledge_directory_no_longer_references_deleted_split_stub_files() -> None:
    knowledge_dir = Path("infra/pipeline/knowledge")
    yaml_files = sorted(path for path in knowledge_dir.glob("*.yaml") if path.name != "many_files_to_split.yaml")

    for path in yaml_files:
        text = path.read_text(encoding="utf-8")
        assert "merged_from:" not in text, path.name
        assert "_2.yaml" not in text, path.name
