# pg-retrieve specification

Status: in progress. Sections written so far: collections and access control (1), the
lexical normaliser (2), indexes (3), MCP server (4), document elements (5, a
placeholder). Still to write: data model for documents and chunks, ingest, search
pipeline, HTTP API, configuration. Each component gets its section
here before implementation.

## 1. Collections and access control

### 1.1 Goals

- Several groups of users share one service. The first case is several experiments,
  each with its own documents, but the mechanism is generic.
- Documents live in collections. A user who has no right on a collection must not learn
  that it exists.
- Rights are read-only or read-write per collection. Bulk operations such as
  re-embedding are reserved for administrators.
- Authentication is pluggable: tokens issued by pg-retrieve always work, and an identity
  provider (CERN SSO, Authentik, any OIDC issuer) can be plugged in.
- Group membership is pluggable too, so CERN e-groups, Authentik groups or a static
  list can drive the same grants.
- A standalone deployment needs nothing but pg-retrieve itself: a minimal built-in
  interface lets an administrator create users and lets each user get their own tokens.

### 1.2 Concepts

- **Principal.** A user or a service account. Identified by a stable `subject` string
  (OIDC `sub`, or a name chosen at creation for local principals). Has a display name
  and an optional email.
- **Group.** A named set of principals, resolved by the group provider. Names are
  namespaced by provider to avoid collisions, for example `egroup:delphi-members`,
  `authentik:lep-archive`, `local:admins`.
- **Collection.** A named set of documents. Names are hierarchical with `/`, for
  example `delphi/notes`, `delphi/theses`, `aleph/papers`. A collection carries a
  description (shown to agents) and one or more indexes (section 3), one of them the
  default. The chunker, embedding model and normaliser belong to the index, not to the
  collection.
- **Role.** One of `read`, `write`, `admin`, ordered: `admin` implies `write` implies
  `read`.
  - `read`: search, get_chunk, get_document, list the collection.
  - `write`: ingest, create, update and delete documents, through the HTTP API, the
    CLI or MCP alike.
  - `admin`: grant and revoke rights, create, fill, switch and delete indexes, delete
    the collection.
- **Service administrator.** A principal or group named in config (`auth.admins`). Can
  create collections, grant any role, run bulk re-embedding across collections, and
  manage principals. This is the only right that is not per collection.
- **Grant.** `(subject, collection pattern, role)`. The subject is a principal or a
  group. The pattern is a collection name or a prefix with a trailing `/*`
  (`delphi/*`). A principal's effective role on a collection is the highest role over
  all grants that match, for the principal itself and for every group it belongs to.
- **No anonymous access.** Every request carries a principal. CERN SSO offers social
  login, so anyone who should read a collection can obtain an identity and a grant.

### 1.3 Authentication providers

Chosen by config (`auth.providers`, several may be active; the first that accepts the
request wins).

- **`token`** (always available). Bearer tokens issued by pg-retrieve. A token is a
  random 256-bit secret shown once at creation; the database stores only a SHA-256 hash,
  the owning principal, a label, creation and expiry time, last use, and an optional
  restriction to a role ceiling and collection pattern (so a user can mint a read-only
  token for one collection to hand to an agent). Tokens act as the owning principal,
  never with more rights than the principal has at request time.
- **`oidc`**. Validates a JWT from a configured issuer (discovery URL, audience,
  JWKS cached). CERN SSO and Authentik are both OIDC, so one provider covers them.
  The principal is created on first sight (`subject` = issuer-qualified `sub`). Claims
  used for name and email are configurable. Group claims are read by the group
  provider below.
- **`header`** (optional, for deployments behind a trusted proxy that authenticates,
  such as an OpenShift OAuth proxy). Trusts a configured header for the subject. Off by
  default and refused unless the proxy's network is the only route.

Plugins implement one small interface: `authenticate(request) -> Principal | None`.
Providers are loaded through Python entry points, so a CERN-specific provider can live
in a separate package.

### 1.4 Group providers

Chosen by config (`auth.groups`, several may be active; memberships are unioned).

