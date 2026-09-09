"""Agent 入口：问答服务。树重建已采用单次结构化判断器，不再使用 ReAct 工具型 Agent。"""

from .qa import QAService

__all__ = ["QAService"]
