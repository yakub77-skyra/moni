"""List currently-available free OpenRouter models (pricing prompt == 0)."""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, "E:/OpenMontage")

# Load .env like the tools do
env_path = Path("E:/OpenMontage/.env")
for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, _, value = line.partition("=")
    os.environ.setdefault(key.strip(), value.strip())

key = os.environ.get("OPENROUTER_API_KEY", "")
req = urllib.request.Request(
    "https://openrouter.ai/api/v1/models",
    headers={"Authorization": f"Bearer {key}"},
)
with urllib.request.urlopen(req, timeout=45) as resp:
    data = json.loads(resp.read().decode("utf-8"))

free = []
for model in data.get("data", []):
    pricing = model.get("pricing") or {}
    try:
        prompt = float(pricing.get("prompt", "1"))
        completion = float(pricing.get("completion", "1"))
    except (TypeError, ValueError):
        continue
    if prompt == 0.0 and completion == 0.0:
        free.append({
            "id": model.get("id"),
            "context": model.get("context_length"),
            "modality": (model.get("architecture") or {}).get("modality"),
            "tools": bool(model.get("supported_parameters") and
                          "tools" in model["supported_parameters"]),
        })

free.sort(key=lambda m: (-(m["context"] or 0), m["id"] or ""))
print(f"total models: {len(data.get('data', []))} | free: {len(free)}")
for model in free:
    print(f"  {model['id']:<58} ctx={model['context']:<8} {model['modality']}")
