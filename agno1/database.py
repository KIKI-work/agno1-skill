"""Database setup and configuration for AgentOS."""

__all__ = ["setup_database"]

from pathlib import Path

from agno.db.sqlite import SqliteDb
from agno.utils.log import log_info


async def setup_database() -> SqliteDb:
    """Setup local SQLite database in data/ folder."""
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)

    db_path = data_dir / "database.db"
    log_info(f"📁 Database: {db_path.absolute()}")

    return SqliteDb(db_file=str(db_path), db_url=f"sqlite:///{db_path}")
