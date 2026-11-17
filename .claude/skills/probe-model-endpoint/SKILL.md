---
name: probe-model-endpoint
description: Probe an OpenAI-compatible model endpoint (default CERN AI gateway) to list models, measure embedding dimensions, and time a chat call used for query expansion or LLM reranking. Use before choosing embedding or chat models, when a model call fails, or when updating docs/cern-infrastructure.md.
---

# Probe an OpenAI-compatible model endpoint

Use this to turn "which models and dimensions do we have" into measured facts.

## Inputs

Environment variables, read by the script:

- `MODEL_API_KEY` (required): the bearer key.
- `MODEL_BASE_URL` (default `https://aigw.cern.ch/`).
- `EMBED_MODELS` (default `embeddinggemma-300m,e5-large-v2`): comma list to probe.
- `CHAT_MODEL` (default `qwen3.8-27b-fp16`): model to time for a rerank-style call.

Never print or log the key. Never pass corpus text to the probe; it uses a fixed
harmless sentence.

## Steps

1. Run the script with an isolated interpreter:

   ```
   python3 -I .claude/skills/probe-model-endpoint/probe.py
   ```

2. Read the output:
   - `models`: the ids the key can see. If an expected model is missing, the team
     (sandbox vs dedicated) is probably the reason, not the endpoint.
   - `embedding`: for each model the vector length, the input-token count reported
     by the endpoint, and the latency. The length is the value to put in config as
     `embedding_dim`.
   - `chat`: latency and token usage for a single rerank-style scoring prompt. Multiply
     by the rerank top-k to estimate per-query cost.
   - Any HTTP 401, 403 or 429 is reported as such. 429 means quota; see the team
     request form in `docs/cern-infrastructure.md`.

3. Record the results in `docs/cern-infrastructure.md` under the gateway section with
   today's date, and update the embedding open question in `CLAUDE.md` if resolved.

## Notes

- The script uses only the standard library, so it runs on lxplus or in the container.
- The gateway may return embedding dimensions that differ from the model's public
  specification (Matryoshka truncation). Always use the measured value.
