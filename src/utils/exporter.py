import os

class FileExporter:
    def export_markdown(self, content: str, filename: str, output_dir: str = None) -> str:
        if output_dir is None:
            from src.config import get_settings
            output_dir = get_settings().output_dir
        
        os.makedirs(output_dir, exist_ok=True)
        filepath = os.path.join(output_dir, filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        
        return filepath