- **`static`**: a mapping in config or a YAML file: group name to list of subjects.
  Enough for standalone use.
- **`claim`**: groups from a claim of the OIDC token (configurable claim path and name
  prefix). This covers CERN SSO, which puts e-group roles into the token, and Authentik.
- **`egroups`**, **`authentik`**: lookups against the respective APIs for memberships
  that are not in the token. Separate packages or later work; the interface is
  `groups(principal) -> set[str]`.

Memberships are cached per principal with a TTL (`auth.group_cache_ttl`, default 300 s).
Revoking a grant takes effect immediately; losing a group membership takes effect after
the TTL. A principal can be in groups from several providers at once.

### 1.5 Enforcement

- Every request resolves a principal (or anonymous) and its effective role per
  collection once, then the core API takes the readable collection set as an argument.
  The search SQL filters on that set before ranking, in the same place as the metadata
  filters, so hidden collections never influence scores, counts or facets.
- Unauthorised access to a collection returns 404, not 403, for read operations, so
  existence is not revealed. Write and admin operations on a collection the principal
  can read return 403.
- Listing collections returns only those with at least `read`.
- `get_document` and `get_chunk` check the collection of the object, not a collection
  named by the client.
- The MCP server applies the same checks. Over HTTP it takes the bearer token from the
  request. Over stdio it reads a token from the environment (`PG_RETRIEVE_TOKEN`) and
  acts as that principal.
- The ingest CLI is an API client like any other. Nothing but the service itself holds
  database credentials. See 1.6 for how bulk loads stay fast under this rule.

### 1.6 Bulk loads through the API

Bulk ingest never writes to the database directly. The reasons: one code path for
access checks and audit, no database credentials outside the service, and the server
can validate every batch against the collection's embedding fingerprint and chunker
version before anything lands. The cost is small: the slow part of a bulk load is
embedding, not insertion.

- **Embedding worker.** The ingest CLI on a GPU host (a T4 on ml.cern.ch) reads the
  Markdown, chunks it with the shared chunker library, embeds with the `local` backend,
  and posts batches of chunks with their vectors to
  `POST /collections/{name}/indexes/{index}/chunks`. Each batch states the document id,
  content hash, chunker version and embedding fingerprint. The server rejects a batch
  whose fingerprint or chunker version differs from the index's, inserts with COPY, and
  updates the lexical index in the same transaction. A batch of 256 chunks with 1024-dimensional vectors is about 1 MB;
  the full DELPHI corpus is about 600 requests.
- **Without a GPU.** The same endpoint accepts chunks without vectors. The server then
  embeds them with its configured `openai` backend, in batches, rate-limited, as a
  background job per document, and the document becomes searchable when its vectors
  are complete. Lexical search works immediately. This is the path for small updates
  from a laptop or a CI job.
- **Idempotency.** A document whose content hash and fingerprint already match is
  skipped. Changed documents replace their chunks atomically. The manifest from the
  upstream repository (`md/manifest.jsonl`) is the natural driver: the CLI compares
  its hashes with `GET /collections/{name}/documents?fields=hash` and posts only the
  difference.
- **Re-embedding.** A model change is a new index (section 3), created by an admin and
  filled by the worker while the old one stays the default. When the new index is
  complete, the admin switches the default and deletes the old index. No staging table
  is needed; the index is the staging area.
- **Credentials.** The worker uses a personal token with role ceiling `write` (or
  `admin` for re-embedding) and pattern `delphi/*`, created on the `/me` page with an
  expiry of a few days. The token never leaves the user's namespace on ml.cern.ch.

### 1.7 Standalone interface

Kept minimal. All of it is also available to OIDC deployments.

- CLI (`pg-retrieve auth ...`): create a principal, create a token for a principal, add
  or remove a static group member, grant and revoke, list grants. The first service
  administrator is bootstrapped from config or by this CLI with database access.
- HTTP, for every authenticated principal: `GET /me` (identity, groups, effective roles
  per visible collection), `GET /me/tokens`, `POST /me/tokens` (label, expiry, optional
  role ceiling and collection pattern; returns the secret once), `DELETE /me/tokens/{id}`.
