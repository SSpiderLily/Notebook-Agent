from abc import ABC, abstractmethod

class BaseChainOfThought(ABC):
    @abstractmethod
    def generate(self, notes: list) -> str:
        pass
