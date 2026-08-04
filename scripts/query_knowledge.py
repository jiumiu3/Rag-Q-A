#!/usr/bin/env python3
import argparse
import json

from app.api.qa import get_qa_service


def main() -> int:
    parser = argparse.ArgumentParser(description="M5 带引用规范问答演示")
    parser.add_argument("question")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    answer, result = get_qa_service().ask(args.question, args.top_k)
    print(
        json.dumps(
            {
                "answer": answer.model_dump(mode="json"),
                "trace": result.trace.model_dump(mode="json"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
