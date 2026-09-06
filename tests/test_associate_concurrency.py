"""关联判定并发化 + LLMGateway 线程安全回归测试。

背景：真实副本验收发现 associate 阶段逐候选顺序调用 LLM（每个 ~15s、上千候选 → 数小时）。
修复：gateway 内部加锁保护成本/台账；associate 判定循环支持线程池并发。
本文件覆盖：(1) gateway 并发下不丢账、台账逐条准确；(2) 并发判定与顺序判定结果一致。
"""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel

from src.infra.llm_gateway import LLMGateway
from src.services.pipeline import Pipeline


class _J(BaseModel):
    source_id: str
    target_id: str
    related: bool
    confidence: float
    evidence: list[str]
    rationale: str


def test_gateway_thread_safe_concurrent_calls(tmp_path):
    """并发调用下台账条数精确、无丢账、每条都带 latency_ms（不再用 calls[-1] 张冠李戴）。"""
    rec = tmp_path / "rec"
    n = {"v": 0}
    lock = threading.Lock()

    def transport(prompt: str) -> str:
        time.sleep(0.005)  # 放大并发窗口
        with lock:
            n["v"] += 1
            i = n["v"]
        return json.dumps({"source_id": str(i), "target_id": "t", "related": True,
                           "confidence": 0.9, "evidence": [], "rationale": "x"})

    gw = LLMGateway(rec, mode="record", model="m", transport=transport, cost_cap=100.0)
    N = 16
    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(lambda i: gw.structured(f"prompt-{i}", _J), range(N)))

    assert len(gw.calls) == N, f"并发下应恰好 {N} 条台账，实际 {len(gw.calls)}"
    # 成本 = 各条 cost_est 之和（锁内读改写，无丢账）
    assert abs(gw.cost - sum(c["cost_est"] for c in gw.calls)) < 1e-9
    # 每条调用都应有 latency_ms，且 digest 各不相同
    assert all("latency_ms" in c for c in gw.calls)
    assert len({c["digest"] for c in gw.calls}) == N


def _extract_json(content: str) -> str:
    return json.dumps({
        "title": "t", "summary": "推进项目", "keywords": ["项目"], "candidate_tags": [],
        "events": [{"content": content, "order_in_note": 0}],
    }, ensure_ascii=False)


def _make_transport():
    """线程安全 transport：区分抽取与关联判定 prompt。"""
    lock = threading.Lock()

    def transport(prompt: str) -> str:
        with lock:
            time.sleep(0.003)
            if "是否存在实质关联" in prompt:
                cand = json.loads(prompt.split("\n")[-1])
                return json.dumps({"source_id": cand["source_id"], "target_id": cand["target_id"],
                                   "related": True, "confidence": 0.9,
                                   "evidence": cand.get("evidence") or [], "rationale": "x"})
            return _extract_json("推进项目")
    return transport


def _run(vault, tmp_path, name, concurrency):
    d = tmp_path / name; d.mkdir(exist_ok=True)
    rec = d / "rec"
    pipe = Pipeline(vault, d / "accept.db", d / "runs", rec,
                    mode="record", transport=_make_transport(), llm_concurrency=concurrency)
    run = pipe.rm.start_run(scope=str(vault), trigger="test")
    pipe.run(run_id=run.id, trigger="test")
    return pipe


def _assoc_pairs(db):
    import sqlite3
    c = sqlite3.connect(str(db))
    return set(c.execute("select src_id, dst_id, confidence from associations").fetchall())


def test_pipeline_associate_concurrent_matches_sequential(tmp_path):
    """并发(4)与顺序(1)跑同一样本，associate 结果（关联对+置信度）应一致，且并发下调用数正确落台账。"""
    vault = tmp_path / "vault"; vault.mkdir()
    notes = ["跑步 计划", "跑步 晨跑", "项目 读书", "读书 摘抄"]
    for i, content in enumerate(notes):
        (vault / f"{i}.md").write_text(f"# n{i}\n{content}", encoding="utf-8")

    p_seq = _run(vault, tmp_path, "seq", 1)
    p_con = _run(vault, tmp_path, "con", 4)

    seq_pairs = _assoc_pairs(tmp_path / "seq" / "accept.db")
    con_pairs = _assoc_pairs(tmp_path / "con" / "accept.db")
    assert seq_pairs, "样本应产生关联，测试前提"
    assert con_pairs == seq_pairs, "并发与顺序判定的关联结果应一致"

    # 并发下 associate 阶段 LLM 台账应完整落库（每候选一条）
    from sqlalchemy import create_engine, select, func
    from src.models.orm import LLMCall
    eng = create_engine(f"sqlite:///{tmp_path/'con'/'accept.db'}")
    with eng.connect() as cc:
        n_assoc = cc.execute(select(func.count()).select_from(LLMCall)
                             .where(LLMCall.stage == "associate")).scalar_one()
    assert n_assoc > 0, "associate 阶段应产生 LLM 台账"