- HTTP, for collection admins: `GET/POST/DELETE /collections/{name}/grants`.
- One static HTML page (`/ui/me`) over those endpoints, so a user can log in through
  OIDC when configured, see their collections, and create or revoke tokens. No framework.

### 1.8 Audit

An append-only table records admin and write actions: time, principal, action,
collection, object id, outcome. Reads are not audited. Retention is configurable.

### 1.9 Data model (auth part)

```
principals(id, subject UNIQUE, display_name, email, created_at, disabled)
tokens(id, principal_id, hash UNIQUE, label, created_at, expires_at, last_used_at,
       role_ceiling, collection_pattern, revoked_at)
group_members(group_name, principal_id)            -- static provider only
collections(id, name UNIQUE, description, default_index_id, created_at)
indexes(id, collection_id, name, chunker, chunker_version, embed_model,
        embed_fingerprint, embed_dim, normaliser, normaliser_version, status,
        created_at, UNIQUE (collection_id, name))
grants(id, subject_kind {principal,group}, subject, collection_pattern, role,
       granted_by, created_at)
audit(id, at, principal_id, action, collection, object_id, outcome, detail)
```

Documents reference `collections.id`; chunks and vectors reference `indexes.id`. A
document belongs to exactly one collection and appears in every index of it.

### 1.10 Configuration

```
auth:
  providers: [token, oidc]          # order matters
  oidc:
    issuer: https://auth.cern.ch/auth/realms/cern
    audience: pg-retrieve
    name_claim: name
    email_claim: email
  groups: [claim, static]
  claim:
    path: cern_roles                # or "groups" for Authentik
    prefix: "egroup:"
  static_file: /etc/pg-retrieve/groups.yml
  group_cache_ttl: 300
  admins: ["egroup:delphi-kb-admins", "local:admin"]
```

### 1.11 Decisions taken

Decided here, change if wrong:

- One `oidc` provider for both CERN SSO and Authentik, rather than one plugin each.
  E-groups arrive as token claims; an API-based e-groups provider is later work.
- Grants on name prefixes (`delphi/*`) instead of a separate tenant object. An
  experiment is a prefix plus a group.
- 404 for hidden collections.
- Service administrators come from config, not from the database, so a lost database
  cannot lock everyone out.
- No anonymous access (decided 2026-11-16): social login through CERN SSO covers
  outside readers.
- Bulk loads go through the API with precomputed vectors (decided 2026-11-16), see 1.6.

## 2. Lexical normaliser

### 2.1 Purpose

Lexical search must find a physics symbol whatever its spelling: `αs`, `alpha_s`,
`$\alpha_{S}$` and `α_s` are one term, `e+e−`, `$e^+e^-$` and `e- e+` are one term.
PostgreSQL's tokenisers see single letters and operators instead. The sibling repository
solved this for qmd with `k8s/qmd/searchnorm.mjs`: one function applied to every document
before indexing and to every lexical query, so all spellings meet on one canonical token.
pg-retrieve ports that design. The embedding leg never sees the normaliser; results always
show the raw text.

### 2.2 Interface

A normaliser is a plugin, loaded through a Python entry point, selected per index
(section 3):

```python
class Normaliser(Protocol):
    name: str          # "none", "physics", or a third-party name
    version: str       # changes whenever the output can change (code or lexicon)
    def normalize(self, text: str) -> str: ...
```

- `normalize` keeps newlines outside math spans, so line-based snippet selection still
  works, and returns lowercase ASCII tokens for symbols with plain words unchanged.
- Both lexical backends call it: on the document text before `to_tsvector` or the BM25
  index, and on the lexical query before `to_tsquery` or the BM25 query. Nothing else
  calls it.
- The index stores `normaliser` and `normaliser_version`. A mismatch at ingest is an
  error. A new normaliser version means a new index; it can share nothing with the old
  one at the interface level, though an implementation may copy vectors when only the
  normaliser differs.

Built-ins:

