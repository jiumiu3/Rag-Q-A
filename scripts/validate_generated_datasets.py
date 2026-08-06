#!/usr/bin/env python3
import json
from pathlib import Path

from app.evaluation.generated import validate_datasets

result = validate_datasets(Path("evaluation"), Path("data/knowledge.db"))
print(json.dumps(result, ensure_ascii=False, indent=2))
raise SystemExit(0 if result["valid"] else 1)
