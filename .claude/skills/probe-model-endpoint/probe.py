#!/usr/bin/env python3
"""Probe an OpenAI-compatible endpoint: models, embedding dims, chat latency.

Standard library only. Reads MODEL_API_KEY, MODEL_BASE_URL, EMBED_MODELS, CHAT_MODEL.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("MODEL_BASE_URL", "https://aigw.cern.ch/").rstrip("/") + "/"
KEY = os.environ.get("MODEL_API_KEY")
EMBED_MODELS = [m for m in os.environ.get("EMBED_MODELS", "embeddinggemma-300m,e5-large-v2").split(",") if m]
CHAT_MODEL = os.environ.get("CHAT_MODEL", "qwen3.8-27b-fp16")
SAMPLE = "The DELPHI detector at LEP measured hadronic Z decays between 1989 and 2000."


def call(path, body=None):
    url = BASE + path.lstrip("/")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("Authorization", f"Bearer {KEY}")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "pg-retrieve-probe/0.1")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            out = json.load(r)
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "detail": e.read()[:300].decode(errors="replace")}, time.perf_counter() - t0
    except (urllib.error.URLError, TimeoutError) as e:
        return {"error": str(e)}, time.perf_counter() - t0
    return out, time.perf_counter() - t0


def main():
    if not KEY:
        print("MODEL_API_KEY is not set", file=sys.stderr)
        return 2
    report = {"base_url": BASE}

    models, dt = call("v1/models")
    report["models"] = sorted(m.get("id") for m in models.get("data", [])) if "data" in models else models

    report["embedding"] = {}
    for m in EMBED_MODELS:
        out, dt = call("embeddings", {"model": m, "input": SAMPLE})
        if "data" in out:
            report["embedding"][m] = {
                "dim": len(out["data"][0]["embedding"]),
                "prompt_tokens": out.get("usage", {}).get("prompt_tokens"),
                "latency_s": round(dt, 3),
            }
        else:
            report["embedding"][m] = out

    prompt = (
        "Score how well the passage answers the question from 0 to 10. Reply with the number only.\n"
        f"Question: When did DELPHI take data?\nPassage: {SAMPLE}"
    )
    out, dt = call("chat/completions", {
        "model": CHAT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 8,
        "temperature": 0,
    })
    if "choices" in out:
        report["chat"] = {
            "model": CHAT_MODEL,
            "reply": out["choices"][0]["message"]["content"].strip(),
            "usage": out.get("usage"),
            "latency_s": round(dt, 3),
        }
    else:
        report["chat"] = {"model": CHAT_MODEL, **out}

    json.dump(report, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
