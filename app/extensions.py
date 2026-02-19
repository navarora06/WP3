from flask_login import LoginManager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

login_manager = LoginManager()

engine = None
SessionLocal = None

def init_db(database_url: str):
    global engine, SessionLocal
    engine = create_engine(database_url, future=True)
    SessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        future=True,
        expire_on_commit=False,
    )
    return engine

from app.models import User
from app.util import db_session

@login_manager.user_loader
def load_user(user_id: str):
    with db_session() as db:
        return db.get(User, int(user_id))
