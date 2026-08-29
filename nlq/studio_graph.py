from pathlib import Path

from eval.manifest import load_manifest
from nlq.model_client import build_chat_model
from nlq.model_settings import ModelSettings
from nlq.pipeline_settings import load_pipeline_settings
from nlq.schema_context import build_verified_schema_context
from nlq.text_to_sql_graph import build_text_to_sql_graph


PROJECT_ROOT = Path(__file__).parents[1]


def create_studio_graph():
    return _create_studio_graph(PROJECT_ROOT)


def _create_studio_graph(project_root: Path):
    manifest_path = project_root / "eval" / "datasets" / "manifest.json"
    model_settings = ModelSettings.from_environment()
    pipeline_settings = load_pipeline_settings(project_root / "config.toml")
    model = build_chat_model(model_settings)
    schema_context = build_verified_schema_context(
        manifest_path,
        project_root / "nlq" / "schema_dictionary.json",
        project_root=project_root,
    )
    manifest = load_manifest(manifest_path)
    database = (project_root / manifest.database).resolve()
    return build_text_to_sql_graph(
        model=model,
        schema_context=schema_context,
        database=database,
        max_attempts=pipeline_settings.max_attempts,
    )
