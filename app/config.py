import os

class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret")
    DATABASE_URL = os.environ["DATABASE_URL"]
    REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    STORAGE_ROOT = os.environ.get("STORAGE_ROOT", "./storage")

    LIGHTRAG_BASE_URL = os.environ.get("LIGHTRAG_BASE_URL", "")
    LIGHTRAG_NAMESPACE = os.environ.get("LIGHTRAG_NAMESPACE", "default")
