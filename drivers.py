"""
drivers.py — Unified database driver abstraction.

Supports: MySQL, PostgreSQL, SQLite, SQL Server.
All drivers expose the same interface so sync.py and db_local.py
never need to know which engine they are talking to.
"""
import sqlite3
from abc import ABC, abstractmethod


# ── Base Driver ───────────────────────────────────────────────────────────────

class BaseDriver(ABC):
    """Common interface every driver must implement."""

    def __init__(self, cfg: dict):
        self.cfg  = cfg
        self.conn = None

    @abstractmethod
    def connect(self): ...

    @abstractmethod
    def is_alive(self) -> bool: ...

    def cursor(self, dictionary: bool = False):
        return self.conn.cursor()

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        try:
            if self.conn:
                self.conn.close()
        except Exception:
            pass

    # ── Schema discovery ──────────────────────────────────────────────────────

    @abstractmethod
    def list_tables(self) -> list[str]: ...

    @abstractmethod
    def list_columns(self, table: str) -> list[str]: ...

    @abstractmethod
    def get_primary_key(self, table: str) -> str | None: ...

    @abstractmethod
    def get_create_ddl(self, table: str) -> str: ...

    # ── SQL dialect helpers ───────────────────────────────────────────────────

    @abstractmethod
    def placeholder(self) -> str:
        """Parameter placeholder: %s for MySQL/PG, ? for SQLite/MSSQL."""
        ...

    @abstractmethod
    def quote(self, name: str) -> str:
        """Quote an identifier: `name` MySQL, "name" PG/SQLite, [name] MSSQL."""
        ...

    def q(self, name: str) -> str:
        return self.quote(name)

    def ph(self) -> str:
        return self.placeholder()

    def upsert_sql(self, table: str, columns: list[str], pk: str) -> str:
        """Build an upsert SQL statement for this engine."""
        raise NotImplementedError(f"upsert_sql not implemented for {type(self).__name__}")

    def get_sync_tables(self, exclude: set = None) -> dict[str, str]:
        """Return {table: pk_col} for all tables with a single-column PK."""
        exclude = exclude or {"offline_queue"}
        result  = {}
        for table in self.list_tables():
            if table in exclude:
                continue
            pk = self.get_primary_key(table)
            if pk:
                result[table] = pk
        return result


# ── MySQL Driver ──────────────────────────────────────────────────────────────

class MySQLDriver(BaseDriver):
    def connect(self):
        import mysql.connector
        timeout = self.cfg.get("timeout", 10)
        c = self.cfg.copy()
        c.pop("engine", None)
        c.pop("timeout", None)
        self.conn = mysql.connector.connect(**c, connection_timeout=timeout)
        return self

    def is_alive(self) -> bool:
        try:
            self.conn.ping(reconnect=True, attempts=2, delay=1)
            return True
        except Exception:
            return False

    def cursor(self, dictionary: bool = False):
        return self.conn.cursor(dictionary=dictionary)

    def list_tables(self) -> list[str]:
        cur = self.conn.cursor()
        cur.execute("SHOW TABLES")
        tables = [r[0] for r in cur.fetchall()]
        cur.close()
        return tables

    def list_columns(self, table: str) -> list[str]:
        cur = self.conn.cursor()
        cur.execute(f"SHOW COLUMNS FROM `{table}`")
        cols = [r[0] for r in cur.fetchall()]
        cur.close()
        return cols

    def get_primary_key(self, table: str) -> str | None:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
            "AND CONSTRAINT_NAME = 'PRIMARY' AND ORDINAL_POSITION = 1",
            (table,),
        )
        row = cur.fetchone()
        cur.close()
        return row[0] if row else None

    def get_create_ddl(self, table: str) -> str:
        cur = self.conn.cursor()
        cur.execute(f"SHOW CREATE TABLE `{table}`")
        ddl = cur.fetchone()[1]
        cur.close()
        return ddl

    def placeholder(self) -> str:
        return "%s"

    def quote(self, name: str) -> str:
        return f"`{name}`"

    def upsert_sql(self, table: str, columns: list[str], pk: str) -> str:
        cols    = ", ".join(f"`{c}`" for c in columns)
        vals    = ", ".join(["%s"] * len(columns))
        updates = ", ".join(f"`{c}`=VALUES(`{c}`)" for c in columns)
        return f"INSERT INTO `{table}` ({cols}) VALUES ({vals}) ON DUPLICATE KEY UPDATE {updates}"


