# db-sync

A lightweight offline-first database sync engine.  
Works with **MySQL**, **PostgreSQL**, **SQLite**, and **SQL Server**.  
Runs on **Linux** and **Windows**.

---

## How it works

- All writes go to your **local database first** — works 100% without internet
- When online, `sync.py` pushes local changes to the remote database
- Pull mode downloads remote data to keep your local database fresh
- Every table gets a `sync_status` column and triggers to track changes automatically
- Deletes are captured in an `offline_queue` table via triggers

```
Your App → writes local DB
              ↓
           sync.py --push   →   Remote DB
           sync.py --pull   ←   Remote DB
```

---

## Supported Databases

| Engine      | Local | Remote |
|-------------|-------|--------|
| MySQL       | ✅    | ✅     |
| PostgreSQL  | ✅    | ✅     |
| SQLite      | ✅    | ✅     |
| SQL Server  | ✅    | ✅     |

You can mix engines — e.g. SQLite local → MySQL remote.

---

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/Ndikuma/db-sync.git
cd db-sync

python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
nano .env   # fill in your database credentials
```

`.env` settings:

```env
LOCAL_DB_ENGINE=mysql        # mysql | postgresql | sqlite | sqlserver
LOCAL_DB_HOST=localhost
LOCAL_DB_PORT=3306
LOCAL_DB_USER=root
LOCAL_DB_PASSWORD=yourpassword
LOCAL_DB_NAME=yourdb

REMOTE_DB_ENGINE=mysql
REMOTE_DB_HOST=your-remote-host
REMOTE_DB_PORT=3306
REMOTE_DB_USER=remote_user
REMOTE_DB_PASSWORD=remote_password
REMOTE_DB_NAME=yourdb

SYNC_INTERVAL=60    # seconds between syncs in watch mode
SYNC_TIMEOUT=10     # seconds before remote connection times out
```

### 3. Prepare local database

```bash
# If you have an existing database already set up:
python db_local.py

# If you have a SQL dump to load first:
python db_local.py --sql your_dump.sql
```

### 4. Sync

```bash
# Push local changes to remote
python sync.py --push

# Pull remote data to local
python sync.py --pull

# Keep syncing automatically every 30 seconds
python sync.py --push --watch 30
python sync.py --pull --watch 60
```

---

## Deploy as a Background Service

### Linux (systemd)

```bash
# Push every 30 seconds
sudo bash deploy-linux.sh --push --watch 30

# Pull every 60 seconds
sudo bash deploy-linux.sh --pull --watch 60
```

Manage the service:

```bash
sudo systemctl status  db-sync
sudo systemctl stop    db-sync
sudo systemctl start   db-sync
sudo journalctl -u db-sync -f    # live logs
```

### Windows (NSSM Service)

1. Download **nssm.exe** from → **https://nssm.cc/download** and place it in the project folder
2. Open PowerShell as Administrator

```powershell
# Push every 30 seconds
.\deploy-windows.ps1 -Push -Watch 30

# Pull every 60 seconds
.\deploy-windows.ps1 -Pull -Watch 60
```

Manage the service:

```powershell
.\nssm.exe status  db-sync
.\nssm.exe stop    db-sync
.\nssm.exe start   db-sync
```

### Windows (manual run)

```bat
start_sync.bat --push --watch 30
start_sync.bat --pull --watch 60
```

---

## Project Structure

```
db-sync/
├── sync.py            # sync engine (push / pull)
├── db_local.py        # prepare local database for sync
├── drivers.py         # database drivers (MySQL, PostgreSQL, SQLite, SQL Server)
├── config.py          # loads .env configuration
├── deploy-linux.sh    # Linux systemd service installer
├── deploy-windows.ps1 # Windows NSSM service installer
├── start_sync.bat     # Windows manual run
├── .env.example       # configuration template
└── requirements.txt   # Python dependencies
```

---

## Requirements

- Python 3.10+
- Packages: `mysql-connector-python`, `python-dotenv`, `psycopg2-binary`, `pyodbc`

Install only what you need:

```bash
pip install mysql-connector-python python-dotenv   # MySQL
pip install psycopg2-binary                        # PostgreSQL
pip install pyodbc                                 # SQL Server
# SQLite is built into Python — nothing to install
```

---

## License

MIT
