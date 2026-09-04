from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, Connection, make_url


class DatabaseState(StrEnum):
    EMPTY = "empty"
    VERSIONED = "versioned"
    CANONICAL_UNVERSIONED = "canonical_unversioned"
    NONCANONICAL_UNVERSIONED = "noncanonical_unversioned"
    EMPTY_VERSION_TABLE = "empty_version_table"
    UNKNOWN_REVISION = "unknown_revision"
    MULTIPLE_REVISIONS = "multiple_revisions"
    INVALID_VERSION_TABLE = "invalid_version_table"
    INTEGRITY_CHECK_FAILED = "integrity_check_failed"
    CORE_FOREIGN_KEY_VIOLATION = "core_foreign_key_violation"


@dataclass(frozen=True)
class DatabaseInspection:
    state: DatabaseState
    head_revision: str
    current_revisions: tuple[str, ...] = ()
    extra_tables: tuple[str, ...] = ()
    differences: tuple[str, ...] = ()

    @property
    def can_upgrade(self) -> bool:
        return self.state in {DatabaseState.EMPTY, DatabaseState.VERSIONED}

    @property
    def can_adopt(self) -> bool:
        return self.state is DatabaseState.CANONICAL_UNVERSIONED

    @property
    def at_head(self) -> bool:
        return self.current_revisions == (self.head_revision,)

    def as_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["state"] = self.state.value
        result["can_upgrade"] = self.can_upgrade
        result["can_adopt"] = self.can_adopt
        result["at_head"] = self.at_head
        return result


class DatabaseBoundaryError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        inspection: DatabaseInspection | None = None,
    ) -> None:
        super().__init__(message)
        self.inspection = inspection


SqlTokens = tuple[str, ...]


@dataclass(frozen=True, order=True)
class _ColumnFingerprint:
    name: str
    declared_type: SqlTokens
    nullable: bool
    primary_key_position: int
    default: SqlTokens | None
    hidden: int


@dataclass(frozen=True, order=True)
class _IndexColumnFingerprint:
    name: str
    descending: bool
    collation: str


@dataclass(frozen=True, order=True)
class _IndexFingerprint:
    unique: bool
    columns: tuple[_IndexColumnFingerprint, ...]
    predicate: SqlTokens
    expression_definition: SqlTokens


@dataclass(frozen=True, order=True)
class _ForeignKeyFingerprint:
    columns: tuple[str, ...]
    target_table: str
    target_columns: tuple[str, ...]
    on_update: str
    on_delete: str
    match: str


@dataclass(frozen=True)
class _TableFingerprint:
    columns: tuple[_ColumnFingerprint, ...]
    primary_key: tuple[str, ...]
    indexes: tuple[_IndexFingerprint, ...]
    foreign_keys: tuple[_ForeignKeyFingerprint, ...]
    checks: tuple[SqlTokens, ...]
    triggers: tuple[SqlTokens, ...]


@dataclass(frozen=True)
class _SchemaFingerprint:
    tables: tuple[tuple[str, _TableFingerprint], ...]

    def as_dict(self) -> dict[str, _TableFingerprint]:
        return dict(self.tables)


def _alembic_config(
    *,
    database_url: str | None = None,
    connection: Connection | None = None,
) -> Config:
    from woolroom import migration_path

    config = Config()
    config.set_main_option("script_location", str(migration_path()))
    if database_url is not None:
        config.set_main_option("sqlalchemy.url", database_url)
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def migration_revisions() -> tuple[str, ...]:
    scripts = ScriptDirectory.from_config(_alembic_config())
    return tuple(script.revision for script in reversed(tuple(scripts.walk_revisions())))


def migration_head() -> str:
    scripts = ScriptDirectory.from_config(_alembic_config())
    heads = scripts.get_heads()
    if len(heads) != 1:
        raise DatabaseBoundaryError(
            f"the installed Woolroom migration graph must have one head; found {len(heads)}"
        )
    return heads[0]