- `none`: identity. The default for a general corpus.
- `physics`: the port of `searchnorm.mjs`, driven by a lexicon file (2.3).

### 2.3 Lexicon file

The `physics` normaliser separates code from data. The code handles what needs a parser:
math span detection (`$...$`, `$$...$$`, `\(...\)`, `\[...\]`, one span per line),
sub- and superscript folding (`B^0_s` and `B_s^0` to `b0s`, charge before subscript, a
numeric power after a subscript dropped), stripping of font wrappers and layout commands,
`\bar`, `\sqrt`, units, and stemmer protection. Everything that is a table lives in a
JSON file, so another domain can change the vocabulary without code:

```
{
  "version": "1",
  "greek":        {"α": "alpha", ...},           // Unicode letter to name
  "latex_greek":  ["alpha", "beta", ...],        // LaTeX names kept as tokens
  "latex_names":  ["sin", "cos", "sqrt", ...],   // other LaTeX commands kept
  "subscripts":   ["had", "T", "Z", ...],        // subscripts folded into the token
  "pairs":        {"e": "e", "μ": "mu", ...},    // X+X− pairs: epem, mupmum
  "particles":    "ZWBDKH",                      // letters that take a charge
  "charges":      {"+": "p", "-": "m", "±": "pm", "0": "0", "*": "star"},
  "units":        ["TeV", "GeV", "MeV", "pb", "fb", ...],
  "aliases":      {                              // new: long tail, canonical token first
    "alphasx":  ["strong coupling constant"],
    "sqrtsx":   ["centre-of-mass energy", "center-of-mass energy", "/s**\\(1/2\\)/"],
    "wpm":      ["W boson"]
  }
}
```

- The first seven tables are exactly what the sibling repository exports with
  `delphi-kb-searchnorm export`, so the two stay in step by copying one file.
- `aliases` is new. Each value is a list of literal phrases, or regexes written between
  slashes, replaced by the canonical token in both documents and queries. This is where an
  LLM-built lexicon of the long tail goes, and where a different domain adds its own
  vocabulary. Longer phrases are applied first. A phrase is matched case-insensitively on
  word boundaries.
- The normaliser's `version` is `physics-<code version>-<lexicon version>`.

### 2.4 Stemmer interaction

The `x` suffix on tokens with a word-like ending (`alphasx`, `sqrtsx`) was tuned for the
Porter stemmer of SQLite FTS5, which would otherwise cut `alphas` to `alpha`. PostgreSQL's
`english` configuration uses Snowball, which stems differently. Decision: the symbol tokens
must not be stemmed at all. On the tsvector backend the document text is split into two
weighted parts, prose through the `english` configuration and the symbol tokens through
`simple`, concatenated into one tsvector; the query does the same. On pg_textsearch the
index takes one configuration, so the same split is done with two columns and the scores
are added. If that proves too complex, the fallback is to keep the suffix protection and
verify the cases file against Snowball. Custom PostgreSQL dictionaries are not an option:
they need files in the server's share directory, which a managed service does not expose.

### 2.5 Tests

The cases file format is domain-independent and ships with the normaliser:

```
{
  "same": [["αs", "α_s", "alpha_s", "$\\alpha_s$"], ...],     // one token each
  "has":  [["the Z° peak at √s = 91.2 GeV", ["z0", "sqrtsx", "91.2", "gev"]], ...]
}
```

`pg-retrieve normaliser check <lexicon> <cases>` runs them, so a user validating their
own lexicon needs no test code. The DELPHI cases file from the sibling repository is the
fixture for the `physics` normaliser, and must pass on both backends against the actual
database tokeniser, not only on the Python output.

### 2.6 Decisions taken

- Plugin interface with a lexicon-driven built-in, not a rule language (decided
  2026-11-16). The LaTeX handling needs a parser; the vocabulary is data.
- Symbol tokens bypass stemming on both backends rather than relying on suffix
  protection (2.4), to be confirmed by the cases file on a real database.

## 3. Indexes

### 3.1 Purpose

