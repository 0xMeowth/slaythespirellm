import json
from pathlib import Path

from eval.models import DatasetManifest
from nlq.pipeline_settings import PipelineSettings
from nlq import studio_graph


def test_studio_factory_builds_graph_from_project_configuration(
    tmp_path, monkeypatch
):
    model_settings = object()
    pipeline_settings = PipelineSettings(max_attempts=4)
    model = object()
    schema_context = object()
    compiled_graph = object()
    manifest = DatasetManifest(
        dataset_id="test-dataset",
        database="data/eval.db",
        created_at="2026-08-10T00:00:00+00:00",
        earliest_run_date="2026-07-01",
        latest_run_date="2026-08-01",
        run_count=1,
        database_sha256="database-sha256",
        schema_git_commit="abc123",
    )
    calls = {}

    monkeypatch.setattr(
        studio_graph.ModelSettings,
        "from_environment",
        staticmethod(lambda: model_settings),
    )

    def fake_load_pipeline_settings(path):
        calls["config_path"] = path
        return pipeline_settings

    def fake_build_chat_model(settings):
        calls["model_settings"] = settings
        return model

    def fake_build_schema_context(
        manifest_path, dictionary_path, *, project_root
    ):
        calls["schema_paths"] = (
            manifest_path,
            dictionary_path,
            project_root,
        )
        return schema_context

    def fake_load_manifest(path):
        calls["manifest_path"] = path
        return manifest

    def fake_build_graph(**arguments):
        calls["graph_arguments"] = arguments
        return compiled_graph

    monkeypatch.setattr(
        studio_graph, "load_pipeline_settings", fake_load_pipeline_settings
    )
    monkeypatch.setattr(studio_graph, "build_chat_model", fake_build_chat_model)
    monkeypatch.setattr(
        studio_graph,
        "build_verified_schema_context",
        fake_build_schema_context,
    )
    monkeypatch.setattr(studio_graph, "load_manifest", fake_load_manifest)
    monkeypatch.setattr(studio_graph, "build_text_to_sql_graph", fake_build_graph)

    result = studio_graph._create_studio_graph(tmp_path)

    manifest_path = tmp_path / "eval" / "datasets" / "manifest.json"
    assert result is compiled_graph
    assert calls["config_path"] == tmp_path / "config.toml"
    assert calls["model_settings"] is model_settings
    assert calls["schema_paths"] == (
        manifest_path,
        tmp_path / "nlq" / "schema_dictionary.json",
        tmp_path,
    )
    assert calls["manifest_path"] == manifest_path
    assert calls["graph_arguments"] == {
        "model": model,
        "schema_context": schema_context,
        "database": (tmp_path / "data" / "eval.db").resolve(),
        "max_attempts": 4,
    }


def test_langgraph_configuration_exposes_studio_graph(project_root: Path):
    config = json.loads((project_root / "langgraph.json").read_text())

    assert set(config) == {"dependencies", "graphs", "env"}
    assert config["dependencies"] == ["."]
    assert config["graphs"] == {
        "sts2_text_to_sql": "./nlq/studio_graph.py:create_studio_graph"
    }
    assert config["env"] == ".env"
