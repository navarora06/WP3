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

    AZURE_OPENAI_EMBEDDING_DEPLOYMENT = os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-small")

    AZURE_SEARCH_ENDPOINT = os.environ.get("AZURE_SEARCH_ENDPOINT", "")
    AZURE_SEARCH_KEY = os.environ.get("AZURE_SEARCH_KEY", "")

    AZURE_COSMOS_GREMLIN_ENDPOINT = os.environ.get("AZURE_COSMOS_GREMLIN_ENDPOINT", "")
    AZURE_COSMOS_GREMLIN_KEY = os.environ.get("AZURE_COSMOS_GREMLIN_KEY", "")
    AZURE_COSMOS_GREMLIN_DATABASE = os.environ.get("AZURE_COSMOS_GREMLIN_DATABASE", "wp3knowledge")
    AZURE_COSMOS_GREMLIN_GRAPH = os.environ.get("AZURE_COSMOS_GREMLIN_GRAPH", "knowledge_graph")

    AZURE_VISION_ENDPOINT = os.environ.get("AZURE_VISION_ENDPOINT", "")
    AZURE_VISION_KEY = os.environ.get("AZURE_VISION_KEY", "")
