"""
db_local.py — Prepare any local database for offline sync.

Supports: MySQL, PostgreSQL, SQLite, SQL Server.

Adds sync infrastructure:
  - sync_status column on every table  (MySQL / PostgreSQL / SQLite / SQL Server)
  - INSERT / UPDATE / DELETE triggers  (MySQL only — others use app-level tracking)
  - offline_queue table for DELETE tracking

Usage:
    python db_local.py                    # use default .env
    python db_local.py --env .env.other   # use a specific .env file
    python db_local.py --sql dump.sql     # load SQL dump first (MySQL only)
"""
import sys
import mysql.connector
from mysql.connector import errorcode
from config import get_db_config
from drivers import get_driver


# ── offline_queue DDL per engine ──────────────────────────────────────────────

_QUEUE_DDL = {
    "mysql": """
        CREATE TABLE IF NOT EXISTS `offline_queue` (
            `id`         BIGINT NOT NULL AUTO_INCREMENT,
            `op`         VARCHAR(10) NOT NULL DEFAULT 'DELETE',
            `tbl`        VARCHAR(100) NOT NULL,
            `pk_col`     VARCHAR(100) NOT NULL,
            `pk_val`     VARCHAR(200) NOT NULL,
            `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `synced`     TINYINT(1) NOT NULL DEFAULT 0,
            PRIMARY KEY (`id`),
            INDEX `idx_synced` (`synced`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8
    """,
    "postgresql": """
        CREATE TABLE IF NOT EXISTS "offline_queue" (
            "id"         BIGSERIAL PRIMARY KEY,
            "op"         VARCHAR(10) NOT NULL DEFAULT 'DELETE',
            "tbl"        VARCHAR(100) NOT NULL,
            "pk_col"     VARCHAR(100) NOT NULL,
            "pk_val"     VARCHAR(200) NOT NULL,
            "created_at" TIMESTAMP NOT NULL DEFAULT NOW(),
            "synced"     SMALLINT NOT NULL DEFAULT 0
        )
    """,
    "sqlite": """
        CREATE TABLE IF NOT EXISTS "offline_queue" (
            "id"         INTEGER PRIMARY KEY AUTOINCREMENT,
            "op"         TEXT NOT NULL DEFAULT 'DELETE',
            "tbl"        TEXT NOT NULL,
            "pk_col"     TEXT NOT NULL,
            "pk_val"     TEXT NOT NULL,
            "created_at" TEXT NOT NULL DEFAULT (datetime('now')),
            "synced"     INTEGER NOT NULL DEFAULT 0
        )
    """,
    "sqlserver": """
        IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='offline_queue')
        CREATE TABLE [offline_queue] (
            [id]         BIGINT IDENTITY(1,1) PRIMARY KEY,
            [op]         VARCHAR(10) NOT NULL DEFAULT 'DELETE',
            [tbl]        VARCHAR(100) NOT NULL,
            [pk_col]     VARCHAR(100) NOT NULL,
            [pk_val]     VARCHAR(200) NOT NULL,
            [created_at] DATETIME NOT NULL DEFAULT GETDATE(),
            [synced]     TINYINT NOT NULL DEFAULT 0
        )
    """,
}


# ── sync_status column ────────────────────────────────────────────────────────

def _add_sync_column(driver, table: str):
    q   = driver.q
    cur = driver.cursor()
    try:
        if driver.cfg["engine"] == "mysql":
            cur.execute(
                f"ALTER TABLE {q(table)} ADD COLUMN {q('sync_status')} "
                "TINYINT(1) NOT NULL DEFAULT 0 COMMENT '0=pending,1=synced'"
            )
        elif driver.cfg["engine"] == "postgresql":
            cur.execute(
                f"ALTER TABLE {q(table)} ADD COLUMN IF NOT EXISTS "
                f"{q('sync_status')} SMALLINT NOT NULL DEFAULT 0"
            )
        elif driver.cfg["engine"] == "sqlite":
            cur.execute(
                f"ALTER TABLE {q(table)} ADD COLUMN {q('sync_status')} "
                "INTEGER NOT NULL DEFAULT 0"
            )
        elif driver.cfg["engine"] == "sqlserver":
            cur.execute(
                f"IF NOT EXISTS (SELECT * FROM sys.columns "
                f"WHERE object_id=OBJECT_ID('{table}') AND name='sync_status') "
                f"ALTER TABLE {q(table)} ADD {q('sync_status')} TINYINT NOT NULL DEFAULT 0"
            )
        driver.commit()
    except Exception as e:
        msg = str(e)
        # Ignore "column already exists" errors from all engines
        if not any(x in msg.lower() for x in
                   ["duplicate", "already exists", "1060", "dup_fieldname"]):
            print(f"  [WARN] sync_status on {table}: {e}")
        driver.rollback()
    finally:
        cur.close()


