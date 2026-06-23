from __future__ import annotations

import json as j


def print_json(data):
    print(j.dumps(data, indent=2, default=str, ensure_ascii=False))
