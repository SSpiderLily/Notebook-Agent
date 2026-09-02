#!/usr/bin/env python3
"""
NoteAgent - 本地 Web 服务启动入口（智能笔记整理与思维链生成工具）。

真正启动 app：从 `src.infra.config.get_settings()` 统一读取路径/模型/监听地址，
构造 TaskManager，用 uvicorn 在 `settings.host:settings.port` 上运行。
所有入口都从仓库根目录运行：`python -m src.main`。
"""


def main() -> None:
    from src.infra.config import get_settings
    settings = get_settings()
    settings.ensure_runtime_dirs()

    from src.api.app import create_app
    from src.api.task_manager import TaskManager

    tm = TaskManager(
        vault_dir=settings.vault_dir,
        db_path=settings.db_path,
        runs_dir=settings.runs_dir,
        recordings_dir=settings.llm_recordings_dir,
        mode=settings.llm_mode,
        transport=None,
    )
    app = create_app(tm)

    import uvicorn
    print(f"NoteAgent 启动中：http://{settings.host}:{settings.port}  mode={settings.llm_mode} model={settings.model_name}")
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()