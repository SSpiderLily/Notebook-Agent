from abc import ABC, abstractmethod

class BaseVectorStore(ABC):
    @abstractmethod
    def add_notes(self, notes: list):
        pass
    
    @abstractmethod
    def search(self, query: str, k: int = 5) -> list:
        pass
    
    @abstractmethod
    def get_all(self) -> list:
        pass
