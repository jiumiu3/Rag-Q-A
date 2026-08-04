#!/usr/bin/env python3
import argparse
import json

from app.api.agent import get_workflow_service


def main() -> int:
    parser = argparse.ArgumentParser(description="从持久化状态重放 M9 工作流")
    parser.add_argument("session_id")
    parser.add_argument("--start-node")
    args = parser.parse_args()
    state = get_workflow_service().replay(args.session_id, args.start_node)
    print(json.dumps(state.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
