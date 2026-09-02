"""API 包：FastAPI 本地 Web 服务（tasks 域）。"""
from src.api.app import create_app
from src.api.task_manager import TaskManager

__all__ = ["create_app", "TaskManager"]