def inspect_database(database_url: str) -> DatabaseInspection:
    return _inspect_database(database_url)


def _inspect_database(database_url: str | URL) -> DatabaseInspection:
    path = _sqlite_path(database_url)
    head = migration_head()
    if not path.exists():
        return DatabaseInspection(state=DatabaseState.EMPTY, head_revision=head)
    if not path.is_file():
        raise DatabaseBoundaryError("the SQLite database path is not a regular file")

    try:
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            return _inspect_connection(connection, head=head)
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        raise DatabaseBoundaryError("the SQLite database could not be inspected") from exc


def upgrade_database(database_url: str) -> DatabaseInspection:
    return _upgrade_database(database_url)


def upgrade_sqlite_database(path: str | Path) -> DatabaseInspection:
    database_url = URL.create(
        "sqlite+pysqlite",
        database=str(Path(path).expanduser()),
    )
    return _upgrade_database(database_url)


def _upgrade_database(database_url: str | URL) -> DatabaseInspection:
    before = _inspect_database(database_url)
    if not before.can_upgrade:
        raise _refusal("upgrade", before)

    return _mutate_database(
        database_url,
        allowed_states={DatabaseState.EMPTY, DatabaseState.VERSIONED},
        alembic_action=lambda config: command.upgrade(config, "head"),
        action="upgrade",
    )


def adopt_database(database_url: str, *, apply: bool = False) -> DatabaseInspection:
    before = inspect_database(database_url)
    if not before.can_adopt:
        raise _refusal("adoption", before)
    if not apply:
        return before

    return _mutate_database(
        database_url,
        allowed_states={DatabaseState.CANONICAL_UNVERSIONED},
        alembic_action=lambda config: command.stamp(config, "head"),
        action="adoption",
    )


def _mutate_database(
    database_url: str | URL,
    *,
    allowed_states: set[DatabaseState],
    alembic_action: Callable[[Config], None],
    action: str,
) -> DatabaseInspection:
    _sqlite_path(database_url)
    engine = create_engine(_sync_sqlite_url(database_url), future=True)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                raw_connection = connection.connection.driver_connection
                locked = _inspect_connection(raw_connection, head=migration_head())
                if locked.state not in allowed_states:
                    raise _refusal(action, locked)
                alembic_action(_alembic_config(connection=connection))
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
    finally:
        engine.dispose()

    after = _inspect_database(database_url)
    if after.state is not DatabaseState.VERSIONED or not after.at_head:
        raise DatabaseBoundaryError(
            f"database {action} did not reach the installed Woolroom migration head",
            inspection=after,
        )
    return after


def _refusal(action: str, inspection: DatabaseInspection) -> DatabaseBoundaryError:
    if inspection.state is DatabaseState.CANONICAL_UNVERSIONED:
        remedy = "run explicit adoption first (dry-run by default)"
    elif inspection.state is DatabaseState.NONCANONICAL_UNVERSIONED:
        remedy = "the versionless core schema is not the canonical installed Woolroom schema"
    elif inspection.state is DatabaseState.UNKNOWN_REVISION:
        remedy = "the version marker is not owned by this Woolroom distribution"
    elif inspection.state is DatabaseState.MULTIPLE_REVISIONS:
        remedy = "the database has multiple version rows"
    elif inspection.state is DatabaseState.EMPTY_VERSION_TABLE:
        remedy = "the database has an empty version table"
    elif inspection.state is DatabaseState.INVALID_VERSION_TABLE:
        remedy = "the database has an invalid Alembic version table"
    elif inspection.state is DatabaseState.INTEGRITY_CHECK_FAILED:
        remedy = "SQLite quick_check or core foreign-key inspection failed"
    elif inspection.state is DatabaseState.CORE_FOREIGN_KEY_VIOLATION:
        remedy = "a Woolroom core table has foreign-key violations"
    else:
        remedy = f"database state {inspection.state.value!r} is not eligible"
    return DatabaseBoundaryError(f"refusing database {action}: {remedy}", inspection=inspection)


