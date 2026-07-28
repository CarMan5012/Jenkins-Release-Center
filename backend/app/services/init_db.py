from app.core.database import Base, sync_engine, SyncSessionLocal
from app.core.security import get_password_hash
from app.core.config import settings
from app.models.user import User
from loguru import logger

from sqlalchemy import bindparam, inspect, text


def ensure_release_plan_preflight_columns(engine) -> None:
    inspector = inspect(engine)
    if "release_plan" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("release_plan")}
    definitions = {
        "preflight_status": "VARCHAR(20) NOT NULL DEFAULT 'UNCHECKED'",
        "preflight_revision": "INTEGER NOT NULL DEFAULT 0",
        "preflight_checked_at": "DATETIME NULL",
        "preflight_result": "JSON NULL",
    }
    with engine.begin() as connection:
        for name, definition in definitions.items():
            if name not in columns:
                connection.execute(text(f"ALTER TABLE release_plan ADD COLUMN {name} {definition}"))


def ensure_jenkins_consistency_schema(engine) -> None:
    inspector = inspect(engine)
    index_name = "uix_release_history_build_identity"
    expected_columns = ["server_id", "job_name", "build_number"]
    existing_indexes = inspector.get_indexes("release_history")
    existing_indexes.extend(
        {
            "name": constraint["name"],
            "unique": True,
            "column_names": constraint["column_names"],
        }
        for constraint in inspector.get_unique_constraints("release_history")
    )
    matching_indexes = [
        index for index in existing_indexes if index["name"] == index_name
    ]
    if matching_indexes and any(
        not index.get("unique")
        or index.get("column_names") != expected_columns
        for index in matching_indexes
    ):
        raise RuntimeError(
            f"Index {index_name} conflicts with required unique Jenkins build identity"
        )

    release_task_columns = {
        column["name"] for column in inspector.get_columns("release_task")
    }
    release_history_columns = {
        column["name"] for column in inspector.get_columns("release_history")
    }

    with engine.begin() as connection:
        if "jenkins_queue_id" not in release_task_columns:
            connection.execute(text(
                "ALTER TABLE release_task ADD COLUMN jenkins_queue_id INTEGER NULL"
            ))
        if "server_id" not in release_history_columns:
            connection.execute(text(
                "ALTER TABLE release_history ADD COLUMN server_id INTEGER NULL"
            ))

        connection.execute(text(
            "UPDATE release_history SET server_id = ("
            "SELECT MIN(jenkins_server.id) FROM jenkins_server "
            "WHERE jenkins_server.name = release_history.server_name"
            ") WHERE server_id IS NULL AND server_name IN ("
            "SELECT name FROM jenkins_server GROUP BY name HAVING COUNT(*) = 1"
            ")"
        ))

        rows = connection.execute(text(
            "SELECT id, server_id, job_name, build_number, task_id "
            "FROM release_history WHERE server_id IS NOT NULL "
            "ORDER BY server_id, job_name, build_number, "
            "CASE WHEN task_id IS NOT NULL THEN 0 ELSE 1 END, id DESC"
        ))
        seen = set()
        duplicate_ids = []
        for row in rows:
            identity = (row.server_id, row.job_name, row.build_number)
            if row.job_name is None or row.build_number is None:
                continue
            if identity in seen:
                duplicate_ids.append(row.id)
            else:
                seen.add(identity)

        if duplicate_ids:
            connection.execute(
                text("DELETE FROM release_history WHERE id IN :duplicate_ids")
                .bindparams(bindparam("duplicate_ids", expanding=True)),
                {"duplicate_ids": duplicate_ids},
            )

    if not matching_indexes:
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE UNIQUE INDEX uix_release_history_build_identity "
                "ON release_history (server_id, job_name, build_number)"
            ))

