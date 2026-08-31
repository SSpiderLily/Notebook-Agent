from .settings import Settings

_settings = None

def get_settings():
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
