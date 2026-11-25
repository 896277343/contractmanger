"""Online SQLite backup: python scripts/backup_sqlite.py DESTINATION_DIRECTORY."""
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit("用法: python scripts/backup_sqlite.py 备份目录")
source = Path(os.environ.get("SQLITE_PATH", Path(__file__).resolve().parents[1] / "data" / "contracts.sqlite3"))
destination_dir = Path(sys.argv[1]).resolve()
destination_dir.mkdir(parents=True, exist_ok=True)
destination = destination_dir / f"contracts-{datetime.now():%Y%m%d-%H%M%S}.sqlite3"
with sqlite3.connect(source) as original, sqlite3.connect(destination) as backup:
    original.backup(backup)
    result = backup.execute("PRAGMA integrity_check").fetchone()[0]
if result != "ok":
    destination.unlink(missing_ok=True)
    raise SystemExit(f"备份完整性校验失败: {result}")
print(destination)
