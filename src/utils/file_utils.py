import os

class FileUtils:
    @staticmethod
    def ensure_dir(path: str):
        os.makedirs(path, exist_ok=True)
    
    @staticmethod
    def get_file_size(filepath: str) -> int:
        return os.path.getsize(filepath)
    
    @staticmethod
    def file_exists(filepath: str) -> bool:
        return os.path.exists(filepath)
