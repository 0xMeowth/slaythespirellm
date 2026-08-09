import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

ANALYTICAL_TABLES = (
    "cards",
    "relics",
    "runs",
    "run_cards",
    "run_relics",
    "run_card_choices",
)


def create_snapshot(source: Path, output: Path) -> dict[str, int]:
    """Copy only analytical tables and indexes into a new SQLite database."""
    if output.exists():
        raise FileExistsError(f"snapshot already exists: {output}")

    table_sql, index_sql = _load_schema(source)
    temporary = output.with_suffix(output.suffix + ".tmp")
    if temporary.exists():
        raise FileExistsError(f"temporary snapshot already exists: {temporary}")

    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        temporary_uri = f"file:{temporary.resolve()}?mode=rwc"
        with sqlite3.connect(temporary_uri, uri=True) as connection:
            connection.execute("PRAGMA journal_mode = OFF")
            connection.execute("PRAGMA synchronous = OFF")
            for statement in table_sql:
                connection.execute(statement)
            source_uri = f"file:{source.resolve()}?mode=ro"
            connection.execute("ATTACH DATABASE ? AS source", (source_uri,))
            for table in ANALYTICAL_TABLES:
                logger.info("Copying table: %s", table)
                connection.execute(
                    f'INSERT INTO "{table}" SELECT * FROM source."{table}"'
                )
            connection.commit()
            connection.execute("DETACH DATABASE source")
            logger.info("Creating %d indexes", len(index_sql))
            for statement in index_sql:
                connection.execute(statement)
            logger.info("Analyzing snapshot")
            connection.execute("ANALYZE")
            counts = {
                table: connection.execute(
                    f'SELECT COUNT(*) FROM "{table}"'
                ).fetchone()[0]
                for table in ANALYTICAL_TABLES
            }
        temporary.replace(output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return counts


def _load_schema(source: Path) -> tuple[list[str], list[str]]:
    uri = f"file:{source.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        table_rows = connection.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type = 'table' AND name IN (?, ?, ?, ?, ?, ?)",
            ANALYTICAL_TABLES,
        ).fetchall()
        found = {name for name, _ in table_rows}
        missing = set(ANALYTICAL_TABLES) - found
        if missing:
            names = ", ".join(sorted(missing))
            raise ValueError(f"missing analytical tables: {names}")
        table_by_name = {name: sql for name, sql in table_rows}
        indexes = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'index' AND tbl_name IN (?, ?, ?, ?, ?, ?) "
            "AND sql IS NOT NULL ORDER BY name",
            ANALYTICAL_TABLES,
        ).fetchall()
    return (
        [table_by_name[table] for table in ANALYTICAL_TABLES],
        [row[0] for row in indexes],
    )
