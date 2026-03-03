import os


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret")
    DATABASE_URL = os.environ["DATABASE_URL"]
    REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    STORAGE_ROOT = os.environ.get("STORAGE_ROOT", "./storage")

    AZURE_AI_ENDPOINT = os.environ.get("AZURE_AI_ENDPOINT", "")
    AZURE_AI_PROJECT_KEY = os.environ.get("AZURE_AI_PROJECT_KEY", "")
    AZURE_OPENAI_DEPLOYMENT = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
    AZURE_OPENAI_NLI_DEPLOYMENT = os.environ.get("AZURE_OPENAI_NLI_DEPLOYMENT", "gpt-4o")

    AZURE_SPEECH_KEY = os.environ.get("AZURE_SPEECH_KEY", "")
    AZURE_SPEECH_REGION = os.environ.get("AZURE_SPEECH_REGION", "swedencentral")

    AZURE_TRANSLATOR_KEY = os.environ.get("AZURE_TRANSLATOR_KEY", "")
    AZURE_TRANSLATOR_ENDPOINT = os.environ.get(
        "AZURE_TRANSLATOR_ENDPOINT", "https://api.cognitive.microsofttranslator.com"
    )
    AZURE_TRANSLATOR_REGION = os.environ.get("AZURE_TRANSLATOR_REGION", "swedencentral")
