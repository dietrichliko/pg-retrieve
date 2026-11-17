# CLAUDE.md — pg-retrieve

Hybrid search service over Markdown documents on PostgreSQL. It replaces qmd for the
DELPHI Knowledge Base (AMALDI) and serves both humans (through the KB UI) and agents
(through MCP).

CERN is the reference deployment, not the only one. Nothing CERN-specific goes into
code: every endpoint, model name, credential and feature flag is configuration. The
service must run anywhere with PostgreSQL 17 plus pgvector and an OpenAI-compatible
model endpoint. CERN-specific facts live in `docs/cern-infrastructure.md`.

## Context

- **Corpus.** 3,277 DELPHI documents (230 MB of Markdown, about 150k chunks of 900
  tokens): internal notes, conference contributions, publications and theses. About 40%
  are scans, and about 200 long documents are not English. They have already been
  converted to Markdown with Docling (plus VLM symbol fixes), with catalog metadata in
  YAML frontmatter and an LLM card per document (topics, detectors, years). Locations
  and formats are in `docs/corpus.md`.
- **Status.** This is a new design, not a port. It takes ideas from qmd, hybrid-rag and
  pg-hybrid-search. See `docs/prior-art.md` for what was verified about qmd.
- **Why.** qmd on lxplus could only run lexical and semantic search. Query expansion and
  reranking had to be switched off. pg-retrieve must provide all four stages with the
  search service itself running without a GPU. Model work goes to an endpoint, or to a
  GPU host at ingest time.
- **Meeting outcomes.** The CERN ML help desk discussion is recorded in
  `docs/cern-ml-helpdesk-discussion-points.md`. Its outcomes section is the reason for
  the pluggable model backends below.
- **Consumers.**
  - The prototype UI: a small JS Markdown viewer with chat.
  - The agent loop.
  - Other agents via MCP.
  - The source-code MCP (delphi-pam-mcp) stays a separate service. Do not merge it in.

## Hard constraints

- **Database.** PostgreSQL 17 with pgvector. Assume pgvector 0.7.x as the floor: the
  reference instance has 0.7.0, which has no iterative HNSW scan, so filtered vector
  queries must not depend on pgvector 0.8 features. Use only extensions available on a
  managed service; the reference list is in `docs/cern-infrastructure.md`. ParadeDB is
  ruled out.
- **BM25.** Lexical search sits behind an interface with two backends:
  - `pg_textsearch` (BM25), preferred where installed. It needs
    `shared_preload_libraries` and a restart, so managed services rarely have it.
  - Built-in `tsvector`/`ts_rank_cd`, the fallback and the default.
  The backend is chosen by config, with auto-detection from `pg_available_extensions`
  as a convenience.
- **Model backends.** Every model stage (embedding, query expansion, rerank) sits behind
  an interface with two backends, chosen by config:
  - `openai`: any OpenAI-compatible endpoint (`/v1/models`, `/embeddings`,
    `/chat/completions`). At CERN this is the AI gateway, or a model served on
    ml.cern.ch through KServe.
  - `local`: in-process GGUF models as in qmd, for GPU hosts at ingest or evaluation time.
    Never required on the search service.
  Base URL, key, model names and embedding dimension all come from config, never from
  code. Prefer small models for expansion and reranking; the gateway is heavily used.
- **Embedding compatibility.** Vectors from both backends must be interchangeable, and
  the gateway's serving options are undocumented. Store a model fingerprint (repository,
  revision, dimension, normalisation, prompt prefixes) with every index, and provide a
  compatibility check that embeds sample texts through both backends and compares them.
  A fingerprint mismatch means a new index, never mixing.
- **Indexes.** A collection has one or more named indexes, each bundling a chunker,
  an embedding model and a normaliser over the same documents, one of them the default.
  Search takes an optional index name, so the evaluation can compare chunking, embedding
  and symbol handling side by side. Re-embedding is a new index plus a default switch.
  Spec: `docs/spec.md` section 3.
