# Prior art

What the projects named in CLAUDE.md actually do, verified against their repositories
on 2026-11-16. This records design decisions worth copying or rejecting; the spec in
`docs/spec.md` is authoritative.

## qmd (tobi/qmd)

MIT, TypeScript, SQLite with FTS5 and sqlite-vec, local GGUF models through
node-llama-cpp. Active and widely used.

Pipeline:

- **Chunking.** About 900 tokens with 15% overlap. Break points are scored, preferring
  headings, code fences, horizontal rules and paragraph boundaries. Never breaks inside
  a code block. Optional tree-sitter chunking for source code (not needed here).
- **Query expansion.** A fine-tuned 1.7B model emits typed sub-queries: `lex` goes to
  BM25, `vec` and `hyde` go to vector search. The original query is also sent to both
  legs, with double weight in fusion.
- **Fusion.** Reciprocal Rank Fusion with k=60, plus a small bonus for top-ranked hits.
  The top 30 candidates go on to reranking.
- **Rerank.** Qwen3-Reranker-0.6B cross-encoder using yes/no logprobs. The final score
  blends retrieval rank and reranker score with position-aware weights, so the top
  retrieval hits keep more influence than the tail.
- **Metadata filters.** Frontmatter under `qmd.metadata`, queried with a typed JSON
  expression tree. Discovery commands list keys and value counts.
- **MCP tools.** `query` (typed sub-queries, filters, limit, score threshold, rerank
  toggle), `get` (by path or docid, with line ranges), `multi_get`, `status`,
  `metadata`. Transports: stdio and HTTP (`/mcp`, `/query`, `/metadata`, `/health`).
  The HTTP transport validates `Origin` and `Host` against DNS rebinding.
- **Models.** embeddinggemma-300M for embeddings, qwen3-reranker-0.6b, and a fine-tuned
  query-expansion model. An OpenAI-compatible remote embedding endpoint is supported.

What pg-retrieve takes from it: the chunker parameters, typed `lex`/`vec` queries,
RRF with k=60 and the original query in both legs, the rerank toggle per request, and
the `get` with line ranges. What it drops: `hyde` sub-queries (cost on a gateway LLM),
the cross-encoder (no such endpoint), and SQLite.

## picoqmd (altSeneca/picoqmd)

Go reimplementation of qmd for small machines. Same models and pipeline. Brute-force
vector search, no chunking yet, small community. Useful only as confirmation that the
qmd pipeline shape is portable.

## ehc-io/qmd

A fork of tobi/qmd with the same architecture and MCP tools (`qmd_search`,
`qmd_vector_search`, `qmd_deep_search`, `qmd_get`, `qmd_multi_get`, `qmd_status`).
Nothing additional to learn.

## lazyqmd

A terminal UI over the qmd MCP daemon. It confirms the collection model and the HTTP
daemon mode, nothing more.

## hybrid-rag and pg-hybrid-search

Not yet verified against their repositories. Add findings here when checked.
