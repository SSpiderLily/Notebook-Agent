import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    def __init__(self):
        self.openai_api_key = os.getenv('OPENAI_API_KEY', '')
        self.openai_base_url = os.getenv('OPENAI_BASE_URL', 'https://api.deepseek.com/v1')
        self.model_name = os.getenv('MODEL_NAME', 'deepseek-chat')
        self.chroma_db_path = os.getenv('CHROMA_DB_PATH', './data/chroma')
        self.notebooks_dir = os.getenv('NOTEBOOKS_DIR', './notebooks')
        self.output_dir = os.getenv('OUTPUT_DIR', './output')

settings = Settings()