Evaluation needs several chunkers, embedding models and normalisers side by side over the
same documents, selectable per request, without redeploying. qmd did this with two whole
indexes (`gemma`, `qwen`) over the same collections. pg-retrieve makes the index a named
object under a collection.

### 3.2 Definition

An index belongs to one collection and bundles everything that decides what is stored:

| Field | Meaning |
|---|---|
| `name` | unique within the collection, for example `default`, `qwen06`, `sem-chunks`, `nonorm` |
| `chunker`, `chunker_version` | the chunker plugin and its version |
| `embed_model`, `embed_fingerprint`, `embed_dim` | the embedding model, its fingerprint (section 1.6), and the vector dimension |
| `normaliser`, `normaliser_version` | the lexical normaliser (section 2) |
| `status` | `building`, `ready`, `retired` |

Documents are shared: one copy of the Markdown per collection, with its metadata,
outline and line numbering. Chunks, vectors, the HNSW index and the lexical index belong
to an index. A document appears in every index of its collection once that index has
been filled for it.

Query expansion and reranking are not part of an index. They change nothing that is
stored and remain per-request switches.

### 3.3 Default and selection

- Every collection has exactly one default index. A new collection gets a `default`
  index from the service configuration (`index.defaults`: chunker, embedding model,
  normaliser).
- `search`, `get_chunk` and `outline` take an optional `index` name and fall back to the
  default. A request naming an index that is not `ready` is an error. The HTTP and MCP
  interfaces expose the parameter; the MCP tool description says to omit it unless an
  evaluation asks for a specific index.
- A search over several collections uses each collection's default unless the request
  names an index, in which case every named collection must have an index of that name.
- Every result and `/info` reports the index that answered, with its fingerprint, so an
  evaluation run records exactly what it measured.

### 3.4 Identifiers

- Document ids are the ingest ids (section on corpus) and are the same in every index.
- Page numbers come from the document and are the same in every index.
- A chunk id is derived from the document content hash, the chunker version and the
  chunk sequence number. It is stable across re-ingests of the same index and differs
  between indexes with different chunkers. Comparisons between indexes are therefore
  made at document or page level, not by chunk id.

### 3.5 Lifecycle

All of it needs `admin` on the collection.

1. `POST /collections/{name}/indexes` creates the index in status `building`, with its
   chunker, embedding model and normaliser.
2. The bulk worker (section 1.6) fills it with
   `POST /collections/{name}/indexes/{index}/chunks`. Documents become searchable in that
   index as they complete. Lexical search in the index works before the vectors arrive.
3. `PUT /collections/{name}/indexes/{index}/status` sets `ready` when the admin is
   satisfied (the server reports how many documents are complete).
4. `PUT /collections/{name}/default-index` switches the default. Readers notice nothing
   except different results.
5. `DELETE /collections/{name}/indexes/{index}` removes chunks, vectors and the lexical
   data. The default index cannot be deleted.

Readers of a collection can list its indexes with their status, so an evaluation can
discover what is available. Re-embedding after a model change is this lifecycle: new
index, fill, switch, delete the old one.

### 3.6 Storage

Every index duplicates chunks and vectors. For the DELPHI corpus one 1024-dimensional
index is about 600 MB of vectors plus an HNSW index of similar size; a 768-dimensional
one about three quarters of that. DBOD caps `shared_buffers` at 2 GB, so three or four
full-size indexes fit on disk but are not all hot at once. Mitigations, in order:

- store vectors as `halfvec` (pgvector 0.7 supports it, including HNSW), which halves
  both the data and the index;
- run chunking and normaliser experiments on a sample collection rather than the full
  corpus;
- retire experimental indexes when the evaluation is done.

A normaliser-only variant still needs its own chunks (text only, no vectors), which is
cheap; sharing vectors between such indexes is a possible optimisation, not part of the
interface.

### 3.7 Decisions taken

- The index, not the collection, carries chunker, embedding model and normaliser
  (decided 2026-11-16). Sections 1 and 2 were adjusted accordingly.
- Re-embedding is a new index plus a default switch, replacing the staging table of an
  earlier draft of section 1.6.
