"""
sync.py — Bidirectional sync supporting MySQL, PostgreSQL, SQLite, SQL Server.

  PUSH INSERT/UPDATE : local rows (sync_status=0) → remote
  PUSH DELETE        : offline_queue entries       → remote
  PULL               : remote rows                 → local (creates missing tables)

Usage:
    python sync.py --push            # push only  (local → remote)
    python sync.py --pull            # pull only  (remote → local)
    python sync.py --push --watch 30 # push every 30s
    python sync.py --pull --watch 60 # pull every 60s
    python sync.py --env .env.other  # use a different .env file

Note: --push and --pull are mutually exclusive. Use one at a time.
"""
import re
import sys
import time
import logging
from config import get_db_config, SYNC_INTERVAL
from drivers import get_driver, BaseDriver

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("sync.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ── Online check ──────────────────────────────────────────────────────────────

def is_online(remote_cfg: dict) -> bool:
    try:
        d = get_driver(remote_cfg)
        d.close()
        return True
    except Exception:
        return False


# ── Schema helpers ───────────────────────────────────────────────────────────

def _clean_ddl_for_remote(ddl: str, engine: str) -> str:
    """Strip local-only columns (sync_status) from DDL before sending to remote."""
    if engine == "mysql":
        ddl = re.sub(r" AUTO_INCREMENT=\d+", "", ddl)
        ddl = re.sub(r",\s*`sync_status`[^\n]+", "", ddl)
        ddl = re.sub(r"([^,])\n(\s*(?:PRIMARY KEY|KEY))", r"\1,\n\2", ddl)
    return ddl


def _clean_ddl_for_local(ddl: str, remote_engine: str, local_engine: str, table: str) -> str:
    """Convert remote DDL to local engine syntax."""

    if remote_engine == "mysql" and local_engine == "sqlite":
        # Strip MySQL-only clauses
        ddl = re.sub(r" AUTO_INCREMENT=\d+", "", ddl)   # table-level
        ddl = re.sub(r"\bAUTO_INCREMENT\b", "", ddl)    # column-level
        ddl = re.sub(r"\bENGINE=\w+\b", "", ddl)
        ddl = re.sub(r"\bDEFAULT CHARSET=\w+\b", "", ddl)
        ddl = re.sub(r"\bCHARSET=\w+\b", "", ddl)
        ddl = re.sub(r"\bCOLLATE\s+\w+\b", "", ddl)
        ddl = re.sub(r"\bCOMMENT\s+'[^']*'", "", ddl)
        ddl = re.sub(r"\bUNSIGNED\b", "", ddl)
        ddl = re.sub(r"\bZEROFILL\b", "", ddl)
        # Remove KEY / INDEX lines (SQLite doesn't support them inside CREATE TABLE)
        ddl = re.sub(r",?\s*(?:UNIQUE\s+)?KEY\s+`[^`]+`[^\n]+", "", ddl)
        # Convert MySQL types to SQLite types
        ddl = re.sub(r"\bTINYINT\(\d+\)", "INTEGER", ddl)
        ddl = re.sub(r"\bSMALLINT\(\d+\)", "INTEGER", ddl)
        ddl = re.sub(r"\bMEDIUMINT\(\d+\)", "INTEGER", ddl)
        ddl = re.sub(r"\bBIGINT\(\d+\)", "INTEGER", ddl)
        ddl = re.sub(r"\bINT\(\d+\)", "INTEGER", ddl)
        ddl = re.sub(r"\bDOUBLE\b", "REAL", ddl)
        ddl = re.sub(r"\bFLOAT\b", "REAL", ddl)
        ddl = re.sub(r"\bDECIMAL\([^)]+\)", "REAL", ddl)
        ddl = re.sub(r"\bTINYTEXT\b|\bMEDIUMTEXT\b|\bLONGTEXT\b", "TEXT", ddl)
        ddl = re.sub(r"\bVARCHAR\(\d+\)", "TEXT", ddl)
        ddl = re.sub(r"\bCHAR\(\d+\)", "TEXT", ddl)
        ddl = re.sub(r"\bBLOB\b|\bMEDIUMBLOB\b|\bLONGBLOB\b", "BLOB", ddl)
        ddl = re.sub(r"\bDATETIME\b", "TEXT", ddl)
        ddl = re.sub(r"\bTIMESTAMP\b", "TEXT", ddl)
        ddl = re.sub(r"\bDATE\b", "TEXT", ddl)
        ddl = re.sub(r"\bTIME\b", "TEXT", ddl)
        # Replace backtick quotes with double quotes
        ddl = ddl.replace("`", '"')
        # Fix trailing commas before closing paren left by removed KEY lines
        ddl = re.sub(r",\s*\)", "\n)", ddl)
        # Add sync_status before the PRIMARY KEY line
        sync_col = '  "sync_status" INTEGER NOT NULL DEFAULT 0'
        ddl = re.sub(
            r"(\n\s*PRIMARY KEY)",
            f",\n{sync_col},\n  PRIMARY KEY",
            ddl, count=1
        )
        # Clean up any double commas introduced
        ddl = re.sub(r",\s*,", ",", ddl)
        # Ensure CREATE TABLE uses IF NOT EXISTS
        ddl = re.sub(r"CREATE TABLE(?!\s+IF)", "CREATE TABLE IF NOT EXISTS", ddl)

    elif remote_engine == "mysql" and local_engine == "mysql":
        ddl = re.sub(r" AUTO_INCREMENT=\d+", "", ddl)
        ddl = re.sub(r",\s*`sync_status`[^\n]+", "", ddl)
        ddl = re.sub(r"([^,])\n(\s*(?:PRIMARY KEY|KEY))", r"\1,\n\2", ddl)
        # Inject sync_status before PRIMARY KEY
        ddl = re.sub(
            r"(\s*)(PRIMARY KEY)",
            r"  `sync_status` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '0=pending,1=synced',\n\1\2",
            ddl, count=1,
        )

    elif remote_engine == "postgresql" and local_engine == "sqlite":
        ddl = re.sub(r"\bSERIAL\b|\bBIGSERIAL\b", "INTEGER", ddl)
        ddl = re.sub(r"\bVARCHAR\(\d+\)", "TEXT", ddl)
        ddl = re.sub(r"\bBOOLEAN\b", "INTEGER", ddl)
        ddl = re.sub(r"\bTIMESTAMP[^,\n]*", "TEXT", ddl)
        ddl = re.sub(r"CREATE TABLE", "CREATE TABLE IF NOT EXISTS", ddl)
        ddl = re.sub(
            r"(\s*)(\)\s*$)",
            r",\n  \"sync_status\" INTEGER NOT NULL DEFAULT 0\n\2",
            ddl, flags=re.MULTILINE
        )

    return ddl.strip()


def ensure_remote_schema(ldriver: BaseDriver, rdriver: BaseDriver):
    """PUSH: create missing tables on remote, strip sync_status."""
    remote_tables = rdriver.list_tables()
    for table in ldriver.list_tables():
        if table == "offline_queue":
            continue
        if table in remote_tables:
            # Drop sync_status on remote if it crept in by mistake
            if "sync_status" in rdriver.list_columns(table):
                try:
                    cur = rdriver.cursor()
                    cur.execute(
                        f"ALTER TABLE {rdriver.q(table)} "
                        f"DROP COLUMN {rdriver.q('sync_status')}"
                    )
                    rdriver.commit()
                    cur.close()
                    log.info("  Dropped sync_status from remote `%s`.", table)
                except Exception:
                    pass
            continue
        ddl = _clean_ddl_for_remote(ldriver.get_create_ddl(table), ldriver.cfg["engine"])
        try:
            cur = rdriver.cursor()
            cur.execute(ddl)
            rdriver.commit()
            cur.close()
            log.info("  Created table `%s` on remote.", table)
        except Exception as e:
            log.warning("  Schema `%s`: %s", table, e)


def ensure_local_schema(ldriver: BaseDriver, rdriver: BaseDriver):
    """PULL: create missing tables on local from remote schema, add sync_status."""
    local_tables  = ldriver.list_tables()
    remote_engine = rdriver.cfg["engine"]
    local_engine  = ldriver.cfg["engine"]

    for table in rdriver.list_tables():
        if table in local_tables:
            continue
        ddl = _clean_ddl_for_local(
            rdriver.get_create_ddl(table), remote_engine, local_engine, table
        )
        try:
            cur = ldriver.cursor()
            cur.execute(ddl)
            ldriver.commit()
            cur.close()
            log.info("  Created local table `%s` from remote.", table)
            if local_engine == "mysql":
                pk = rdriver.get_primary_key(table)
                if pk:
                    _add_mysql_triggers_inline(ldriver, table, pk)
        except Exception as e:
            log.warning("  Local schema `%s`: %s", table, e)


def _add_mysql_triggers_inline(ldriver: BaseDriver, table: str, pk_col: str):
    """Add sync triggers to a newly pulled table."""
    cur = ldriver.cursor()
    for name, stmt in [
        (f"trg_{table}_ins_sync",
         f"CREATE TRIGGER `trg_{table}_ins_sync` BEFORE INSERT ON `{table}` "
         f"FOR EACH ROW SET NEW.sync_status = 0"),
        (f"trg_{table}_upd_sync",
         f"CREATE TRIGGER `trg_{table}_upd_sync` BEFORE UPDATE ON `{table}` FOR EACH ROW "
         f"BEGIN IF NOT (NEW.sync_status=1 AND OLD.sync_status=0) THEN "
         f"SET NEW.sync_status=0; END IF; END"),
        (f"trg_{table}_del_queue",
         f"CREATE TRIGGER `trg_{table}_del_queue` AFTER DELETE ON `{table}` "
         f"FOR EACH ROW INSERT INTO `offline_queue` (op,tbl,pk_col,pk_val) "
         f"VALUES ('DELETE','{table}','{pk_col}',OLD.`{pk_col}`)"),
    ]:
        cur.execute(f"DROP TRIGGER IF EXISTS `{name}`")
        cur.execute(stmt)
    ldriver.commit()
    cur.close()


# ── PUSH INSERT / UPDATE ──────────────────────────────────────────────────────

def push_upserts(ldriver: BaseDriver, rdriver: BaseDriver, table: str, pk: str) -> int:
    lcur = ldriver.cursor()
    rcur = rdriver.cursor()
    ph   = ldriver.ph()

    cols   = [c for c in ldriver.list_columns(table) if c != "sync_status"]
    pk_idx = cols.index(pk)
    sel    = ", ".join(ldriver.q(c) for c in cols)

    lcur.execute(f"SELECT {sel} FROM {ldriver.q(table)} WHERE sync_status = 0")
    rows = lcur.fetchall()

    if not rows:
        lcur.close(); rcur.close()
        return 0

    upsert = rdriver.upsert_sql(table, cols, pk)
    pushed = []
    for row in rows:
        try:
            rcur.execute(upsert, tuple(row))
            pushed.append(row[pk_idx])
        except Exception as e:
            log.warning("  PUSH skip %s[%s]: %s", table, row[pk_idx], e)

    rdriver.commit()

    if pushed:
        fmt = ", ".join([ph] * len(pushed))
        lcur.execute(
            f"UPDATE {ldriver.q(table)} SET sync_status = 1 "
            f"WHERE {ldriver.q(pk)} IN ({fmt})",
            pushed,
        )
        ldriver.commit()

    lcur.close(); rcur.close()
    return len(pushed)


# ── PUSH DELETE ───────────────────────────────────────────────────────────────

def push_deletes(ldriver: BaseDriver, rdriver: BaseDriver) -> int:
    ph   = ldriver.ph()
    lcur = ldriver.cursor()
    rcur = rdriver.cursor()

    lcur.execute(
        f"SELECT id, tbl, pk_col, pk_val FROM {ldriver.q('offline_queue')} "
        f"WHERE op='DELETE' AND synced=0 ORDER BY id"
    )
    ops = lcur.fetchall()

    if not ops:
        lcur.close(); rcur.close()
        return 0

    done = []
    for op_id, tbl, pk_col, pk_val in ops:
        try:
            rcur.execute(
                f"DELETE FROM {rdriver.q(tbl)} WHERE {rdriver.q(pk_col)} = {rdriver.ph()}",
                (pk_val,),
            )
            done.append(op_id)
            log.info("  ⬆ DELETE  %s[%s]", tbl, pk_val)
        except Exception as e:
            log.warning("  DELETE skip queue[%s] %s[%s]: %s", op_id, tbl, pk_val, e)

    rdriver.commit()

    if done:
        fmt = ", ".join([ph] * len(done))
        lcur.execute(
            f"UPDATE {ldriver.q('offline_queue')} SET synced=1 WHERE id IN ({fmt})",
            done,
        )
        ldriver.commit()

    lcur.close(); rcur.close()
    return len(done)


# ── PULL remote → local ───────────────────────────────────────────────────────

def pull_table(ldriver: BaseDriver, rdriver: BaseDriver, table: str, pk: str) -> int:
    lcur = ldriver.cursor()
    rcur = rdriver.cursor()

    r_cols = [c for c in rdriver.list_columns(table) if c != "sync_status"]
    sel    = ", ".join(rdriver.q(c) for c in r_cols)

    rcur.execute(f"SELECT {sel} FROM {rdriver.q(table)}")
    rows = rcur.fetchall()

    if not rows:
        lcur.close(); rcur.close()
        return 0

    all_cols = r_cols + ["sync_status"]
    upsert   = ldriver.upsert_sql(table, all_cols, pk)

    pulled = 0
    for row in rows:
        try:
            lcur.execute(upsert, tuple(row) + (1,))
            pulled += 1
        except Exception as e:
            log.warning("  PULL skip row in `%s`: %s", table, e)

    ldriver.commit()
    lcur.close(); rcur.close()
    return pulled


# ── Main ──────────────────────────────────────────────────────────────────────

def run_push(local_cfg: dict, remote_cfg: dict):
    """Push local pending rows → remote."""
    if not is_online(remote_cfg):
        log.warning("⚠️  Remote unreachable — skipping push.")
        return

    ldriver = get_driver(local_cfg)
    rdriver = get_driver(remote_cfg)

    SYNC_TABLES = ldriver.get_sync_tables()
    log.info("⬆ PUSH [%s → %s] %d table(s)",
             local_cfg["engine"], remote_cfg["engine"], len(SYNC_TABLES))
    try:
        ensure_remote_schema(ldriver, rdriver)
        push_upd = push_del = 0

        for table, pk in SYNC_TABLES.items():
            try:
                n = push_upserts(ldriver, rdriver, table, pk)
                if n:
                    log.info("  ⬆ %-40s %d row(s)", table, n)
                push_upd += n
            except KeyboardInterrupt:
                raise
            except Exception as e:
                log.error("  ✘ %s: %s", table, e)

        try:
            push_del = push_deletes(ldriver, rdriver)
            if push_del:
                log.info("  ⬆ DELETE %d queued operation(s)", push_del)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            log.error("  ✘ DELETE: %s", e)

        log.info("✅ Push done — %d upsert(s) + %d delete(s).", push_upd, push_del)

    except KeyboardInterrupt:
        log.info("🛑 Push interrupted.")
    finally:
        ldriver.close()
        rdriver.close()


def run_pull(local_cfg: dict, remote_cfg: dict):
    """Pull remote rows → local. Creates missing local tables automatically."""
    if not is_online(remote_cfg):
        log.warning("⚠️  Remote unreachable — skipping pull.")
        return

    ldriver = get_driver(local_cfg)
    rdriver = get_driver(remote_cfg)

    log.info("⬇ PULL [%s → %s]", remote_cfg["engine"], local_cfg["engine"])
    try:
        # Create any missing local tables from remote schema first
        ensure_local_schema(ldriver, rdriver)

        # Re-discover tables after potential new ones were created
        SYNC_TABLES = rdriver.get_sync_tables()
        log.info("  %d table(s) on remote.", len(SYNC_TABLES))

        pull_total = 0
        for table, pk in SYNC_TABLES.items():
            try:
                n = pull_table(ldriver, rdriver, table, pk)
                if n:
                    log.info("  ⬇ %-40s %d row(s)", table, n)
                pull_total += n
            except KeyboardInterrupt:
                raise
            except Exception as e:
                log.error("  ✘ %s: %s", table, e)

        log.info("✅ Pull done — %d row(s).", pull_total)

    except KeyboardInterrupt:
        log.info("🛑 Pull interrupted.")
    finally:
        ldriver.close()
        rdriver.close()


if __name__ == "__main__":
    args     = sys.argv[1:]
    env_file = args[args.index("--env") + 1] if "--env" in args else None

    # --push and --pull are mutually exclusive
    if "--push" in args and "--pull" in args:
        print("❌ --push and --pull cannot be used together. Pick one.")
        sys.exit(1)

    if "--push" not in args and "--pull" not in args:
        print("❌ Specify --push or --pull.")
        print("   python sync.py --push            # local → remote")
        print("   python sync.py --pull            # remote → local")
        print("   python sync.py --push --watch 30 # push every 30s")
        print("   python sync.py --pull --watch 60 # pull every 60s")
        sys.exit(1)

    do_push = "--push" in args
    local_cfg, remote_cfg = get_db_config(env_file)
    action = run_push if do_push else run_pull
    label  = "push" if do_push else "pull"

    if "--watch" in args:
        idx      = args.index("--watch")
        interval = int(args[idx + 1]) if idx + 1 < len(args) and args[idx + 1].isdigit() else SYNC_INTERVAL
        log.info("👀 Watch mode [%s] — every %ds (Ctrl+C to stop).", label, interval)
        try:
            while True:
                action(local_cfg, remote_cfg)
                time.sleep(interval)
        except KeyboardInterrupt:
            log.info("🛑 Sync stopped.")
    else:
        try:
            action(local_cfg, remote_cfg)
        except KeyboardInterrupt:
            log.info("🛑 Sync interrupted.")
