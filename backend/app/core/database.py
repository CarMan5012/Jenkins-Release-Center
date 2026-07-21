import os
import sys
import urllib.parse
from typing import Generator
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from loguru import logger
from app.core.config import settings

# Monkey patch to redirect pysqlcipher3 calls to sqlcipher3-binary package
try:
    import sqlcipher3
    sys.modules['pysqlcipher3'] = sqlcipher3
    if hasattr(sqlcipher3, 'dbapi2'):
        sys.modules['pysqlcipher3.dbapi2'] = sqlcipher3.dbapi2
except ImportError:
    pass

# Determine database type (default is sqlite)
db_type = os.getenv("DB_TYPE", "sqlite").lower()

if db_type == "sqlite":
    db_path = os.getenv("SQLITE_PATH", "/app/data/release-center.db")
    
    # 1. Plaintext SQLite database check
    if os.path.exists(db_path):
        try:
            with open(db_path, "rb") as f:
                header = f.read(16)
            if header == b"SQLite format 3\x00":
                logger.critical(f"Plaintext SQLite database file detected at {db_path}!")
                raise RuntimeError(
                    f"Plaintext SQLite database file detected at {db_path}. "
                    "For security reasons, automatic in-place encryption is not supported and the system will not overwrite the file. "
                    "Please migrate the database manually or back it up and delete it before starting the application."
                )
        except IOError as e:
            logger.error(f"Failed to inspect SQLite database header: {str(e)}")

    # 2. Key loading with priority: SQLITE_KEY_FILE > SQLITE_KEY
    key_file = os.getenv("SQLITE_KEY_FILE", "/run/secrets/sqlite_key")
    key = ""
    if os.path.exists(key_file):
        try:
            with open(key_file, "r", encoding="utf-8") as f:
                key = f.read().strip()
        except IOError as e:
            logger.error(f"Failed to read SQLITE_KEY_FILE at {key_file}: {str(e)}")
            
    if not key:
        key = os.getenv("SQLITE_KEY", "").strip()
        
    is_dev = settings.APP_ENV == "development"
    if not key and not is_dev:
        logger.critical("SQLCipher encryption key is missing or empty. Startup aborted.")
        raise RuntimeError("SQLCipher encryption key is missing or empty. Please configure SQLITE_KEY_FILE or SQLITE_KEY.")

    if is_dev and not key:
        if os.path.isabs(db_path):
            db_url = f"sqlite:////{db_path.replace('\\', '/')}"
        else:
            db_url = f"sqlite:///{db_path.replace('\\', '/')}"
        logger.info("Initializing standard plaintext SQLite engine for development fallback...")
    else:
        # 3. Escape key for database URI
        escaped_key = urllib.parse.quote_plus(key)
        
        # Absolute paths in SQLite connection URIs require double slashes on Unix, or triple slashes.
        # SQLAlchemy's sqlite+pysqlcipher format is: sqlite+pysqlcipher://:password@/filename
        # For an absolute path, we construct: sqlite+pysqlcipher://:password@//absolute/path/to/db
        if os.path.isabs(db_path):
            db_url = f"sqlite+pysqlcipher://:{escaped_key}@//{db_path.replace('\\', '/')}"
        else:
            db_url = f"sqlite+pysqlcipher://:{escaped_key}@/{db_path.replace('\\', '/')}"
        logger.info("Initializing SQLCipher SQLite engine...")
        
    from sqlalchemy.pool import NullPool
    
    # Create engine using NullPool and check_same_thread=False to ensure multi-threaded stability
    sync_engine = create_engine(
        db_url,
        poolclass=NullPool,
        connect_args={"check_same_thread": False},
        echo=False
    )

    
    # Configure database file permissions and SQLCipher verification on connection
    @event.listens_for(sync_engine, "connect")
    def configure_sqlite_connection(dbapi_connection, connection_record):
        # Limit access permissions to 0600 (owner read/write only)
        if os.path.exists(db_path):
            try:
                os.chmod(db_path, 0o600)
            except Exception as e:
                logger.debug(f"Could not adjust SQLite file permissions: {str(e)}")
                
        cursor = dbapi_connection.cursor()
        
        # Enforce foreign key constraints, rollback journaling, and busy timeout
        cursor.execute("PRAGMA foreign_keys = ON;")
        cursor.execute("PRAGMA journal_mode = DELETE;")
        cursor.execute("PRAGMA busy_timeout = 5000;")
        
        # Perform key verification by running a test query
        try:
            cursor.execute("SELECT count(*) FROM sqlite_master;")
            cursor.fetchone()
        except Exception as e:
            logger.critical("SQLCipher key verification failed! The key is incorrect or the database file is corrupted.")
            raise RuntimeError(
                "SQLCipher key verification failed. The key is either incorrect, missing, or the database is corrupted."
            ) from e
        finally:
            cursor.close()

else:
    # External MySQL Mode
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        db_url = settings.SYNC_SQLALCHEMY_DATABASE_URI
        
    # Hide password in connection logs
    try:
        parsed = urllib.parse.urlparse(db_url)
        masked_url = db_url.replace(parsed.password, "********") if parsed.password else db_url
    except Exception:
        masked_url = "mysql+pymysql://<user>:********@<host>..."
        
    logger.info(f"Initializing external MySQL engine with: {masked_url}")
    
    sync_engine = create_engine(
        db_url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20
    )

# Unified Sync Session Local
SyncSessionLocal = sessionmaker(
    bind=sync_engine,
    autocommit=False,
    autoflush=False
)

Base = declarative_base()

# Unified synchronous get_db Dependency Generator
def get_db() -> Generator[Session, None, None]:
    session = SyncSessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