- **Task prompts.** The client adds the per-model query and document prefixes itself
  (for example EmbeddingGemma's `task: search result | query: ...`). They are config per
  model family, not hard-coded.
- **No dedicated reranker on the endpoint.** Reranking is an optional, switchable stage
  with backends: `llm` (yes/no scoring with `top_logprobs` when the endpoint returns
  them, otherwise a parsed numeric score) and `cross-encoder` (local Qwen3-Reranker).
  Everything must work with it turned off.
- **Access control.** Documents live in named collections, and every right is per
  collection: `read`, `write` or `admin`, granted to principals or groups, with service
  administrators from config. A collection a principal cannot read is invisible, not
  forbidden. Authentication and group membership are plugins: pg-retrieve-issued tokens
  always work, OIDC (CERN SSO, Authentik) and group sources (token claims, CERN
  e-groups, static lists) are selected by config. Several groups such as different
  experiments share one service. Bulk re-embedding is an admin operation. Spec:
  `docs/spec.md` section 1.
- **Deployment.** A container image with config from env and secrets, and no local
  state. The reference target is OpenShift (CERN PaaS). Bulk embedding runs elsewhere (a T4
  on ml.cern.ch) and posts chunks with vectors through the API; only the service holds
  database credentials. Assume embeddings can be recreated.
- **Licensing.** The corpus includes preprints. Content may only be sent to the
  configured model endpoint, and at CERN that endpoint must be the on-premises gateway
  models only. Never add a second outbound destination. Document licensing and agent
  sandboxing are out of scope for this service.

## Retrieval design

1. **Chunking.** Heading-aware, following qmd (about 900 tokens, 15% overlap, break at
   headings, code fences and paragraphs, never inside a code block). Keep a document id,
   heading path, character offsets and page range on every chunk. Page-level provenance
   is mandatory: citations must point to the page in the source PDF. The Markdown has
   no page markers yet; the plan is page markers added upstream at export, with the
   Docling JSON as fallback source (see `docs/corpus.md`). Until then, pages are reported
   as unknown, never guessed. Semantic chunking for long chapters is a later option;
   keep the chunker pluggable.
2. **Lexical leg.** BM25 via `pg_textsearch`, or the tsvector fallback. Both backends
   apply a normaliser plugin to documents and queries, so physics symbol spellings
   (`αs`, `alpha_s`, `e+e-`) map to one token as qmd's `searchnorm` does. Built-ins are
   `none` and `physics`; `physics` is a port of the sibling repo's normaliser with its
   vocabulary in a lexicon JSON file, so other domains change data, not code. The
   normaliser name and version are stored per index; a change means a new index, which rebuilds only the
   lexical index. Spec: `docs/spec.md` section 2.
3. **Vector leg.** pgvector with an HNSW index, using cosine distance.
4. **Fusion.** Reciprocal Rank Fusion of the two legs, with k configurable (default 60).
   The original query is always sent to both legs in addition to any expanded queries.
5. **Rerank (optional).** Scoring of the fused top-k by the configured rerank backend,
   with a cache and a timeout. On failure, return the fused order.
6. **Query handling.**
   - Human queries: expansion into a lexical and a vector query before search, by one of
     two backends: `prompt` (a small chat model with instructions) or `qmd` (the
     `tobil/qmd-query-expansion-1.7B` model, local or served, emitting typed lines).
     HyDE is a third optional output, off by default until evaluated.
   - Agent queries: the agent supplies separate `lex` and `vec` queries itself, so the
     API must accept them separately and skip expansion.
7. **Filters.** Filter by metadata (topics, detectors, years, document type) before
   ranking. With pgvector 0.7 a selective filter can starve the HNSW scan, so the vector
   leg must raise `ef_search` or over-fetch when a filter is active.

## Interfaces

- **Python library core.** Ingest, search and get_chunk/get_document.
- **HTTP API.** Search with optional lex/vec/filters/rerank flags, plus fetch chunk and
  fetch document, all scoped to the caller's readable collections. Results include
  score components (bm25, vector, rrf, rerank) so they can be debugged. Plus the
  minimal auth endpoints: `/me`, personal tokens, grants, and one static page for them.
- **MCP server.** A thin wrapper over the same core, with the same access checks (token
  from the request over HTTP, from the environment over stdio). Tools: `search`,
  `get_document`, `get_chunk`. Tool descriptions tell agents to write separate lex and
  vec queries.
- **Ingest CLI.** An API client: chunks with the shared chunker, embeds locally when a
  GPU is present, and posts batches with vectors; otherwise posts text and the server
  embeds. Idempotent and keyed on content hash. Needs `write` on the target
  collection; re-embedding needs `admin`.

## Stack (proposed — confirm before scaffolding)

- Python ≥ 3.12 with `uv`, FastAPI, psycopg 3 (async), the pgvector Python adapter,
  the official `mcp` SDK and Pydantic for config and schemas.
- The `local` model backend as an optional extra (candidate: `llama-cpp-python`, as
  qmd uses llama.cpp). Not installed in the service image. Confirm before adding.
- SQL migrations as plain numbered `.sql` files, runnable without superuser rights.
- Tests with pytest against a local PostgreSQL 17 + pgvector container. Mock model
  calls in unit tests.

## Working rules

- **Spec first.** Write or update `docs/spec.md` before implementing a component. Ask
  when the spec is ambiguous instead of guessing.
- **Keep it small.** No framework beyond the stack above without asking.
- **Config.** All endpoints, model names, k values and feature flags live in config,
  with sane defaults for local dev. CERN values are defaults in the example env file,
  not in code.
- **Evaluation lives in a separate repo.** It treats pg-retrieve as a black box via
  HTTP/MCP. Do not add benchmark sets or quality metrics here. This repo only provides
  what the eval needs:
  - Per-request switches for each stage (lex-only, vec-only, fused, fused+rerank), so
    the eval can compare them without redeploying.
  - Score components in every result.
  - An `/info` endpoint returning the service version, a config hash and the models in
    use, so every eval run can record exactly what it measured.
  - Stable document and chunk ids across re-ingests.
- **Tests.** Tests here are functional, not quality measurements: a handful of fixture
  documents and queries that check the pipeline returns the right chunks with correct
  provenance.
- **Dependencies.** Do not add one that a managed PostgreSQL or a plain container
  platform cannot support.

## Docs and skills

- `docs/spec.md` — the design spec. Sections 1 (collections and access control),
  2 (lexical normaliser) and 3 (indexes) are written; the other sections come before
  their components.
- `docs/cern-infrastructure.md` — verified facts about DBOD, the AI gateway and
  ml.cern.ch, with dates. Update it when re-checked.
- `docs/prior-art.md` — what qmd and its relatives actually do.
- `docs/corpus.md` — where the DELPHI Markdown, metadata and existing qmd indexes live,
  and what they contain.
- `docs/cern-ml-helpdesk-discussion-points.md` — the questions put to the CERN ML help
  desk and the outcomes.
- `docs/ALTERNATIVES.md` — survey of qmd alternatives.
- `.claude/skills/probe-model-endpoint` — check an OpenAI-compatible endpoint: models,
  embedding dimension, chat latency.
- `.claude/skills/check-postgres-target` — check a PostgreSQL instance: version,
  extensions, limits, and which lexical backend to use.

## Todo

Verified facts go to `docs/cern-infrastructure.md`; decisions go to `docs/spec.md`.

- **Check the existing DBOD instance** for `pg_textsearch` and the pgvector version
  with the `check-postgres-target` skill, before asking DBOD for anything.
- **Embedding model.** Candidates: `embeddinggemma-300m` on the gateway (multilingual,
  dimension to measure) and Qwen3-Embedding run by us on a T4 at ml.cern.ch.
  `e5-large-v2` is out (English only, 512 tokens). Measure dimensions with the
  `probe-model-endpoint` skill, then compare gateway and local vectors with the
  compatibility check.
- **Reranking.** Test whether the gateway returns `top_logprobs` for a small chat model.
  Measure latency and cost; the self-hosted cross-encoder is the fallback.
- **Query expansion.** Compare the prompt backend on a small gateway model with the qmd
  model served on ml.cern.ch. Evaluate HyDE before enabling it.
- **Chunking.** Evaluate semantic chunking for long chapters against the heading-aware
  default. The eval repo measures this; here only the pluggable interface is needed.
- **Ingest source.** For DELPHI it is the EOS tree described in `docs/corpus.md`
  (Markdown with frontmatter, plus a card JSON per document). Decide how the ingest CLI
  on PaaS reaches it: an EOS mount, a copy, or an upload.
- **Page markers.** Agree with the delphi-knowledge-base repository on the page marker
  format (its backlog plans `page_break_placeholder`), or implement the Docling JSON
  fallback in the ingest.
