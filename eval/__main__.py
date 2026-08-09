import argparse
import json
import logging
import os
import sys
from pathlib import Path

from eval.cases import load_cases
from eval.manifest import (
    create_manifest,
    inspect_runs,
    load_manifest,
    verify_manifest,
    write_manifest,
)
from eval.sqlite_eval import QueryExecutionError, execute_query
from eval.snapshot import ANALYTICAL_TABLES, create_snapshot


def main(arguments: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(arguments)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        args.handler(args)
    except Exception as error:
        if args.debug:
            raise
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m eval")
    parser.add_argument("--debug", action="store_true")
    commands = parser.add_subparsers(required=True)

    snapshot = commands.add_parser("snapshot")
    snapshot_commands = snapshot.add_subparsers(required=True)
    snapshot_create = snapshot_commands.add_parser("create")
    snapshot_create.add_argument("--source", type=Path, required=True)
    snapshot_create.add_argument("--output", type=Path, required=True)
    snapshot_create.set_defaults(handler=_create_snapshot)

    manifest = commands.add_parser("manifest")
    manifest_commands = manifest.add_subparsers(required=True)

    create = manifest_commands.add_parser("create")
    create.add_argument("--database", type=Path, required=True)
    create.add_argument("--dataset-id", required=True)
    create.add_argument("--schema-git-commit", required=True)
    create.add_argument("--output", type=Path, required=True)
    create.set_defaults(handler=_create_manifest)

    verify = manifest_commands.add_parser("verify")
    verify.add_argument("--manifest", type=Path, required=True)
    verify.set_defaults(handler=_verify_manifest)

    cases = commands.add_parser("cases")
    case_commands = cases.add_subparsers(required=True)
    validate = case_commands.add_parser("validate")
    validate.add_argument("--cases", type=Path, required=True)
    validate.add_argument("--manifest", type=Path, required=True)
    validate.add_argument("--timeout-seconds", type=float, default=30.0)
    validate.add_argument("--preview-limit", type=int, default=5)
    validate.set_defaults(handler=_validate_cases)
    return parser


def _create_snapshot(args: argparse.Namespace) -> None:
    counts = create_snapshot(args.source, args.output)
    print(
        f"Created snapshot: {args.output} "
        f"({len(ANALYTICAL_TABLES)} analytical tables)"
    )
    for table, count in counts.items():
        print(f"{table}: {count}")


def _create_manifest(args: argparse.Namespace) -> None:
    project_root = Path.cwd()
    database = _from_project_root(args.database, project_root)
    run_count, earliest, latest = inspect_runs(database)
    manifest = create_manifest(
        dataset_id=args.dataset_id,
        database=database,
        manifest_database_path=_relative_database_path(
            args.database,
            project_root,
        ),
        schema_git_commit=args.schema_git_commit,
        run_count=run_count,
        earliest_run_date=earliest,
        latest_run_date=latest,
    )
    write_manifest(manifest, args.output)
    print(f"Created manifest: {args.output}")


def _verify_manifest(args: argparse.Namespace) -> None:
    manifest = load_manifest(args.manifest)
    verify_manifest(manifest, Path.cwd())
    print(f"Verified dataset: {manifest.dataset_id}")


def _validate_cases(args: argparse.Namespace) -> None:
    manifest = load_manifest(args.manifest)
    database = verify_manifest(manifest, Path.cwd())
    cases = load_cases(args.cases)
    sql_count = 0
    for case in cases:
        if case.expected_route != "sql":
            continue
        sql_count += 1
        try:
            result = execute_query(
                database,
                case.gold_sql,
                args.timeout_seconds,
            )
        except QueryExecutionError as error:
            raise ValueError(f"gold SQL failed for {case.id}: {error}") from error
        preview = [list(row) for row in result.rows[: args.preview_limit]]
        print(f"{case.id}: {json.dumps(preview, ensure_ascii=False)}")
    router_count = len(cases) - sql_count
    print(f"Validated {sql_count} SQL cases and {router_count} router cases")


def _from_project_root(path: Path, project_root: Path) -> Path:
    return path if path.is_absolute() else project_root / path


def _relative_database_path(path: Path, project_root: Path) -> str:
    if path.is_absolute():
        relative = Path(os.path.relpath(path, project_root))
    else:
        relative = path
    if relative == Path("..") or ".." in relative.parts:
        raise ValueError("database must be inside the project root")
    return relative.as_posix()


if __name__ == "__main__":
    raise SystemExit(main())
