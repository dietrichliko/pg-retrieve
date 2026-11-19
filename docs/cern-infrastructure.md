# CERN infrastructure for pg-retrieve

Verified facts about the reference deployment. Everything here is configuration input,
not design input: the service must also run outside CERN. Each section carries the date
it was last checked. Re-check before relying on a version number.

## DBOD PostgreSQL (checked 2026-11-16)

Source: [DBOD extensions](https://dbod-user-guide.web.cern.ch/getting_started/PostgreSQL/extensions),
[configuration files](https://dbod-user-guide.web.cern.ch/instance_management/configuration_files/),
[versioning policy](https://dbod-user-guide.web.cern.ch/getting_started/PostgreSQL/versioning-policy/).

Extensions in the DBOD server package that matter here:

| Extension | Version | Use |
|---|---|---|
| `vector` (pgvector) | 0.7.0 listed | The guide's list is out of date: live instances have 0.8.0 (PG 15) and 0.8.2 (PG 17). |
| `pg_trgm` | 1.6 | Fuzzy matching, optional. |
| `unaccent` | present | Text search normalisation. |
| `btree_gin` | present | Composite metadata indexes. |
| `pg_cron` | present | Background jobs, if ever needed. |
| `pg_textsearch` | absent | Would need a SNOW ticket; see below. |

Other facts:

- Missing extensions: open a SNOW ticket to the DBOD service. The team evaluates the
  request and builds a new RPM. Security review applies.
- `postgresql.conf` is editable through the DBOD web file editor. `shared_buffers` is
  capped at 2 GB and `max_connections` at 150. Other changes need a ticket.
- A new PostgreSQL major is added each November, one year after upstream release, and
  supported for five years. PostgreSQL 17 is the current target.
- Check the live instance rather than this table: `SELECT * FROM pg_available_extensions`.
  The `check-postgres-target` skill does this.

### pg-retrieve instance (checked 2026-11-19)

`check-postgres-target` run as `admin` on `dbod-amaldidb.cern.ch:6605`, the instance
created for pg-retrieve.

- PostgreSQL 17.10. TLS on (connection used SSL). Password authentication; the
  temporary admin password was replaced on creation day.
- Available: `vector` **0.8.2**, so the iterative HNSW scan
  (`hnsw.iterative_scan`) exists for filtered vector queries. `pg_trgm` 1.6,
  `unaccent` 1.1, `btree_gin` 1.3, `pg_cron` 1.6, `pg_stat_statements` 1.11.
  76 extensions in total. Nothing beyond `plpgsql` installed yet.
- `pg_textsearch` not available. The tsvector backend is the default.
- Settings as on the older instance: `shared_buffers` 1 GB, `max_connections` 100,
  `work_mem` 4 MB, `maintenance_work_mem` 64 MB.
- `admin` has `CREATEDB` and `CREATEROLE`, is not superuser, and cannot create in the
  `postgres` database. Whether `admin` may `CREATE EXTENSION vector` in its own database
  is still to be confirmed by the first migration.

### Live check of an older instance (2026-11-19)

`check-postgres-target` run as `admin` against an older DBOD instance (not the
pg-retrieve one). Re-run on the PostgreSQL 17 instance before relying on it.

- PostgreSQL 15.15. Password authentication; psql first tries GSSAPI, which fails
  harmlessly (`gssencmode=disable` silences it).
- Available: `vector` **0.8.0** (newer than the 0.7.0 in the DBOD extension list, so the
  shipped version differs by package), `pg_trgm` 1.6, `unaccent` 1.1, `btree_gin` 1.3,
  `pg_cron` 1.6, `pg_stat_statements` 1.10. 69 extensions in total.
- `pg_textsearch` not available. The tsvector backend is the default.
- Text search configs: the 29 built-in ones, including `english`, `simple`, `german`,
  `french`, `italian`, `russian`.
- Settings: `shared_buffers` 1 GB, `max_connections` 100, `work_mem` 4 MB,
  `maintenance_work_mem` 64 MB (too small for a fast HNSW build over 150k vectors;
  raise it per session in the index build).
- `admin` is not superuser but has `CREATEDB` and `CREATEROLE`. It cannot create in the
  `postgres` database (owned by `postgres`), so pg-retrieve needs its own database
  created by `admin`.
- `admin` cannot read `shared_preload_libraries` (needs `pg_read_all_settings`).

### pg_textsearch (checked 2026-11-16)

Source: [timescale/pg_textsearch](https://github.com/timescale/pg_textsearch), v1.5.1,
PostgreSQL License, supports PostgreSQL 17 and 18.

- Requires `shared_preload_libraries = 'pg_textsearch'` and a server restart, then
  `CREATE EXTENSION pg_textsearch` per database.
- Index: `CREATE INDEX ... USING bm25(content) WITH (text_config='english')`.
  Query: `ORDER BY content <@> 'terms' LIMIT n`. Scores are negative; lower is better.
- Shared-memory budget `pg_textsearch.memory_limit` defaults to 2 GB. That is the whole
  DBOD `shared_buffers` cap, so it would need tuning down.
- Metadata filters combined with BM25 ordering are post-filters, not one index scan.
  Works well only when the filter is selective.

## AI gateway (checked 2026-11-16)

Source: [getting started](https://ml.docs.cern.ch/aigw/gettingstarted/),
[models](https://ml.docs.cern.ch/aigw/models/).

- Base URL `https://aigw.cern.ch/`, bearer API key from
  <https://aigw.cern.ch/ui/?page=api-keys>. Pick a team, then "All Team Models".
- OpenAI-compatible routes: `/v1/models`, `/embeddings`, `/chat/completions`,
  `/audio/transcriptions`. No rerank route.
- Default team is `sandbox` with limited quotas and the default model set. A dedicated
  team is requested through the SNOW form "ll-gateway-new-team". No rate limits or
  prices are published. Usage dashboard: <https://aigw.cern.ch/ui/usage/>.
- Data handling is not stated on the page. Only on-premises models may be used for the
  corpus. Two Vertex AI preview models are external and retire on 2026-11-20.

Models as listed, on-premises unless noted:

| Model id | Type | Max message tokens | Notes |
|---|---|---|---|
| `qwen3.8-27b-fp16` | chat, vision | 131,072 | reasoning off by default; rerank candidate |
| `hf-qwen3-32b-awq` | chat | 16,384 | |
| `gpt-oss-20b` | chat, reasoning | 32,768 | effort low/medium/high |
| `gemma-4-12b-it-qat-w4a16-ct` | chat, multimodal | 131,072 | INT4; query-expansion candidate |
| `glm-5.3-flash-int4` | chat, vision | 1,048,576 | INT4; reasoning high by default |
| `embeddinggemma-300m` | embedding | 2,048 | dimension not documented |
| `e5-large-v2` | embedding | 512 | 1024 dimensions |
| `qwen3-asr-1.7b` | speech to text | 32,768 | not relevant |

Generally available models get six months of deprecation notice, preview models about
two weeks. Store the model name with every embedding set.

From the ML help desk meeting (recorded 2026-11-16): the serving options of the embedding
models (revision, precision, engine, truncation, pooling, normalisation) are not documented
and may differ from the models' public defaults. The gateway is heavily used, so prefer
small models for expansion and reranking. The Vertex AI models are not to be used. Whether
task prefixes are added server-side is unknown; assume the client adds them.

## ml.cern.ch: Kubernetes with GPUs (checked 2026-11-16)

Source: [getting started](https://ml.docs.cern.ch/getting-started/),
[serving](https://ml.docs.cern.ch/ml/serving/),
[custom runtimes](https://ml.docs.cern.ch/ml/serving/custom-runtime/).

This is a Kubeflow platform, separate from OpenShift PaaS. It is where bulk embedding
runs (a T4 16 GB in the personal namespace), where Qwen3-Embedding and the qmd
query-expansion model can be served for evaluation, and the fallback for hosting a
cross-encoder reranker if LLM reranking proves too slow.

- Personal namespace equals the CERN username. kubectl with the OIDC plugin, kubeconfig
  from `https://ml-public.web.cern.ch/config`, context `ml-prod`. CERN SSO tokens last 10 h.
- GPU limits per personal namespace: 1 Nvidia and 2 AMD. Flavors: H100 94 GB and 47 GB
  MIG slices, A100 40 GB, T4 16 GB, V100S 32 GB, RTX6000 96 GB, AMD MI50 and MI100.
  Requested with `nodeSelector: nvidia.com/gpu.product` and `resources.limits`.
- Interactive sessions are Kubeflow notebooks, culled after 8 h idle, not reachable from
  outside CERN.
- Serving is KServe. The HuggingFace runtime has a vLLM backend and exposes an
  OpenAI-compatible `/openai/v1/chat/completions` route under
  `https://ml.cern.ch/serving/<user>/<name>/`. Endpoints need a bearer token created with
  `kubectl create token default-viewer --duration=168h --audience=istio-ingressgateway.istio-system.svc.cluster.local`,
  seven days by default. Embedding or rerank models are not documented as supported;
  a custom runtime from an image in the CERN GitLab registry would be needed.
- Reachability from OpenShift PaaS is not documented. Confirm before planning on it.

## Support

- DBOD: SNOW ticket to the DBOD service.
- ML platform and gateway: Mattermost channel, SNOW, the "LLM Consultancy" SNOW form,
  office hours Wednesdays 15:00 CET.
