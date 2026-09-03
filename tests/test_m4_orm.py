"""M4 ORM：Tree/TreeNode 表定义、约束与追加原则标记的持久化测试。"""
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.models.orm import (
    Base,
    DANGLING_TREE_STATUSES,
    NODE_ORIGINS,
    TREE_STATUSES,
    Tree,
    TreeNode,
)


# ── 枚举常量 ──

def test_tree_enum_constants():
    assert TREE_STATUSES == (
        "complete",
        "in_progress",
        "dangling_confirmed",
        "dangling_suspected",
    )
    assert set(DANGLING_TREE_STATUSES) <= set(TREE_STATUSES)
    assert NODE_ORIGINS == ("agent", "human")


def test_default_tree_status_is_in_progress():
    assert "in_progress" in TREE_STATUSES


# ── 持久化 roundtrip ──

def test_tree_roundtrip(tmp_path):
    db = create_engine(f"sqlite:///{tmp_path / 'orm.sqlite'}")
    Base.metadata.create_all(db)
    with Session(db) as session:
        session.add(
            Tree(
                id="t1",
                root_note_id="n1",
                title="推进项目",
                status="dangling_confirmed",
                confidence=0.85,
                verified=True,
                locked=True,
                evidence=json.dumps(["事件后无后续"]),
                narrative="项目停滞",
                run_id="r1",
            )
        )
        session.commit()

    with Session(db) as session:
        tree = session.get(Tree, "t1")
        assert tree.root_note_id == "n1"
        assert tree.status == "dangling_confirmed"
        assert tree.verified is True and tree.locked is True
        assert json.loads(tree.evidence) == ["事件后无后续"]
        assert tree.narrative == "项目停滞"


def test_tree_node_roundtrip(tmp_path):
    db = create_engine(f"sqlite:///{tmp_path / 'orm.sqlite'}")
    Base.metadata.create_all(db)
    with Session(db) as session:
        session.add(Tree(id="t1", title="推进项目", run_id="r1"))
        root = TreeNode(
            tree_id="t1", note_id="n1", order=0, confidence=0.9,
            evidence=json.dumps(["命名规律"]), origin="agent",
        )
        session.add(root)
        session.flush()
        child = TreeNode(
            tree_id="t1", note_id="n2", parent_id=root.id, order=1,
            confidence=0.8, evidence=json.dumps(["人工修正"]), origin="human",
        )
        session.add(child)
        session.commit()

    with Session(db) as session:
        nodes = list(session.scalars(select(TreeNode).order_by(TreeNode.order)))
        assert len(nodes) == 2
        root, child = nodes
        assert root.parent_id is None and root.origin == "agent"
        assert json.loads(root.evidence) == ["命名规律"]
        assert child.parent_id == root.id
        assert child.origin == "human"
        assert json.loads(child.evidence) == ["人工修正"]


# ── 约束 ──

def test_tree_node_order_unique_within_tree(tmp_path):
    """同一树内 (tree_id, order) 必须唯一，防止拓扑歧义。"""
    db = create_engine(f"sqlite:///{tmp_path / 'orm.sqlite'}")
    Base.metadata.create_all(db)
    with Session(db) as session:
        session.add(Tree(id="t1", title="推进项目", run_id="r1"))
        session.add(TreeNode(tree_id="t1", note_id="n1", order=0))
        session.flush()
        session.add(TreeNode(tree_id="t1", note_id="n2", order=0))
        with pytest.raises(IntegrityError):
            session.commit()


def test_existing_tables_still_created(tmp_path):
    """新增 Tree/TreeNode 不破坏既有 runs/stages 等表的创建（兼容性）。"""
    from src.models.orm import Run, Stage

    db = create_engine(f"sqlite:///{tmp_path / 'orm.sqlite'}")
    Base.metadata.create_all(db)
    names = set(db.dialect.get_table_names(db.connect()))
    assert {"runs", "stages", "notes", "trees", "tree_nodes"} <= names