# ── PostgreSQL Driver ─────────────────────────────────────────────────────────

class PostgreSQLDriver(BaseDriver):
    def connect(self):
        import psycopg2
        import psycopg2.extras
        c = self.cfg.copy()
        c.pop("engine", None)
        c.pop("timeout", None)
        self.conn = psycopg2.connect(
            host=c["host"], port=c.get("port", 5432),
            user=c["user"], password=c["password"],
            dbname=c["database"],
            connect_timeout=self.cfg.get("timeout", 10),
        )
        self.conn.autocommit = False
        return self

    def is_alive(self) -> bool:
        try:
            cur = self.conn.cursor()
            cur.execute("SELECT 1")
            cur.close()
            return True
        except Exception:
            return False

    def list_tables(self) -> list[str]:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
        )
        tables = [r[0] for r in cur.fetchall()]
        cur.close()
        return tables

    def list_columns(self, table: str) -> list[str]:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
            (table,),
        )
        cols = [r[0] for r in cur.fetchall()]
        cur.close()
        return cols

    def get_primary_key(self, table: str) -> str | None:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT kcu.column_name FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "  ON tc.constraint_name = kcu.constraint_name "
            "WHERE tc.constraint_type='PRIMARY KEY' AND tc.table_name=%s "
            "  AND kcu.ordinal_position=1",
            (table,),
        )
        row = cur.fetchone()
        cur.close()
        return row[0] if row else None

    def get_create_ddl(self, table: str) -> str:
        # PostgreSQL has no SHOW CREATE TABLE — build minimal DDL
        cols = []
        cur  = self.conn.cursor()
        cur.execute(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
            (table,),
        )
        for col, dtype, nullable, default in cur.fetchall():
            line = f'  "{col}" {dtype}'
            if default:
                line += f" DEFAULT {default}"
            if nullable == "NO":
                line += " NOT NULL"
            cols.append(line)
        pk = self.get_primary_key(table)
        if pk:
            cols.append(f'  PRIMARY KEY ("{pk}")')
        cur.close()
        return f'CREATE TABLE IF NOT EXISTS "{table}" (\n' + ",\n".join(cols) + "\n)"

    def placeholder(self) -> str:
        return "%s"

    def quote(self, name: str) -> str:
        return f'"{name}"'

    def upsert_sql(self, table: str, columns: list[str], pk: str) -> str:
        cols    = ", ".join(f'"{c}"' for c in columns)
        vals    = ", ".join(["%s"] * len(columns))
        updates = ", ".join(f'"{c}"=EXCLUDED."{c}"' for c in columns if c != pk)
        return (
            f'INSERT INTO "{table}" ({cols}) VALUES ({vals}) '
            f'ON CONFLICT ("{pk}") DO UPDATE SET {updates}'
        )


# ── SQLite Driver ─────────────────────────────────────────────────────────────

class SQLiteDriver(BaseDriver):
    def connect(self):
        path = self.cfg.get("database", ":memory:")
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        return self

    def is_alive(self) -> bool:
        try:
            self.conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    def list_tables(self) -> list[str]:
        cur = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        return [r[0] for r in cur.fetchall()]

    def list_columns(self, table: str) -> list[str]:
        cur = self.conn.execute(f"PRAGMA table_info(\"{table}\")")
        return [r[1] for r in cur.fetchall()]

    def get_primary_key(self, table: str) -> str | None:
        cur = self.conn.execute(f"PRAGMA table_info(\"{table}\")")
        for row in cur.fetchall():
            if row[5] == 1:   # pk flag
                return row[1]
        return None

    def get_create_ddl(self, table: str) -> str:
        cur = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        )
        row = cur.fetchone()
        return row[0] if row else ""

    def placeholder(self) -> str:
        return "?"

    def quote(self, name: str) -> str:
        return f'"{name}"'

    def upsert_sql(self, table: str, columns: list[str], pk: str) -> str:
        cols = ", ".join(f'"{c}"' for c in columns)
        vals = ", ".join(["?"] * len(columns))
        return f'INSERT OR REPLACE INTO "{table}" ({cols}) VALUES ({vals})'


