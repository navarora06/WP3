from flask import Flask, redirect, url_for
from sqlalchemy import select

from app.config import Config
from app import extensions            # IMPORTANT: import module, not engine value
from app.extensions import login_manager
from app.models import Base, User, Role
from app.storage_backend import StorageBackend
from app.util import ensure_dirs, db_session


def _apply_column_migrations(engine):
    """Add columns that create_all won't add to existing tables."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    if "gap_reports" in insp.get_table_names():
        existing = {c["name"] for c in insp.get_columns("gap_reports")}
        with engine.begin() as conn:
            if "is_reviewed" not in existing:
                conn.execute(text(
                    "ALTER TABLE gap_reports ADD COLUMN is_reviewed BOOLEAN DEFAULT FALSE"
                ))
            if "reviewed_at" not in existing:
                conn.execute(text(
                    "ALTER TABLE gap_reports ADD COLUMN reviewed_at TIMESTAMP"
                ))
            if "report_name" not in existing:
                conn.execute(text(
                    "ALTER TABLE gap_reports ADD COLUMN report_name VARCHAR(500)"
                ))
            if "knowledge_created" not in existing:
                conn.execute(text(
                    "ALTER TABLE gap_reports ADD COLUMN knowledge_created BOOLEAN DEFAULT FALSE"
                ))


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Storage backend (local now; Azure later)
    app.storage = StorageBackend(app.config["STORAGE_ROOT"])
    ensure_dirs(app.config["STORAGE_ROOT"])

    # DB init (sets extensions.engine + extensions.SessionLocal)
    extensions.init_db(app.config["DATABASE_URL"])

    # Create tables (MVP convenience). Later: Alembic migrations.
    Base.metadata.create_all(bind=extensions.engine)
    _apply_column_migrations(extensions.engine)

    # Flask-Login init
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"

    # Default landing route
    @app.get("/")
    def home():
        return redirect(url_for("admin_upload.index"))

    # Blueprints
    from app.auth.routes import bp as auth_bp
    from app.admin_upload.routes import bp as admin_bp
    from app.gap_analysis.routes import bp as gap_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(gap_bp)

    # Create default users if none exist
    with db_session() as db:
        existing = db.execute(select(User).limit(1)).scalar_one_or_none()
        if not existing:
            from app.util import hash_password
            db.add(User(email="admin@local", password_hash=hash_password("admin123"), role=Role.ADMIN))
            db.add(User(email="reviewer@local", password_hash=hash_password("reviewer123"), role=Role.REVIEWER))

    return app
