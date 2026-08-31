from abc import ABC, abstractmethod

class BaseExporter(ABC):
    @abstractmethod
    def export(self, data: any, output_path: str):
        pass
