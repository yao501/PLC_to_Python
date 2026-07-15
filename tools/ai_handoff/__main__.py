from __future__ import annotations

import argparse
import os
from pathlib import Path

from .scheduler import AsyncExecutionCoordinator, default_runtime_dir
from .server import run


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="启动只读 AI 交接进度面板")
    parser.add_argument("--source", type=Path, default=root / "docs" / "AI_REVIEW_HANDOFF.md")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--claude-proxy",
        default=os.environ.get("AI_HANDOFF_CLAUDE_PROXY"),
        help="Claude Code 使用的 HTTP(S) 代理，例如 http://127.0.0.1:6789",
    )
    parser.add_argument(
        "--enable-external-processes",
        action="store_true",
        help="显式打开 Claude/Codex 事件执行；省略时始终为 dry-run",
    )
    parser.add_argument(
        "--retry-failed-key",
        metavar="IDEMPOTENCY_KEY",
        help="用户处置失败后，授权指定幂等键重试一次；执行后直接退出",
    )
    args = parser.parse_args()
    if args.retry_failed_key:
        result = AsyncExecutionCoordinator(default_runtime_dir(args.source)).authorize_retry(
            args.retry_failed_key
        )
        print(f"已授权一次重试: {result['idempotency_key']}")
        return
    run(
        args.source,
        args.host,
        args.port,
        claude_proxy=args.claude_proxy,
        enable_external_processes=args.enable_external_processes,
    )


if __name__ == "__main__":
    main()