- Query expansion and reranking stay per-request switches outside the index.

## 4. MCP server

### 4.1 Purpose and scope

The MCP server lets agents find, read and, where they have the right, write documents.
Finding is `search`. Reading is a first-class use: an agent gets an outline of a document, then reads it section by
section, and cites what it read. This is the workflow the DELPHI agents already use with
qmd (wiki page with a table of contents and line ranges, then `get` with a line range).
Documents average 68 k characters and theses run to hundreds of chunks, so every read is
bounded.

The server is a thin layer over the library core, with the same access checks as the
HTTP API (section 1.5). What an agent may do on a collection follows only from its
principal's roles, through users and groups, and from its token's ceiling: `read` allows
the reading tools, `write` adds the document tools of 4.5. There are no special
collection kinds. Bulk ingest, index lifecycle and grants stay on the HTTP API and the
CLI, never on MCP.

### 4.2 Transports and authentication

- **Streamable HTTP**, mounted in the service at `/mcp`. The bearer token from the
  request identifies the principal. `Origin` and `Host` are validated against config, as
  qmd does against DNS rebinding.
- **stdio**, started as `pg-retrieve mcp`, for local agent harnesses. It talks to a
  remote service over HTTP with the token from `PG_RETRIEVE_TOKEN` and the URL from
  `PG_RETRIEVE_URL`. It never opens the database itself.

A token minted with a role ceiling and collection pattern (section 1.3) is the normal
way to give an agent access: for example read-only on `delphi/*`, expiring. An agent that
should write gets a token whose ceiling is `write`, ideally on a narrower pattern than
its read access. This is also the main defence against prompt injection from documents
the agent reads: an agent can only damage what its token can write.

### 4.3 Server instructions

The MCP `instructions` field carries the reading workflow, so every client gets it
without a project file:

- Start with `search` or `list_documents`; treat hits and snippets as leads, not facts.
- Call `outline` before reading a long document; read sections or line ranges with
  `get_document`, not whole documents.
- Write separate `lex` (keywords, exact terms, symbols) and `vec` (a natural-language
  sentence) queries; use `query` only for a plain question.
- Cite as `[[<doc_id>]]` with section, lines or pages, exactly as returned.
- When writing, read the document's `version` first and pass it back; state the sources
  a written document relies on in its `sources` field.

The text is generic. Per-deployment additions (for DELPHI: the collection descriptions,
id formats, the preference for journal versions) come from config, so the DELPHI
specifics never sit in code.

### 4.4 Tools

All tools take only collections the principal can read; others behave as nonexistent.
Every response that contains document text carries the citation fields of 4.6.

