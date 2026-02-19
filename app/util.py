import os
from contextlib import contextmanager
from werkzeug.security import generate_password_hash, check_password_hash
from app import extensions


@contextmanager
def db_session():
    db = extensions.SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def hash_password(pw: str) -> str:
    return generate_password_hash(pw)

def verify_password(pw: str, pw_hash: str) -> bool:
    return check_password_hash(pw_hash, pw)

def ensure_dirs(storage_root: str):
    os.makedirs(os.path.join(storage_root, "uploads", "audio"), exist_ok=True)
    os.makedirs(os.path.join(storage_root, "uploads", "docs"), exist_ok=True)
    os.makedirs(os.path.join(storage_root, "reports"), exist_ok=True)