def init_db() -> None:
    # 1. Create tables if they do not exist
    logger.info("Initializing database tables...")
    try:
        # Note: Since APScheduler jobs store table is handled by the scheduler,
        # here we create only our application tables.
        # APScheduler table is automatically created by SQLAlchemyJobStore on start.
        Base.metadata.create_all(bind=sync_engine)
        ensure_release_plan_preflight_columns(sync_engine)
        ensure_jenkins_consistency_schema(sync_engine)
        logger.info("Database tables created or verified.")

        if sync_engine.dialect.name == "mysql":
            foreign_keys = inspect(sync_engine).get_foreign_keys("release_task")
            for foreign_key in foreign_keys:
                if (
                    foreign_key.get("referred_table") == "jenkins_job"
                    and "job_id" in foreign_key.get("constrained_columns", [])
                ):
                    constraint_name = foreign_key.get("name")
                    if not constraint_name or not constraint_name.replace("_", "").isalnum():
                        raise RuntimeError(f"Unsafe foreign key name: {constraint_name!r}")
                    with sync_engine.begin() as connection:
                        connection.execute(text(
                            f"ALTER TABLE release_task DROP FOREIGN KEY `{constraint_name}`"
                        ))
                    logger.info(
                        "Removed legacy release_task.job_id foreign key; "
                        "Jenkins jobs are now disposable cache records."
                    )
            
            # Alter release_task.job_id to nullable in MySQL
            with sync_engine.begin() as connection:
                connection.execute(text(
                    "ALTER TABLE release_task MODIFY COLUMN job_id INT NULL;"
                ))
            logger.info("Successfully altered release_task.job_id to nullable.")

        # Backfill release_task.job_name if missing (idempotent, supports both MySQL and SQLite)
        with sync_engine.begin() as connection:
            if sync_engine.dialect.name == "mysql":
                connection.execute(text(
                    "UPDATE release_task rt JOIN jenkins_job jj ON rt.job_id = jj.id "
                    "SET rt.job_name = jj.name WHERE rt.job_name IS NULL OR rt.job_name = '';"
                ))
            else:
                connection.execute(text(
                    "UPDATE release_task SET job_name = ("
                    "SELECT name FROM jenkins_job WHERE jenkins_job.id = release_task.job_id"
                    ") WHERE job_name IS NULL OR job_name = '';"
                ))
        logger.info("Completed release_task.job_name backfilling logic.")

        # Check and add keyword column in notify_config dynamically for SQLite & MySQL
        session = SyncSessionLocal()
        try:
            inspector = inspect(sync_engine)
            if "notify_config" in inspector.get_table_names():
                columns = [c["name"] for c in inspector.get_columns("notify_config")]
                if "keyword" not in columns:
                    if sync_engine.dialect.name == "mysql":
                        session.execute(text("ALTER TABLE notify_config ADD COLUMN keyword VARCHAR(255) NULL;"))
                    else:
                        session.execute(text("ALTER TABLE notify_config ADD COLUMN keyword VARCHAR(255);"))
                    session.commit()
                    logger.info("Database column notify_config.keyword added dynamically.")
        except Exception as ex:
            session.rollback()
            logger.warning(f"Failed to auto-upgrade notify_config table structure: {str(ex)}")
        finally:
            session.close()
        
        # Upgrade tables columns in MySQL/MariaDB dynamically
        session = SyncSessionLocal()
        try:
            if sync_engine.dialect.name == "mysql":
                session.execute(text("ALTER TABLE release_history MODIFY COLUMN task_id INT NULL;"))
                session.execute(text("ALTER TABLE release_history MODIFY COLUMN plan_id INT NULL;"))
                logger.info("Database columns release_history.task_id/plan_id updated to nullable.")
                
                # Upgrade jenkins_backup.summary_md to LONGTEXT safely
                try:
                    session.execute(text("ALTER TABLE jenkins_backup MODIFY COLUMN summary_md LONGTEXT;"))
                    logger.info("Database column jenkins_backup.summary_md upgraded to LONGTEXT successfully.")
                except Exception as ex:
                    logger.debug(f"Skip upgrade jenkins_backup.summary_md: {str(ex)}")
                
                # Check and add is_external column dynamically
                inspector = inspect(sync_engine)
                columns = [c["name"] for c in inspector.get_columns("release_history")]
                if "is_external" not in columns:
                    session.execute(text("ALTER TABLE release_history ADD COLUMN is_external TINYINT(1) NOT NULL DEFAULT 0;"))
                    session.execute(text("UPDATE release_history SET is_external = 1 WHERE task_id IS NULL;"))
                    logger.info("Database column release_history.is_external added and preseeded.")

                session.execute(text("ALTER TABLE release_history MODIFY COLUMN logs LONGTEXT;"))
                session.commit()
                logger.info("Database column release_history.logs upgraded to LONGTEXT successfully.")
        except Exception as e:
            session.rollback()
            logger.warning(f"Failed to auto-upgrade release_history table structure: {str(e)}")
        finally:
            session.close()

        # Upgrade release_plan & jenkins_backup to add idempotency_key dynamically
        try:
            inspector = inspect(sync_engine)
            if "release_plan" in inspector.get_table_names():
                columns = [c["name"] for c in inspector.get_columns("release_plan")]
                if "idempotency_key" not in columns:
                    with sync_engine.begin() as connection:
                        if sync_engine.dialect.name == "mysql":
                            connection.execute(text("ALTER TABLE release_plan ADD COLUMN idempotency_key VARCHAR(255) NULL UNIQUE;"))
                        else:
                            connection.execute(text("ALTER TABLE release_plan ADD COLUMN idempotency_key VARCHAR(255) NULL;"))
                            connection.execute(text("CREATE UNIQUE INDEX uix_release_plan_idem ON release_plan (idempotency_key);"))
                    logger.info("Database column release_plan.idempotency_key added dynamically.")

            if "jenkins_backup" in inspector.get_table_names():
                columns = [c["name"] for c in inspector.get_columns("jenkins_backup")]
                if "idempotency_key" not in columns:
                    with sync_engine.begin() as connection:
                        if sync_engine.dialect.name == "mysql":
                            connection.execute(text("ALTER TABLE jenkins_backup ADD COLUMN idempotency_key VARCHAR(255) NULL UNIQUE;"))
                        else:
                            connection.execute(text("ALTER TABLE jenkins_backup ADD COLUMN idempotency_key VARCHAR(255) NULL;"))
                            connection.execute(text("CREATE UNIQUE INDEX uix_jenkins_backup_idem ON jenkins_backup (idempotency_key);"))
                    logger.info("Database column jenkins_backup.idempotency_key added dynamically.")
        except Exception as ex:
            logger.warning(f"Failed to auto-upgrade idempotency_key columns: {str(ex)}")

            
    except Exception as e:
        logger.error(f"Failed to create database tables: {str(e)}")
        raise

    # 2. Create initial admin user
    session = SyncSessionLocal()
    try:
        admin = session.query(User).filter(User.username == settings.INITIAL_ADMIN_USERNAME).first()
        if not admin:
            logger.info(f"Creating initial admin user: {settings.INITIAL_ADMIN_USERNAME}...")
            hashed_pwd = get_password_hash(settings.INITIAL_ADMIN_PASSWORD)
            new_admin = User(
                username=settings.INITIAL_ADMIN_USERNAME,
                password_hash=hashed_pwd,
                role="admin",
                email="admin@example.com",
                is_active=1
            )
            session.add(new_admin)
            session.commit()
            logger.info("Initial admin user created successfully.")
        else:
            logger.info("Initial admin user already exists.")
    except Exception as e:
        session.rollback()
        logger.error(f"Error during initial admin seed: {str(e)}")
        raise
    finally:
        session.close()