def _sqlite_path(database_url: str | URL) -> Path:
    try:
        parsed = make_url(database_url)
    except Exception as exc:
        raise DatabaseBoundaryError("DATABASE_URL is not a valid SQLAlchemy URL") from exc
    if parsed.get_backend_name() != "sqlite":
        raise DatabaseBoundaryError("Woolroom database adoption currently supports SQLite only")
    if parsed.query.get("uri") or (parsed.database and parsed.database.startswith("file:")):
        raise DatabaseBoundaryError("Woolroom database adoption requires a plain SQLite file URL")
    if not parsed.database or parsed.database == ":memory:":
        raise DatabaseBoundaryError("Woolroom database adoption requires a file-backed SQLite URL")
    return Path(parsed.database).expanduser()


def _sync_sqlite_url(database_url: str | URL) -> URL:
    parsed = make_url(database_url)
    return parsed.set(drivername="sqlite+pysqlite", database=str(_sqlite_path(database_url)))


def _inspect_connection(
    connection: sqlite3.Connection,
    *,
    head: str,
) -> DatabaseInspection:
    known_revisions = frozenset(migration_revisions())
    schema_rows = tuple(
        connection.execute(
            "SELECT type, name, tbl_name FROM sqlite_schema "
            "WHERE name NOT LIKE 'sqlite\\_%' ESCAPE '\\'"
        )
    )
    all_table_names = tuple(
        sorted(
            name
            for object_type, name, _ in schema_rows
            if object_type == "table"
        )
    )
    all_tables_by_folded = {name.casefold(): name for name in all_table_names}
    version_table_name = all_tables_by_folded.get("alembic_version")
    table_names = tuple(
        name for name in all_table_names if name.casefold() != "alembic_version"
    )
    canonical = _canonical_fingerprint(head).as_dict()
    canonical_version_table = canonical.pop("alembic_version")
    canonical_names = frozenset(canonical)
    actual_by_folded = {name.casefold(): name for name in table_names}
    extra_tables = tuple(
        sorted(name for name in table_names if name.casefold() not in canonical_names)
    )

    try:
        quick_check = tuple(str(row[0]).casefold() for row in connection.execute("PRAGMA quick_check"))
    except sqlite3.DatabaseError:
        quick_check = ()
    if quick_check != ("ok",):
        return DatabaseInspection(
            state=DatabaseState.INTEGRITY_CHECK_FAILED,
            head_revision=head,
            extra_tables=extra_tables,
            differences=("SQLite quick_check did not return ok",),
        )

    for folded_name in sorted(canonical_names):
        actual_name = actual_by_folded.get(folded_name)
        if actual_name is None:
            continue
        try:
            violation = connection.execute(
                f"PRAGMA foreign_key_check({_quote_identifier(actual_name)})"
            ).fetchone()
        except sqlite3.DatabaseError:
            return DatabaseInspection(
                state=DatabaseState.INTEGRITY_CHECK_FAILED,
                head_revision=head,
                extra_tables=extra_tables,
                differences=(f"core table {actual_name} foreign keys could not be checked",),
            )
        if violation is not None:
            return DatabaseInspection(
                state=DatabaseState.CORE_FOREIGN_KEY_VIOLATION,
                head_revision=head,
                extra_tables=extra_tables,
                differences=(f"core table {actual_name} has foreign key violations",),
            )

    if version_table_name is not None:
        if _fingerprint_table(connection, version_table_name) != canonical_version_table:
            return DatabaseInspection(
                state=DatabaseState.INVALID_VERSION_TABLE,
                head_revision=head,
                extra_tables=extra_tables,
            )
        try:
            revisions = tuple(
                sorted(
                    str(row[0])
                    for row in connection.execute(
                        f"SELECT version_num FROM {_quote_identifier(version_table_name)}"
                    )
                )
            )
        except sqlite3.DatabaseError:
            return DatabaseInspection(
                state=DatabaseState.INVALID_VERSION_TABLE,
                head_revision=head,
                extra_tables=extra_tables,
            )
        if not revisions:
            state = DatabaseState.EMPTY_VERSION_TABLE
        elif len(revisions) > 1:
            state = DatabaseState.MULTIPLE_REVISIONS
        elif revisions[0] not in known_revisions:
            state = DatabaseState.UNKNOWN_REVISION
        else:
            state = DatabaseState.VERSIONED
        return DatabaseInspection(
            state=state,
            head_revision=head,
            current_revisions=revisions,
            extra_tables=extra_tables,
        )

    if not schema_rows:
        return DatabaseInspection(state=DatabaseState.EMPTY, head_revision=head)

    actual = _fingerprint_schema(connection, table_names=canonical_names).as_dict()
    differences = _schema_differences(canonical, actual, actual_by_folded)
    state = (
        DatabaseState.CANONICAL_UNVERSIONED
        if not differences
        else DatabaseState.NONCANONICAL_UNVERSIONED
    )
    return DatabaseInspection(
        state=state,
        head_revision=head,
        extra_tables=extra_tables,
        differences=differences,
    )