# ── MySQL triggers ────────────────────────────────────────────────────────────

def _add_mysql_triggers(driver, table: str, pk_col: str):
    """MySQL supports triggers natively — use them for automatic tracking."""
    cur = driver.cursor()

    ins = f"trg_{table}_ins_sync"
    cur.execute(f"DROP TRIGGER IF EXISTS `{ins}`")
    cur.execute(
        f"CREATE TRIGGER `{ins}` BEFORE INSERT ON `{table}` "
        f"FOR EACH ROW SET NEW.sync_status = 0"
    )

    upd = f"trg_{table}_upd_sync"
    cur.execute(f"DROP TRIGGER IF EXISTS `{upd}`")
    cur.execute(
        f"CREATE TRIGGER `{upd}` BEFORE UPDATE ON `{table}` FOR EACH ROW "
        f"BEGIN "
        f"  IF NOT (NEW.sync_status = 1 AND OLD.sync_status = 0) THEN "
        f"    SET NEW.sync_status = 0; "
        f"  END IF; "
        f"END"
    )

    dlt = f"trg_{table}_del_queue"
    cur.execute(f"DROP TRIGGER IF EXISTS `{dlt}`")
    cur.execute(
        f"CREATE TRIGGER `{dlt}` AFTER DELETE ON `{table}` "
        f"FOR EACH ROW "
        f"INSERT INTO `offline_queue` (op, tbl, pk_col, pk_val) "
        f"VALUES ('DELETE', '{table}', '{pk_col}', OLD.`{pk_col}`)"
    )

    driver.commit()
    cur.close()


# ── Load SQL dump (MySQL only) ────────────────────────────────────────────────

def _load_sql_dump(local_cfg: dict, sql_file: str):
    print(f"📂 Loading SQL dump: {sql_file}")
    with open(sql_file, encoding="utf-8") as f:
        sql = f.read()

    c = local_cfg.copy()
    c.pop("engine", None); c.pop("timeout", None); db = c.pop("database")
    conn = mysql.connector.connect(**c)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(f"CREATE DATABASE IF NOT EXISTS `{db}`")
    cur.execute(f"USE `{db}`")
    for stmt in (s.strip() for s in sql.split(";") if s.strip()):
        try:
            cur.execute(stmt)
        except mysql.connector.Error as e:
            if e.errno not in (1007, errorcode.ER_TABLE_EXISTS_ERROR, errorcode.ER_DUP_ENTRY):
                print(f"  [WARN] {e.msg}")
    cur.close()
    conn.close()
    print("  ✔ SQL dump loaded.")


# ── Main setup ────────────────────────────────────────────────────────────────

def setup(local_cfg: dict, sql_file: str = None):
    engine = local_cfg["engine"]

    if sql_file:
        if engine != "mysql":
            print(f"[WARN] --sql dump loading only supported for MySQL, skipping.")
        else:
            _load_sql_dump(local_cfg, sql_file)

    driver = get_driver(local_cfg)
    cur    = driver.cursor()

    # Create offline_queue
    cur.execute(_QUEUE_DDL[engine])
    driver.commit()
    cur.close()

    sync_tables = driver.get_sync_tables()
    db_name     = local_cfg["database"]

    print(f"\n🗄  Engine   : {engine}")
    print(f"🗄  Database : {db_name}")
    print(f"📋 Tables   : {len(sync_tables)} found\n")

    for table, pk in sync_tables.items():
        _add_sync_column(driver, table)
        if engine == "mysql":
            _add_mysql_triggers(driver, table, pk)
        print(f"   ✔ {table:<45} pk={pk}")

    driver.close()

    if engine != "mysql":
        print(
            f"\n💡 [{engine}] Triggers not supported — your app must:\n"
            "   • Set sync_status=0 on INSERT/UPDATE\n"
            "   • Insert into offline_queue on DELETE"
        )

    print(f"\n✅ [{db_name}] ready for sync.")


if __name__ == "__main__":
    args     = sys.argv[1:]
    env_file = args[args.index("--env") + 1] if "--env" in args else None
    sql_file = args[args.index("--sql") + 1] if "--sql" in args else None
    local_cfg, _ = get_db_config(env_file)
    setup(local_cfg, sql_file)
