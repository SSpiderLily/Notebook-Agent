"""树重建 ReAct Agent：Gateway 适配器、五工具白名单与多轮编排。

对应 DESIGN.md：§6.1 树重建为真实多轮工具调用 Agent（LangGraph create_agent + ToolNode），
§十 决策记录。LLM 统一经 `LLMGateway` 转发（RECORD/REPLAY、台账、成本护栏不绕过），
工具白名单由 `tools.py` 硬编码，`submit_assignment` 为终态工具。
本模块只实现 Agent 级推理与工具选择；草稿森林/追加原则/幂等持久化在
`src/core/tree_rebuild.py`。安全边界：工具只读查询、`submit_assignment` 只记账不到已验证树，
任何移动/拆分/改父级由 core 层按追加原则拒绝进人工复核队列。
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any, Mapping, Sequence

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from src.agents.tools import TOOL_NAMES, build_tools
from src.core.tree_rebuild import TreeAssignment

# 工具白名单（与 DESIGN.md 6.1 一致），硬编码在 tools.py
DEFAULT_SYSTEM_PROMPT = SystemMessage(
    "你是笔记森林的树重建 Agent。给定一个待挂接事件，你可调用工具检索候选树、"
    "回读笔记、查看树时间线、搜索事件库。完成考量后调用 submit_assignment 提交终态判定。"
    "只允许使用白名单工具，禁止假设不存在的树 ID。"
)


def _parse_gateway_response(raw: Any, call_id: str) -> AIMessage:
    """把 Gateway/transport 返回解析为工具调用或纯文本 AIMessage。

    协议：
    - `{"tool": "...", "args": {...}}` → 工具调用 AIMessage（触发 ReAct 下一轮）；
    - `{"tool_calls": [...]}` → 多工具调用；
    - 真实 Provider 常自由输出 `{args JSON} to=工具 json` 混排、DSML `<DSML>invoke`/`<|DSML|>`、
      XML `<toolcall>` 块，统一容错提取；JSON 可解析但无法识别为工具调用时按终态纯文本处理。
    """
    if isinstance(raw, str):
        normalized = raw.strip()
        if normalized.startswith("```"):
            normalized = normalized.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        try:
            data = json.loads(normalized)
        except json.JSONDecodeError:
            data = None
    else:
        data = raw
    if isinstance(data, dict):
        tool_calls = data.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            return AIMessage(content="", tool_calls=[_norm_tool_call(tc) for tc in tool_calls])
        if "tool" in data:
            name = str(data["tool"])
            args = data.get("args") or {}
            return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])
    if isinstance(raw, str):
        text = raw.strip()
        # DSML / sklearn 风格 `<DSML>invoke name="..."` / `<tool_call>` / `<toolcall>` XML 块
        calls = _extract_xml_tool_calls(text) or _extract_prose_tool_calls(text)
        if calls:
            return AIMessage(content="", tool_calls=[{"name": n, "args": a, "id": f"{call_id}-{i}"} for i, (n, a) in enumerate(calls)])
    return AIMessage(content=raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False))


def _norm_tool_call(tc: Mapping[str, Any]) -> dict[str, Any]:
    name = tc.get("name") or tc.get("function", {}).get("name", "")
    if "function" in tc and "name" not in tc:  # OpenAI 风格
        args = tc.get("function", {}).get("arguments", "{}")
        args = json.loads(args) if isinstance(args, str) else args
        return {"name": name, "args": args, "id": tc.get("id", "")}
    return {"name": name, "args": tc.get("args", {}), "id": tc.get("id", "c0")}


def _extract_xml_tool_calls(text: str) -> list[tuple[str, dict[str, Any]]]:
    """从 XML/DSML 风格的 tool_call 块里提取 (工具名, args)。"""
    calls: list[tuple[str, dict[str, Any]]] = []
    # DSML 风格（glm 系 Provider 前后常各带一个管道符）：<|DSML|invoke name="tool">、<|DSML|parameter>...</parameter>
    # dsml 令牌：兼容 <DSML|、<|DSML|、<|DSML> 等带/不带管道符的写法
    dsml = r"(?:[|｜]?\s*DSML\s*[|｜]?)"
    invokes = re.findall(rf"<{dsml}\s*invoke\s+name\s*=\s*[\"']([^\"']+)[\"']", text, re.I)
    params = re.findall(rf"<{dsml}\s*parameter\b[^>]*>([\s\S]*?)</{dsml}\s*parameter\s*>", text, re.I)
    if invokes:
        for i, name in enumerate(invokes):
            arg = params[i] if i < len(params) else ""
            calls.append((name, _parse_tool_args(name, arg)))
    if calls:
        return calls
    # XML 风格：<toolcall>/<tool_call>/<toolcall><tool_name>...<parameters>{json}</parameters>
    blocks = re.findall(r'<tool(?:call|_call)[^>]*>([\s\S]*?)</tool(?:call|_call)>', text, re.I)
    for block in blocks:
        name = re.search(r'<tool_name>\s*([^<]+?)\s*</tool_name>', block, re.I)
        params = re.search(r'<parameters>\s*([\s\S]*?)\s*</parameters>', block, re.I)
        n = name.group(1).strip() if name else ""
        if n:
            calls.append((n, _parse_tool_args(n, params.group(1) if params else "{}")))
    return calls


def _extract_prose_tool_calls(text: str) -> list[tuple[str, dict[str, Any]]]:
    """从 `{args JSON} to=工具 json` 混排文本里提取工具调用（真实 Provider 常见）。"""
    calls: list[tuple[str, dict[str, Any]]] = []
    for m in re.finditer(r'(\{.*?\})\s*(?:to=)?\s*([a-z_]+)\s*(?:json|code)?', text, re.I):
        try:
            args = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        name = m.group(2)
        if name in TOOL_NAMES and isinstance(args, dict):
            calls.append((name, args))
    return calls


def _parse_tool_args(name: str, raw: str) -> dict[str, Any]:
    """把工具调用的参数串解析为 dict：优先 JSON，退化为去引号单值。"""
    raw = raw.strip()
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {name.replace("search_", ""): v}
    except json.JSONDecodeError:
        return {"query": raw} if "search" in name else {}


class GatewayChatModel(BaseChatModel):
    """把 LangChain BaseChatModel 适配到 LLMGateway 的聊天模型。

    - `bind_tools`: create_agent 会把白名单工具绑定到本模型；基类默认抛
      NotImplementedError，故这里保存工具 schema 供 `_generate` 组装 prompt。
    - `_generate`: 把多轮消息历史 + 工具说明组装成单 prompt，交给 `LLMGateway.chat`
      转发（record/replay/成本护栏），并把返回解析为工具调用或最终文本。
    """

    gateway: Any
    bound_tools: Sequence[Any] = ()
    system_prompt: Any = None

    @property
    def _llm_type(self) -> str:
        return "noteagent-gateway"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "GatewayChatModel":
        return self.__class__(
            gateway=self.gateway,
            bound_tools=tuple(tools),
            system_prompt=self.system_prompt,
        )

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        # 组装单 prompt：系统指令 + 工具 schema + 多轮历史
        tools_desc = ""
        for t in self.bound_tools:
            schema = t.args_schema.model_json_schema() if getattr(t, "args_schema", None) else {}
            tools_desc += f"- {t.name}: {t.description or ''} 参数={json.dumps(schema, ensure_ascii=False)}\n"
        history = "\n".join(
            f"{m.type}: {m.content if isinstance(m.content, str) else json.dumps(m.content, ensure_ascii=False)}"
            for m in messages
        )
        sys_text = getattr(self.system_prompt, "content", "") or "你是树重建 Agent。"
        # 明确约束输出协议：要调用工具时只输出严格 JSON，禁止 DSML/XML/Markdown 围栏/自由文本混排，
        # 与 _parse_gateway_response 的解析协议保持一致（真实 Provider 会自由发挥成各类格式导致解析失败）。
        protocol = (
            "\n【输出协议（必须严格遵守）】\n"
            "每一步只输出一个 JSON 对象，禁止输出任何其他内容（不要 DSML 标签、不要 XML、"
            "不要 Markdown 围栏、不要解释性文字、不要 `to=工具` 混排文本）。\n"
            "要调用工具时输出：{\"tool\": \"工具名\", \"args\": {...}}\n"
            "若要一次调用多个工具，输出：{\"tool_calls\": [{\"tool\": \"工具名\", \"args\": {...}}, ...]}\n"
            "完成考量、提交终态时输出 TreeAssignment JSON："
            "{\"tree_id\": \"目标树ID或NEW\", \"parent_event_id\": null, \"confidence\": 0~1, "
            "\"evidence\": \"依据\", \"action\": \"append\"}\n"
        )
        prompt = f"【系统】{sys_text}\n{protocol}\n【可用工具】\n{tools_desc}\n【消息历史】\n{history}"
        raw = self.gateway.chat(prompt)
        return ChatResult(
            generations=[ChatGeneration(message=_parse_gateway_response(raw, call_id=f"c{len(self.gateway.calls)}"))]
        )


class TreeBuilder:
    """树重建 ReAct Agent 运行入口。

    对单个待挂接事件运行多轮工具调用（search_candidate_trees / read_note /
    get_tree_timeline / search_events / submit_assignment），返回 TreeAssignment。
    `max_steps` 限制工具往返步数（DESIGN.md 6.1：最大 12 步），配合 LLMGateway 成本护栏。
    """

    def __init__(
        self,
        gateway: Any,
        max_steps: int = 12,
        backends: Mapping[str, Any] | None = None,
        system_prompt: Any = DEFAULT_SYSTEM_PROMPT,
    ) -> None:
        self.gateway = gateway
        self.max_steps = max_steps
        self.backends = backends or {}
        self.system_prompt = system_prompt
        self.tools = build_tools(self.backends)

    def run(
        self,
        event: Mapping[str, Any],
        verified_tree_ids: set[str] | None = None,
    ) -> TreeAssignment:
        """对单个事件执行多轮 ReAct，返回最终 TreeAssignment。

        - 超出步数 / Agent 输出非法 → 抛 ValueError，由调用方按失败隔离处理。
        - verified_tree_ids：交由 tools.py 的 `validate_assignment` 做追加原则校验（可选）。
        """
        if not 1 <= self.max_steps <= 12:
            raise ValueError("max_steps 必须为 1-12")

        model = GatewayChatModel(
            gateway=self.gateway, system_prompt=self.system_prompt
        ).bind_tools(self.tools)
        graph = create_agent(model, self.tools, system_prompt=self.system_prompt)

        user_msg = HumanMessage(
            content=(
                "请为以下未挂接事件做树重建判定。可先用工具检索候选树/回读笔记，"
                "最终调用 submit_assignment 提交终态（或输出 TreeAssignment JSON）。"
                f"\n事件: {json.dumps(dict(event), ensure_ascii=False)}"
            )
        )
        result = graph.invoke(
            {"messages": [user_msg]},
            config={"recursion_limit": self.max_steps + 4},
        )
        messages = result.get("messages", [])
        # 取最后一条 ai/tool 文本作为最终判定内容
        last_ai = next(
            (m for m in reversed(messages) if getattr(m, "type", "") == "ai"),
            None,
        )
        if last_ai is None or not getattr(last_ai, "content", ""):
            raise ValueError("Agent 未产出终态判定")
        content = last_ai.content
        if isinstance(content, list):
            content = next((c.get("text", "") for c in content if isinstance(c, dict)), "")
        if isinstance(content, str):
            normalized = content.strip()
            if normalized.startswith("```"):
                normalized = normalized.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            data = json.loads(normalized)
        else:
            data = content
        data = {**dict(data), "event_id": event.get("event_id"), "note_id": event.get("note_id")}
        return TreeAssignment.model_validate(data)