import os
from dotenv import load_dotenv

load_dotenv()

SYNC_INTERVAL = int(os.getenv("SYNC_INTERVAL", 60))
SYNC_TIMEOUT  = int(os.getenv("SYNC_TIMEOUT", 10))


def get_db_config(env_file: str = None) -> tuple[dict, dict]:
    """
    Load LOCAL and REMOTE db configs from .env.
    Returns two dicts ready for drivers.get_driver().
    """
    if env_file:
        load_dotenv(env_file, override=True)

    def _cfg(prefix: str) -> dict:
        return {
            "engine":   os.getenv(f"{prefix}_ENGINE", "mysql").lower(),
            "host":     os.getenv(f"{prefix}_HOST", "localhost"),
            "port":     int(os.getenv(f"{prefix}_PORT", 3306)),
            "user":     os.getenv(f"{prefix}_USER", "root"),
            "password": os.getenv(f"{prefix}_PASSWORD", ""),
            "database": os.getenv(f"{prefix}_NAME", ""),
            "timeout":  SYNC_TIMEOUT,
        }

    local  = _cfg("LOCAL_DB")
    remote = _cfg("REMOTE_DB")

    if not local["database"]:
        raise ValueError("LOCAL_DB_NAME is not set.")
    if not remote["database"]:
        raise ValueError("REMOTE_DB_NAME is not set.")

    return local, remote


# Default configs loaded at import time
LOCAL_DB, REMOTE_DB = get_db_config()