@lru_cache(maxsize=8)
def _canonical_fingerprint(revision: str) -> _SchemaFingerprint:
    with tempfile.TemporaryDirectory(prefix="woolroom-schema-") as directory:
        path = Path(directory) / "canonical.db"
        url = URL.create("sqlite+pysqlite", database=str(path))
        engine = create_engine(url, future=True)
        try:
            with engine.begin() as connection:
                command.upgrade(_alembic_config(connection=connection), revision)
        finally:
            engine.dispose()
        with sqlite3.connect(path) as connection:
            table_names = tuple(
                name
                for (name,) in connection.execute(
                    "SELECT name FROM sqlite_schema WHERE type = 'table' "
                    "AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\'"
                )
            )
            return _fingerprint_schema(connection, table_names=table_names)


def _fingerprint_schema(
    connection: sqlite3.Connection,
    *,
    table_names: Sequence[str],
) -> _SchemaFingerprint:
    available = {
        str(name).casefold(): str(name)
        for (name,) in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table'"
        )
    }
    tables = []
    for requested_name in sorted({name.casefold() for name in table_names}):
        actual_name = available.get(requested_name)
        if actual_name is None:
            continue
        tables.append((requested_name, _fingerprint_table(connection, actual_name)))
    return _SchemaFingerprint(tables=tuple(tables))