**`list_collections`** — the readable collections with description, document count,
default index, the other indexes and their status, and the filterable metadata fields
with example values (qmd's `metadata` discovery). No parameters.

**`search`**

| Parameter | Type | Meaning |
|---|---|---|
| `query` | string | a plain question; expanded by the configured backend unless `lex` or `vec` is given |
| `lex` | list of strings | lexical queries, no expansion |
| `vec` | list of strings | vector queries, no expansion |
| `hyde` | string | optional hypothetical passage for the vector leg |
| `collections` | list | names or prefixes (`delphi/*`); default all readable |
| `filters` | object | metadata filters: `year`, `kind`, `language`, `topics`, `detectors`, ... (field list from `list_collections`) |
| `index` | string | named index; omit for the default (section 3.3) |
| `mode` | enum | `lex`, `vec`, `hybrid` (default) |
| `rerank` | bool | default from config |
| `limit` | int | default 10, max 50 |

At least one of `query`, `lex`, `vec` is required. Each hit returns the chunk id, document
id, title, collection, heading path, line range, page range, a snippet, and the score
components (`bm25`, `vector`, `rrf`, `rerank`). The response names the index used and
whether expansion and reranking ran.

**`list_documents`** — browse without a query. Parameters `collections`, `filters`,
`sort` (`year`, `title`), `limit`, `cursor`. Returns id, title, year, kind, language,
one-line summary when a card exists.

**`outline`** — the entry point for reading. Parameter `doc_id`, optional `index`.
Returns:

- the frontmatter metadata and the card summary when present;
- the heading tree, each node with its number when the document has one, title, level,
  line range, page range, character count, and the chunk ids it spans in the chosen index;
- total lines, characters and pages.

The heading tree is computed at ingest by the same heading pass the chunker uses, so
section boundaries in the outline and chunk provenance agree.

**`get_document`** — bounded reads of the Markdown. Parameter `doc_id` plus at most one
range selector:

| Selector | Example | Meaning |
|---|---|---|
| `lines` | `{"from": 220, "count": 80}` | a line range |
| `section` | `"4.2"` or `["Results", "Systematic errors"]` | a section by number or heading path, with its subsections |
| `pages` | `{"from": 12, "to": 13}` | the text on those PDF pages (once page markers exist; error otherwise) |

Without a selector, the whole document is returned if it is below the cap. Every read is
capped at `mcp.max_chars` (default 12,000). A truncated read ends at a line boundary and
returns `next` (the selector for the following range), so an agent can continue without
arithmetic. Lines are returned with their line numbers, so a citation can point inside
the range.

**`get_chunk`** — one chunk by id, with its text, provenance, and the ids of the previous
and next chunk in the same index, so an agent can read around a search hit.

### 4.5 Document tools

Listed in `tools/list` only when the principal, within its token's ceiling, has `write` on
at least one collection; otherwise the server is read-only for that session. Each call
checks `write` on the target collection.

**`put_document`** — create or replace one document.

| Parameter | Type | Meaning |
|---|---|---|
| `collection` | string | target collection |
| `doc_id` | string | optional on create (the server assigns one); required on replace |
| `markdown` | string | the full document, optionally with YAML frontmatter |
| `metadata` | object | optional; merged over the frontmatter |
| `sources` | list | optional ids of documents this one relies on; the server checks they exist and are readable by the caller |
| `version` | string | required on replace: the version the caller last read; a mismatch fails |

**`patch_section`** — replace one section, by number or heading path, with new Markdown
including its heading. Same `version` check. Fits page-by-page work on long documents.

**`delete_document`** — remove one document, with the `version` check.

Behaviour:

- Every write produces a new `version` (a content hash), returned to the caller. A stale
  `version` fails with the current one, so concurrent agents cannot silently overwrite
  each other.
- The document is lexically searchable when the call returns, in every index of the
  collection. Vectors are computed in the background by the server's `openai` embedding
  backend for each index whose model it can reach (section 1.6, the path without a GPU).
  An index whose model is only available to the bulk worker marks the document as
  pending until the worker fills it. `outline` reports per index whether vectors are
  complete.
- Size and rate limits apply per token (`mcp.max_write_chars`, `mcp.writes_per_minute`),
  to keep gateway load bounded.
- Every write is in the audit log (section 1.8).

### 4.6 Resources

Each readable document is also an MCP resource, for clients that attach documents rather
than call tools:

- URI `pg-retrieve://<collection>/<doc_id>`, for example
  `pg-retrieve://delphi/notes/delphi-1995-012-phys-487`.
- Resource templates with the selectors of `get_document`:
  `...?lines=220-299`, `...?section=4.2`, `...?pages=12-13`, under the same cap.
- `resources/list` is paginated and limited to readable collections. Subscriptions are
  not supported; the corpus changes by ingest, not live.

### 4.7 Citation and provenance fields

Every piece of returned text carries `collection`, `doc_id`, `title`, `version`, `lines`
(first and last line number), `pages` (first and last page, or `null` when unknown, never
guessed), `heading_path`, and, for chunks, `chunk_id` and `index`. `outline` also returns
the provenance of the document: how it arrived (ingest or API write), created and updated
by which principal and token label, when, and its declared `sources`. Search hits carry
the same provenance in short form, so a reader can tell a written note from an ingested
paper without the service knowing which is which. Line numbers count lines of the
stored Markdown file including its frontmatter, as qmd's `get` does, so line citations
already in the DELPHI wiki remain valid.

### 4.8 Limits and errors

- Per-call caps: `limit` on search and listing, `max_chars` on reads, `max_write_chars`
  on writes. Rate limiting per principal is configurable, off by default for reads and on
  for writes.
- A document or collection the principal cannot read returns the same error as one that
  does not exist.
- A named index that is not `ready`, a `pages` selector without page data, or a section
  that does not exist return an error that says what is available (the ready indexes,
  the outline's section numbers).
- Expansion or rerank failures do not fail the call; the response says the stage was
  skipped, as in section 5 of the retrieval design.

### 4.9 Decisions taken

- MCP reads and writes single documents; roles from users and groups, capped by the
  token, decide which (decided 2026-11-17, replacing an earlier read-only draft). There
  are no collection kinds such as source or derived: the service stays generic, and a
  deployment expresses such distinctions through collections and grants.
- Bulk ingest, index lifecycle and grants are not exposed over MCP.
- Writes use optimistic concurrency with a version hash.
- Reading is bounded by a character cap with an explicit `next` selector, rather than
  trusting agents to request sensible ranges.
- Line numbers include the frontmatter, for compatibility with qmd and the DELPHI wiki.
- The stdio server is a client of the HTTP service, so there is one place that holds
  database credentials.

## 5. Document elements (placeholder)

### 5.1 Purpose

Figures, tables and equations need their own place in the data model now, so that
captions, generated descriptions and images can be added later without a schema change.
Making them useful for search is future work. This section only fixes where they live
and what the chunker does with them by default.

Measured on the DELPHI corpus (2026-11-18):

| Element | Count | Share of text | Notes |
|---|---|---|---|
| figures | 52,737 image references in 3,191 documents | none (links only) | captions are text next to the image; Docling JSON links caption and page |
| tables | 47,292 | 18.6% | median 210 characters, 1% above 10 k; 60% mostly numeric; about 10% have only empty cells; only 30% have a "Table N" caption nearby; scanned tables are often garbled |
| display equations | 67,824 | 3.3% | one `$$...$$` line each, often with an equation number |
| inline math | 704,000 spans | in prose | part of the sentences around them |

### 5.2 Element records

At ingest each document gets zero or more element records:

```
elements(id, doc_id, kind {figure, table, equation}, number, caption,
         lines, pages, asset_ref, quality, created_at)
element_descriptions(element_id, text, model, created_at, created_by)
```

- `number` is the printed label (`Figure 3`, `Table 2`, `(12)`) when one is found.
- `lines` and `pages` locate the element in the stored Markdown and the PDF.
- `asset_ref` points to an image in the configured blob store (figures only); images are
  served through pg-retrieve so collection rights apply.
- `quality` is a flag from simple checks (for tables: share of empty cells, garbled
  numbers) so that broken elements can be left out of the index.
- Descriptions are optional, may come from several models, and are always marked as
  generated. They arrive by ingest or by a `write` on the document, never from the search
  service itself.

The Markdown keeps the elements in place, so reading a document is unchanged.

### 5.3 What the chunker does with them

How each kind is indexed is a chunker option, so it is part of the index (section 3) and
an evaluation can compare variants. Defaults:

- **Figures:** the image link is removed from the chunk text; the caption stays in the
  surrounding text chunk.
- **Tables:** a table goes into its own chunk with its caption and header row, and is
  split by rows with the header repeated when it exceeds the chunk size. Tables flagged as
  broken are not indexed. Small tables stay in the text chunk.
- **Equations:** stay inline in the text chunk. Their symbols reach the lexical leg through
  the normaliser (section 2); the vector leg sees them as part of the prose.

Element chunks carry `kind` so search can include or exclude them; descriptions, once they
exist, become additional text of the element chunk.

### 5.4 Later

- Generated descriptions for figures, tables and equations, piloted on a sample first.
- MCP: `outline` lists elements; a `get_element` tool returns caption, descriptions, page
  and, for figures, the image as MCP image content.
- Structured table data (rows and columns) for result tables. The per-document cards
  upstream already extract measurements; whether pg-retrieve exposes them is open.