# ── SQL Server Driver ─────────────────────────────────────────────────────────

class SQLServerDriver(BaseDriver):
    def connect(self):
        import pyodbc
        c = self.cfg
        dsn = (
            f"DRIVER={{ODBC Driver 17 for SQL Server}};"
            f"SERVER={c['host']},{c.get('port', 1433)};"
            f"DATABASE={c['database']};"
            f"UID={c['user']};PWD={c['password']};"
            f"Connection Timeout={c.get('timeout', 10)}"
        )
        self.conn = pyodbc.connect(dsn)
        self.conn.autocommit = False
        return self

    def is_alive(self) -> bool:
        try:
            self.conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    def list_tables(self) -> list[str]:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME"
        )
        return [r[0] for r in cur.fetchall()]

    def list_columns(self, table: str) -> list[str]:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME=? ORDER BY ORDINAL_POSITION",
            (table,),
        )
        return [r[0] for r in cur.fetchall()]

    def get_primary_key(self, table: str) -> str | None:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT kcu.COLUMN_NAME FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc "
            "JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu "
            "  ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME "
            "WHERE tc.CONSTRAINT_TYPE='PRIMARY KEY' AND tc.TABLE_NAME=? "
            "  AND kcu.ORDINAL_POSITION=1",
            (table,),
        )
        row = cur.fetchone()
        return row[0] if row else None

    def get_create_ddl(self, table: str) -> str:
        cols = []
        cur  = self.conn.cursor()
        cur.execute(
            "SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT "
            "FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME=? ORDER BY ORDINAL_POSITION",
            (table,),
        )
        for col, dtype, nullable, default in cur.fetchall():
            line = f"  [{col}] {dtype}"
            if default:
                line += f" DEFAULT {default}"
            if nullable == "NO":
                line += " NOT NULL"
            cols.append(line)
        pk = self.get_primary_key(table)
        if pk:
            cols.append(f"  PRIMARY KEY ([{pk}])")
        cur.close()
        return f"IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='{table}') " \
               f"CREATE TABLE [{table}] (\n" + ",\n".join(cols) + "\n)"

    def placeholder(self) -> str:
        return "?"

    def quote(self, name: str) -> str:
        return f"[{name}]"

    def upsert_sql(self, table: str, columns: list[str], pk: str) -> str:
        cols    = ", ".join(f"[{c}]" for c in columns)
        vals    = ", ".join(["?"] * len(columns))
        updates = ", ".join(f"target.[{c}]=source.[{c}]" for c in columns if c != pk)
        src     = ", ".join(f"source.[{c}]" for c in columns)
        return (
            f"MERGE [{table}] AS target "
            f"USING (VALUES ({vals})) AS source ({cols}) "
            f"ON target.[{pk}] = source.[{pk}] "
            f"WHEN MATCHED THEN UPDATE SET {updates} "
            f"WHEN NOT MATCHED THEN INSERT ({cols}) VALUES ({src});"
        )


# ── Factory ───────────────────────────────────────────────────────────────────

_DRIVERS = {
    "mysql":      MySQLDriver,
    "postgresql": PostgreSQLDriver,
    "sqlite":     SQLiteDriver,
    "sqlserver":  SQLServerDriver,
}


def get_driver(cfg: dict) -> BaseDriver:
    """
    Create and connect a driver based on cfg['engine'].
    cfg must contain: engine, host, port, user, password, database (+ timeout optional)
    SQLite only needs: engine, database (file path)
    """
    engine = cfg.get("engine", "mysql").lower()
    cls    = _DRIVERS.get(engine)
    if not cls:
        raise ValueError(
            f"Unsupported engine '{engine}'. Choose from: {', '.join(_DRIVERS)}"
        )
    return cls(cfg).connect()