def _fingerprint_table(
    connection: sqlite3.Connection,
    table_name: str,
) -> _TableFingerprint:
    quoted_table = _quote_identifier(table_name)
    column_rows = tuple(connection.execute(f"PRAGMA table_xinfo({quoted_table})"))
    columns = tuple(
        sorted(
            _ColumnFingerprint(
                name=str(row[1]).casefold(),
                declared_type=_sql_tokens(str(row[2])),
                nullable=not bool(row[3]),
                default=_default_tokens(row[4]),
                primary_key_position=int(row[5]),
                hidden=int(row[6]),
            )
            for row in column_rows
        )
    )
    primary_key = tuple(
        column.name
        for column in sorted(
            (column for column in columns if column.primary_key_position),
            key=lambda column: column.primary_key_position,
        )
    )

    indexes = []
    for index_row in connection.execute(f"PRAGMA index_list({quoted_table})"):
        index_name = str(index_row[1])
        if str(index_row[3]).casefold() == "pk":
            continue
        quoted_index = _quote_identifier(index_name)
        index_columns = tuple(
            _IndexColumnFingerprint(
                name=(
                    str(row[2]).casefold()
                    if row[2] is not None
                    else f"<expression:{int(row[0])}>"
                ),
                descending=bool(row[3]),
                collation=str(row[4] or "").casefold(),
            )
            for row in connection.execute(f"PRAGMA index_xinfo({quoted_index})")
            if bool(row[5])
        )
        sql_row = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE type = 'index' AND name = ?",
            (index_name,),
        ).fetchone()
        index_sql = str(sql_row[0]) if sql_row and sql_row[0] else ""
        indexes.append(
            _IndexFingerprint(
                unique=bool(index_row[2]),
                columns=index_columns,
                predicate=_index_predicate(index_sql),
                expression_definition=(
                    _index_definition(index_sql)
                    if any(column.name.startswith("<expression:") for column in index_columns)
                    else ()
                ),
            )
        )

    foreign_key_groups: dict[int, list[tuple]] = {}
    for row in connection.execute(f"PRAGMA foreign_key_list({quoted_table})"):
        foreign_key_groups.setdefault(int(row[0]), []).append(row)
    foreign_keys = []
    for rows in foreign_key_groups.values():
        ordered_rows = sorted(rows, key=lambda row: int(row[1]))
        first = ordered_rows[0]
        foreign_keys.append(
            _ForeignKeyFingerprint(
                columns=tuple(str(row[3]).casefold() for row in ordered_rows),
                target_table=str(first[2]).casefold(),
                target_columns=tuple(str(row[4]).casefold() for row in ordered_rows),
                on_update=str(first[5]).casefold(),
                on_delete=str(first[6]).casefold(),
                match=str(first[7]).casefold(),
            )
        )

    table_sql_row = connection.execute(
        "SELECT sql FROM sqlite_schema WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    table_sql = str(table_sql_row[0]) if table_sql_row and table_sql_row[0] else ""
    trigger_sql = tuple(
        sorted(
            _trigger_definition(str(sql))
            for (sql,) in connection.execute(
                "SELECT sql FROM sqlite_schema WHERE type = 'trigger' AND tbl_name = ?",
                (table_name,),
            )
            if sql
        )
    )
    return _TableFingerprint(
        columns=columns,
        primary_key=primary_key,
        indexes=tuple(sorted(indexes)),
        foreign_keys=tuple(sorted(foreign_keys)),
        checks=tuple(sorted(_check_expressions(table_sql))),
        triggers=trigger_sql,
    )


def _schema_differences(
    expected: dict[str, _TableFingerprint],
    actual: dict[str, _TableFingerprint],
    actual_names: dict[str, str],
) -> tuple[str, ...]:
    differences: list[str] = []
    fields = (
        "columns",
        "primary_key",
        "indexes",
        "foreign_keys",
        "checks",
        "triggers",
    )
    for table_name, expected_table in expected.items():
        actual_table = actual.get(table_name)
        if actual_table is None:
            differences.append(f"missing core table: {table_name}")
            continue
        for field in fields:
            if getattr(expected_table, field) != getattr(actual_table, field):
                differences.append(f"core table {actual_names[table_name]} differs: {field}")
    return tuple(differences)


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _default_tokens(value: object) -> SqlTokens | None:
    if value is None:
        return None
    tokens = _sql_tokens(str(value))
    while _tokens_have_outer_parentheses(tokens):
        tokens = tokens[1:-1]
    return tokens


_TOKEN_PATTERN = re.compile(
    r"""
    '(?:''|[^'])*'
    | "(?:""|[^"])*"
    | `(?:``|[^`])*`
    | \[(?:\]\]|[^]])*\]
    | [A-Za-z_][A-Za-z0-9_$]*
    | (?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?
    | <=|>=|<>|!=|==|\|\||<<|>>|->>|->
    | [(),.+\-*/%<>=~&|]
    | [^\s]
    """,
    re.VERBOSE,
)


def _sql_tokens(sql: str) -> SqlTokens:
    tokens = []
    for match in _TOKEN_PATTERN.finditer(sql):
        token = match.group(0)
        if token.startswith("'"):
            value = token[1:-1].replace("''", "'")
            tokens.append(f"string:{value}")
        elif token.startswith('"'):
            tokens.append(token[1:-1].replace('""', '"').casefold())
        elif token.startswith("`"):
            tokens.append(token[1:-1].replace("``", "`").casefold())
        elif token.startswith("["):
            tokens.append(token[1:-1].replace("]]", "]").casefold())
        elif token[0].isalpha() or token[0] == "_":
            tokens.append(token.casefold())
        else:
            tokens.append(token)
    return tuple(tokens)


def _tokens_have_outer_parentheses(tokens: SqlTokens) -> bool:
    if len(tokens) < 2 or tokens[0] != "(" or tokens[-1] != ")":
        return False
    depth = 0
    for index, token in enumerate(tokens):
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
            if depth == 0 and index != len(tokens) - 1:
                return False
    return depth == 0


def _check_expressions(table_sql: str) -> tuple[SqlTokens, ...]:
    tokens = _sql_tokens(table_sql)
    expressions = []
    index = 0
    while index < len(tokens) - 1:
        if tokens[index] != "check" or tokens[index + 1] != "(":
            index += 1
            continue
        depth = 1
        cursor = index + 2
        start = cursor
        while cursor < len(tokens) and depth:
            if tokens[cursor] == "(":
                depth += 1
            elif tokens[cursor] == ")":
                depth -= 1
            cursor += 1
        if depth == 0:
            expressions.append(tokens[start : cursor - 1])
        index = cursor
    return tuple(expressions)


def _index_predicate(index_sql: str) -> SqlTokens:
    tokens = _sql_tokens(index_sql)
    depth = 0
    for index, token in enumerate(tokens):
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
        elif token == "where" and depth == 0:
            return tokens[index + 1 :]
    return ()


def _index_definition(index_sql: str) -> SqlTokens:
    tokens = _sql_tokens(index_sql)
    try:
        on_index = tokens.index("on")
    except ValueError:
        return tokens
    cursor = on_index + 2
    if cursor < len(tokens) and tokens[cursor - 1] == ".":
        cursor += 2
    return tokens[cursor:]


def _trigger_definition(trigger_sql: str) -> SqlTokens:
    tokens = _sql_tokens(trigger_sql)
    try:
        cursor = tokens.index("trigger") + 1
    except ValueError:
        return tokens
    if tokens[cursor : cursor + 3] == ("if", "not", "exists"):
        cursor += 3
    cursor += 1
    return tokens[cursor:]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="woolroom-db",
        description="Inspect, upgrade, or explicitly adopt a Woolroom SQLite schema.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command_name in ("inspect", "upgrade", "adopt"):
        command_parser = subparsers.add_parser(command_name)
        command_parser.add_argument(
            "--database-url",
            help="SQLAlchemy SQLite URL (defaults to DATABASE_URL)",
        )
        if command_name == "adopt":
            command_parser.add_argument(
                "--apply",
                action="store_true",
                help="stamp a canonical unversioned schema; omitted means dry-run",
            )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    database_url = args.database_url or os.environ.get("DATABASE_URL")
    if not database_url:
        print("woolroom-db: DATABASE_URL is required", file=sys.stderr)
        return 2
    try:
        if args.command == "inspect":
            inspection = inspect_database(database_url)
            applied = False
        elif args.command == "upgrade":
            inspection = upgrade_database(database_url)
            applied = True
        else:
            inspection = adopt_database(database_url, apply=args.apply)
            applied = bool(args.apply)
    except DatabaseBoundaryError as exc:
        print(f"woolroom-db: {exc}", file=sys.stderr)
        if exc.inspection is not None:
            print(json.dumps(exc.inspection.as_dict(), sort_keys=True), file=sys.stderr)
        return 2

    payload = inspection.as_dict()
    payload.update(action=args.command, applied=applied)
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
