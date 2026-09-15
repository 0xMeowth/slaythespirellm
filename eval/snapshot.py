import logging
from pathlib import Path

import duckdb


logger = logging.getLogger(__name__)

ANALYTICAL_TABLES = (
    "cards",
    "relics",
    "runs",
    "run_cards",
    "run_relics",
    "run_card_choices",
)


def create_snapshot(
    source: Path,
    output: Path,
    schema: Path,
    link: Path | None = None,
) -> dict[str, int]:
    """Create and publish a native DuckDB analytical snapshot."""
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"snapshot already exists: {output}")

    temporary = output.with_suffix(output.suffix + ".tmp")
    if temporary.exists() or temporary.is_symlink():
        raise FileExistsError(f"temporary snapshot already exists: {temporary}")
    if link is not None and link.resolve(strict=False) == output.resolve(strict=False):
        raise ValueError("snapshot link must differ from output")

    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with duckdb.connect(str(temporary)) as connection:
            _apply_schema(connection, schema)
            _attach_sqlite_source(connection, source)
            _copy_approved_tables(connection)
            counts = _verify_snapshot(connection)
            connection.execute("DETACH source_sqlite")
            connection.execute("CHECKPOINT")
        temporary.replace(output)
        if link is not None:
            _replace_link(link, output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return counts


def apply_duckdb_schema(database: Path, schema: Path) -> None:
    database.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(database)) as connection:
        _apply_schema(connection, schema)


def _apply_schema(connection, schema: Path) -> None:
    connection.execute(schema.read_text())


def _attach_sqlite_source(connection, source: Path) -> None:
    source_literal = _sql_string_literal(source.resolve())
    connection.execute("INSTALL sqlite")
    connection.execute("LOAD sqlite")
    connection.execute(
        f"ATTACH {source_literal} AS source_sqlite (TYPE sqlite, READ_ONLY)"
    )


def _copy_approved_tables(connection) -> None:
    source_tables = {
        row[0]
        for row in connection.execute(
            "SELECT table_name FROM duckdb_tables() "
            "WHERE database_name = 'source_sqlite'"
        ).fetchall()
    }
    missing = set(ANALYTICAL_TABLES) - source_tables
    if missing:
        names = ", ".join(sorted(missing))
        raise ValueError(f"missing analytical tables: {names}")

    for table in ANALYTICAL_TABLES:
        logger.info("Copying table: %s", table)
        connection.execute(
            f'INSERT INTO "{table}" SELECT * FROM source_sqlite."{table}"'
        )


def _verify_snapshot(connection) -> dict[str, int]:
    target_tables = {
        row[0] for row in connection.execute("SHOW TABLES").fetchall()
    }
    expected_tables = set(ANALYTICAL_TABLES)
    if target_tables != expected_tables:
        missing = expected_tables - target_tables
        unexpected = target_tables - expected_tables
        details = []
        if missing:
            details.append(f"missing: {', '.join(sorted(missing))}")
        if unexpected:
            details.append(f"unexpected: {', '.join(sorted(unexpected))}")
        raise ValueError(f"invalid analytical tables ({'; '.join(details)})")
    return {
        table: int(
            connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        )
        for table in ANALYTICAL_TABLES
    }


def _replace_link(link: Path, output: Path) -> None:
    if link.exists() and not link.is_symlink():
        raise FileExistsError(f"snapshot link path is not a symlink: {link}")
    temporary_link = link.with_name(link.name + ".tmp")
    if temporary_link.exists() or temporary_link.is_symlink():
        raise FileExistsError(f"temporary snapshot link already exists: {temporary_link}")

    link.parent.mkdir(parents=True, exist_ok=True)
    temporary_link.symlink_to(output.resolve())
    try:
        temporary_link.replace(link)
    except Exception:
        temporary_link.unlink(missing_ok=True)
        raise


def _sql_string_literal(value: Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"
