# pg-retrieve specification

Status: in progress. Sections written so far: collections and access control (1), the
lexical normaliser (2), indexes (3), MCP server (4), document elements (5, a
placeholder), lexical backends (6), metadata, aliases and links (7), with the scientific profile
in appendix A. Still to write: data model for documents and chunks, ingest, search
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
        embed_fingerprint, embed_dim, normaliser, normaliser_version,
        lexicon_id NULL, lexical_backend, status, created_at,
        UNIQUE (collection_id, name))
lexicons(id, collection_id NULL, name, version, hash, body jsonb, created_by,
         created_at, UNIQUE (collection_id, name, version))   -- NULL = service scope
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

class NormaliserFactory(Protocol):
    uses_lexicon: bool
    def __call__(self, lexicon: dict | None) -> Normaliser: ...
```

The factory is the entry point. A normaliser that uses a lexicon receives the parsed
lexicon version the index names (2.6); it never reads files.

- `normalize` keeps newlines outside math spans, so line-based snippet selection still
  works, and returns lowercase ASCII tokens for symbols with plain words unchanged.
- Both lexical backends call it: on the document text before `to_tsvector` or the BM25
  index, and on the lexical query before `to_tsquery` or the BM25 query. Nothing else
  calls it.
- The index stores `normaliser`, `normaliser_version` and, for normalisers that use
  one, the exact lexicon version (2.6). A mismatch at ingest is an error. A new normaliser version means a new index; it can share nothing with the old
  one at the interface level, though an implementation may copy vectors when only the
  normaliser differs.

Built-ins:

- `none`: identity. The default for a general corpus.
- `physics`: the port of `searchnorm.mjs`, driven by a lexicon (2.3, 2.6).

### 2.3 Lexicon format

The `physics` normaliser separates code from data. The code handles what needs a parser:
math span detection (`$...$`, `$$...$$`, `\(...\)`, `\[...\]`, one span per line),
sub- and superscript folding (`B^0_s` and `B_s^0` to `b0s`, charge before subscript, a
numeric power after a subscript dropped), stripping of font wrappers and layout commands,
`\bar`, `\sqrt`, units, and stemmer protection. Everything that is a table lives in a
JSON lexicon, stored in the database and managed through the API (2.6), so another domain
can change the vocabulary without code or a redeploy:

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
  slashes, replaced by the canonical token in both documents and queries. Regexes are
  accepted only in service-wide lexicons (2.6), because a pathological pattern can stall
  the service; collection lexicons use literal phrases. This is where an
  LLM-built lexicon of the long tail goes, and where a different domain adds its own
  vocabulary. Longer phrases are applied first. A phrase is matched case-insensitively on
  word boundaries.
- The `version` field inside the file is informative. The normaliser's `version` is
  `physics-<code version>-<lexicon hash>`, where the hash is the first 12 hex digits of
  the SHA-256 of the canonical JSON (sorted keys, no whitespace), so equal content gives
  an equal version wherever it was uploaded.

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

`pg-retrieve normaliser check --lexicon <file or name@version> --cases <file>` runs them
through the service (`POST /lexicons/check`), so the result reflects the server's
normaliser code and the database tokeniser, and a user validating their own lexicon needs
no test code. The DELPHI cases file from the sibling repository is the
fixture for the `physics` normaliser, and must pass on both backends against the actual
database tokeniser, not only on the Python output.

### 2.6 Lexicon storage and API

Lexicons are data in the database, not files in the image or a mount, so the service
keeps no local state and several groups sharing one service can each have their own
vocabulary.

- **Objects.** A lexicon has a name and numbered versions. A version is immutable: an
  upload creates the next version with its content hash, and nothing is edited in place.
- **Scope.** A lexicon belongs either to one collection or to the service.
  - Collection lexicons are uploaded by an `admin` of that collection and can be used
    only by indexes of that collection.
  - Service lexicons are uploaded by service administrators and can be used by any index.
  - The package ships the `physics` lexicon (the DELPHI vocabulary exported from the
    sibling repository). The service registers it as a service lexicon at start-up when
    its hash is not yet present, so a fresh installation works without an upload.
- **Indexes pin a version.** Creating an index names a normaliser and, if it uses one, a
  lexicon reference `name@version` or `name@latest`. The reference is resolved once, at
  creation, to an exact version; `latest` never moves an existing index. A lexicon change
  is therefore a new index (section 3.5), which rebuilds only lexical data.
- **Validation.** On upload the server checks the JSON against the schema of 2.3, rejects
  unknown sections, rejects regexes in collection lexicons, and enforces a size limit
  (`lexicon.max_bytes`, default 1 MB). The normaliser factory must accept it.
- **Deletion.** A version referenced by any index cannot be deleted. Others can be
  deleted by whoever could upload them.
- **Reading.** Readers of a collection can list and fetch its lexicons and the service
  lexicons, so an evaluation can record exactly which vocabulary an index used.

Endpoints:

| Method and path | Right | Purpose |
|---|---|---|
| `GET /lexicons` | authenticated | service lexicons |
| `POST /lexicons/{name}` | service admin | upload the next version of a service lexicon |
| `GET /collections/{c}/lexicons` | `read` on `c` | collection lexicons |
| `POST /collections/{c}/lexicons/{name}` | `admin` on `c` | upload the next version |
| `GET …/lexicons/{name}/{version}` | as listing | fetch one version (`latest` allowed) |
| `DELETE …/lexicons/{name}/{version}` | as upload | delete an unreferenced version |
| `POST /lexicons/check` | `admin` on any collection | run a cases file against a lexicon, uploaded or given inline; stores nothing |

The response of an upload and a fetch carries `name`, `version`, `hash`, `scope`,
`created_by`, `created_at` and, on fetch, the lexicon itself.

### 2.7 Decisions taken

- Plugin interface with a lexicon-driven built-in, not a rule language (decided
  2026-11-16). The LaTeX handling needs a parser; the vocabulary is data.
- Lexicons are versioned, immutable objects managed through the API, owned by a
  collection or by the service, and pinned by indexes (decided 2026-11-19). Normaliser
  code stays a plugin in the image.
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
| `normaliser`, `normaliser_version`, `lexicon` | the lexical normaliser and the exact lexicon version it uses (section 2) |
| `lexical_backend` | `tsvector` or `pg_textsearch` (section 6) |
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
  normaliser, and a lexicon reference such as `physics@latest`, resolved when the index
  is created).
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

- store vectors as `halfvec` (supported by pgvector, including HNSW), which halves
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

## 6. Lexical backends

### 6.1 Purpose

The lexical leg must rank like BM25 on a managed PostgreSQL that has no BM25 extension.
`ts_rank_cd` counts and positions query terms but has no inverse document frequency: a
common word weighs as much as a rare detector name or decay channel. `pg_textsearch`
provides BM25 but needs `shared_preload_libraries`, which DBOD does not offer today
(see `docs/cern-infrastructure.md`). This section adds BM25 computed in plain SQL over the
tsvector that the default backend stores anyway, so the three rankings can be compared
before anyone asks a database service for an extension.

### 6.2 Backends and rankers

Two things are separate: what is stored (per index) and how it is ranked (per request).

| Storage (per index) | Ranker | Notes |
|---|---|---|
| `tsvector` | `ts_rank_cd` | built-in, no statistics needed |
| `tsvector` | `bm25` | BM25 in SQL over the same tsvector (6.4) |
| `pg_textsearch` | `bm25` | the extension's own index and scoring |

- The index records its lexical storage, next to the normaliser (section 3.2). It is set
  at index creation from `lexical.backend` in the configuration, with `auto` choosing
  `pg_textsearch` when the extension is installed and `tsvector` otherwise.
- On `tsvector` storage the ranker is a per-request switch, `lex_ranker`, with the
  default from `lexical.ranker` (initially `ts_rank_cd`). Switching needs no new index,
  so an evaluation can compare both on identical data.
- Results report the lexical score with the ranker that produced it.

### 6.3 Stored data for `tsvector`

Per chunk, built at ingest from the normaliser output with the two-part split of 2.4:

- `tsv tsvector`, with a GIN index. Chunks of all indexes share one table, so the GIN
  index is on `(index_id, tsv)` through `btree_gin`.
- `lex_len integer`: the number of lexeme positions in `tsv`, the document length for
  BM25. Counted the same way as term frequency, so the two are consistent.

Per index, for the `bm25` ranker:

```
lex_term_stats(index_id, lexeme, df)              -- chunks containing the lexeme
lex_index_stats(index_id, n_chunks, avg_len, refreshed_at)
```

Both are filled from `ts_stat` over the index's chunks, one full scan. They are
approximate by design: BM25 is insensitive to small drift in `df`, `N` and `avgdl`.

### 6.4 BM25 query

1. Normalise the lexical query and turn it into lexemes with the same two-part split as
   the documents. Duplicates are kept as query term frequency, which is then ignored
   (each distinct term counts once, as in common BM25 implementations).
2. Candidates: chunks of the index with `tsv @@` the OR of the lexemes, restricted by
   the request's collections and metadata filters in the same statement.
3. Candidate cap: when the summed `df` of the query terms exceeds
   `lexical.bm25.max_candidates` (default 20000), only the rarer terms (ascending `df`,
   until the sum fits) form the candidate predicate. All terms are still scored. A query
   made only of very common terms falls back to the full OR with the cap as a `LIMIT`
   after a `ts_rank_cd` pre-order.
4. Score each candidate with `unnest(tsv)`, where term frequency is the number of
   positions of the lexeme:

   ```
   score = Σ_terms  idf · tf · (k1 + 1) / (tf + k1 · (1 − b + b · lex_len / avg_len))
   idf   = ln(1 + (N − df + 0.5) / (df + 0.5))
   ```

   `k1` (default 1.2) and `b` (default 0.75) come from configuration. The `idf` form is
   the one that never goes negative. A lexeme missing from `lex_term_stats` (added since
   the last refresh) counts with `df = 1`.
5. Order by score, return the top `n` for fusion.

Phrases and exclusions in the lexical query are constraints on the candidate set, not
part of the score; their syntax belongs to the search pipeline section.

Known limits of the tsvector representation: at most 256 positions per lexeme, positions
capped at 16383, and 1 MB per tsvector. With chunks of about 900 tokens none of these is
reached.

### 6.5 Statistics refresh

- Refreshed for an index when it is set to `ready`, and after writes to it, debounced
  to at most once per `lexical.bm25.refresh_interval` (default 10 minutes). The refresh
  runs in the service, not through `pg_cron`, so nothing depends on an extension.
- A refresh replaces the index's rows in one transaction; searches see either the old
  or the new statistics.
- While an index has no statistics yet (`building`, no refresh), the `bm25` ranker
  falls back to `ts_rank_cd` and the result says so.

### 6.6 Performance and the case for pg_textsearch

The cost of 6.4 grows with the candidate set, because every candidate's tsvector is
unnested and scored, where `pg_textsearch` can stop early inside its index. For about
150k chunks the expected latency is tens to a few hundred milliseconds; this is measured
on the reference instance before the ranker becomes a default.

The evaluation repository compares `ts_rank_cd`, `bm25` (SQL) and fused results. A request
to a database service for `pg_textsearch` is made when that comparison shows either that
BM25 ranking matters and the SQL version is too slow, or that the SQL version is good
enough and no request is needed. A posting table (one row per lexeme and chunk) is not
planned: at this corpus size it is tens of millions of rows for little gain over 6.4.

### 6.7 Decisions taken

- BM25 in SQL is a ranker on the `tsvector` storage, selectable per request, not a
  third storage backend (decided 2026-11-19).
- The DBOD ticket for `pg_textsearch` is held until the comparison in 6.6 has numbers
  (decided 2026-11-19).

## 7. Metadata, aliases and links

### 7.1 Purpose and boundary

pg-retrieve must stay as generic as qmd: collections of Markdown documents, with nothing
in the schema that only one domain needs. Documents in every domain still carry
metadata, have other names (a DOI, a report number, a note's slug), and link to each
other (wikilinks in notes, references in papers). This section models exactly those
three generic things:

- **Metadata**: the document's frontmatter, with filterable fields declared by a profile.
- **Aliases**: other names of a document, `scheme:value`.
- **Links**: edges from a document to another document or to an alias, of a named kind,
  either extracted from the Markdown or supplied with the document.

Everything domain-specific (which frontmatter fields matter, which alias schemes exist
and how they are normalised, which link kinds exist) is data in a **profile** (7.2), as
the physics vocabulary is data in a lexicon (2.6). Appendix A is the scientific profile
used for DELPHI.

Interpretation built on top (person identities and affiliations, concepts, measurements
as records, which analysis supersedes which, links an LLM infers) belongs to a future
knowledge layer such as the wiki, not to this service.

One service holds many collections with different audiences (section 1). A link can
therefore point to a document the reader can read, to one the service holds but hides
from that reader, or to something outside the service. What is stored about a link is
the same for everyone; what a reader sees is computed per reader (7.7).

A corpus without metadata, aliases or links leaves the tables empty and loses nothing
else. At the scale of the reference corpus (about 3,300 documents, an estimated 100,000
references) PostgreSQL answers filter, backlink and citation-chain queries in
milliseconds; a graph database would add a second store with its own access control for
no gain.

### 7.2 Profiles

A profile is a JSON object with three parts:

```
{
  "fields":  { "<name>": {"type": "...", "from": "<path>"} , ... },     // 7.3
  "aliases": { "<scheme>": {"from": "<path>", "normalise": [...]}, ... },// 7.4
  "link_kinds": ["cites", "erratum_of", ...]                             // 7.5
}
```

- Profiles are stored and managed exactly like lexicons (2.6): named, versioned,
  immutable versions with a content hash, owned by a collection or by the service, with
  the same endpoints under `/profiles` and `/collections/{c}/profiles`. Regex
  normalisation steps are accepted only in service profiles.
- Built-in service profiles: `generic` (fields `title`, `tags`, `date`; no alias
  schemes; link kind `links_to` only) and `scientific` (appendix A).
- A collection names one profile version (`profile`, default `generic@latest`,
  resolved when the collection is created). Changing it needs `admin`.
- Metadata, aliases and links belong to documents, not to indexes, so a profile change
  needs no new index. The server re-derives filter values and aliases from the stored
  frontmatter in the background and re-resolves links (7.5); the collection reports
  progress, and filters use the old values until it completes.

### 7.3 Metadata and filters

- A document's metadata is its frontmatter plus any `metadata` object in the ingest or
  write payload, stored as JSON with the document. The raw frontmatter is always kept.
- The profile declares the filterable fields. `from` is a path into the metadata
  (`year`, `card.topics`), defaulting to the field name. Types:

| Type | Filter semantics |
|---|---|
| `keyword` | equality, or any of a list |
| `keyword[]` | the document's list contains any (or all, with `all: true`) of the values |
| `int`, `date` | equality, list, or range `{"from": .., "to": ..}` |
| `person[]` | list of names; matches case- and accent-insensitively on surname, with optional initials (`Hamacher`, `Hamacher K`) |
| `text` | case-insensitive substring; for titles and short fields only |

- Every list field also accepts a length filter (`{"max_len": 100}`), so for example
  documents with very long author lists can be excluded without a dedicated parameter.
- Undeclared metadata is returned in `outline` but cannot be filtered.
- `list_collections` reports each collection's declared fields with types and example
  values (section 4.4).
- Storage: one `metadata jsonb` column with a GIN index, plus the derived filter values.
  At this corpus size filters on documents are cheap; per-field indexes are not needed.

### 7.4 Aliases

- A document has its id plus any number of aliases `scheme:value` derived by the profile
  from the metadata (`"doi": {"from": "doi", "normalise": ["lower"]}`) or supplied in the
  payload.
- Normalisation steps are a fixed list: `lower`, `upper`, `trim`, `collapse_space`,
  `strip_prefix:<text>`, and in service profiles `replace:<regex>:<replacement>`.
  The same steps are applied to a link's target identifier before matching.
- Aliases are unique per scheme within a collection, not across collections: the same
  DOI may be held by a public paper and an internal draft in two collections. Two
  documents of one collection with the same alias are an ingest error.
- Aliases are addressable: `get_document`, `outline` and wikilinks accept
  `scheme:value` wherever a document id is accepted.

### 7.5 Links

```
document_aliases(doc_id, scheme, value)  UNIQUE (collection_id, scheme, value)
links(id, doc_id, kind, position, lines, pages,
      target_ref,                         -- doc id or scheme:value as written/cited
      text NULL,                          -- link label or reference text
      origin, created_at)                 -- origin: extracted | payload
link_targets(link_id, target_doc_id, target_section NULL, matched_by)
```

- **Two origins, one table.** `extracted` links come from the Markdown (7.6) and have kind
  `links_to`. `payload` links arrive with the document, for example the reference list
  of a paper, extracted upstream, with a kind from the collection's profile; an unknown
  kind is an ingest error. Payload links are never checked or rejected: they are facts
  about a source document, including its mistakes.
- **Targets.** `link_targets` holds every document of the service the target reference
  matches, by id or by alias, in any collection. One reference can match several
  documents. `matched_by` records how. There is no stored status: whether a link is
  resolved depends on the reader (7.7).
- **Maintenance.** After each ingest, write or delete, and after a profile change, the
  server updates `link_targets` for links whose reference matches the added, changed or
  removed document or aliases, in every collection, so a reference to a document
  ingested later resolves without re-posting.
- Every link keeps its position with line and page provenance, like chunks, and is
  replaced with its document on re-ingest or rewrite.
- pg-retrieve never looks anything up outside itself: the only outbound destination is
  the configured model endpoint. Resolution against external catalogues happens upstream
  of the ingest.

### 7.6 Page links: extraction and checking

The server extracts `links_to` edges from the Markdown of every document it stores, on
ingest and on every write (section 4.5):

| Form | Example |
|---|---|
| wikilink | `[[delphi-1995-012-phys-487]]`, `[[delphi-1995-012-phys-487|label]]` |
| wikilink to a section | `[[delphi-1995-012-phys-487#4.2]]`, `[[...#Systematic errors]]` |
| wikilink by alias | `[[doi:10.1016/0370-2693(95)01234-5]]`, `[[report:DELPHI-95-12]]` |
| wikilink to another collection | `[[delphi/notes/delphi-1995-012-phys-487]]` |
| resource URI | `[label](pg-retrieve://delphi/notes/delphi-1995-012-phys-487)` |

- A bare id or alias resolves in the page's own collection first, then in collections
  sharing its prefix (`delphi/*`), then anywhere. Links point to ids or aliases, which do
  not change; titles are only labels, so editing a title breaks nothing.
- A section target is checked against the target's outline (number or heading path).
- Links inside code blocks and inline code are ignored. Ordinary web URLs are not edges.
- **Checking** is a per-collection setting, `links.unresolved`:
  - `flag` (default): the write succeeds, the link is stored without targets, and the
    response lists it as unresolved.
  - `reject`: a write with an unresolved `links_to` edge fails with the list of broken
    links. Nothing is stored. No broken link can enter the collection, which replaces a
    lint pass.
  Checking uses the writer's own rights: a link whose only targets the writer cannot read
  counts as unresolved for them.
- **Narrowing links.** When a writer links to a document whose collection is readable by
  fewer principals than the page's own collection, the write succeeds and the response
  warns that some readers of the page will see only the reference. The comparison is made
  on grants, so it is approximate when group membership comes from an external provider.
- **Deleting a linked document.** `delete_document` reports the incoming `links_to` edges
  from documents the deleting principal can read. In a `reject` collection it refuses
  while any exist unless `force` is given; forced or not, those links lose the target.
- Changing `links.unresolved` to `reject` does not touch existing pages; the response
  lists the collection's unresolved links. The change needs `admin`.

### 7.7 Access and what a reader sees

The status of a link is computed for each reader from the stored targets:

| Stored situation | Reader sees |
|---|---|
| at least one target in a collection the reader can read | `resolved`: the preferred readable target's id, title and section |
| targets exist, none readable by this reader | `external`: the reference as written and the text |
| no target in the service, reference is an alias | `external`: the reference as written and the text |
| no target, reference is not an alias (a bare id or free text) | `unresolved` |

- A hidden target and a document the service does not hold look exactly alike, in the
  status and the fields, and are answered by the same query, so readers cannot probe
  which hidden documents exist. The reference itself is not secret: it is written in the
  linking document, which the reader can read.
- Among several readable targets the preferred one is in the linking document's own
  collection, then in a collection sharing its prefix, then the most recent. The others
  are listed as `also`.
- Incoming links, counts in `outline`, and any later ranking use only links whose
  linking document the reader can read.
- Metadata and aliases are visible only through readable documents; filters return only
  readable documents.
- External references can carry a URL for clients, from per-scheme templates in the
  profile or config (`doi: https://doi.org/{value}`). The server builds the string and
  never fetches it.

### 7.8 Search and MCP

- **Filters** in `search` and `list_documents` are the profile's declared fields (7.3),
  including list-length filters, plus `alias` (`scheme:value`).
- **`outline`** includes the metadata (long lists truncated with their length), the
  aliases, the number of outgoing links per kind and of readable incoming links.
- **Tools:**

| Tool | Parameters | Returns |
|---|---|---|
| `get_links` | `doc_id`, `kind` (optional, list), `status` (optional), `limit`, `cursor` | the document's outgoing links in order: kind, text, lines and pages, the status for this reader (7.7), the preferred readable target and `also`, or the reference with its URL |
| `linked_from` | `doc_id`, `kind` (optional, list), `limit`, `cursor` | readable documents with links to it (citing papers, wiki pages), each with the kind and the linking lines and pages |

  The write tools (4.5) return the extracted links, the unresolved ones and any
  narrowing warnings. The server instructions add: follow links with `get_links` and
  `linked_from` rather than searching for titles; treat external references as leads
  outside the corpus; in a `reject` collection, fix or remove broken links instead of
  retrying the write.
- The HTTP API exposes the same under `/documents/{id}/links` and
  `/documents/{id}/linked-from`.
- Ranking does not use links. Boosting by incoming link count is a possible later option
  for the evaluation, not part of this section.

### 7.9 Decisions taken

- The core models only metadata, aliases and links; everything domain-specific is a
  profile, managed like lexicons (decided 2026-11-19, replacing a draft with persons,
  identifiers and references as tables).
- Person identities and affiliations are not modelled; authors are a filterable
  metadata field. Disambiguation belongs to the knowledge layer.
- Markdown stays the source of truth for pages; page links are derived on every write
  and checked there per collection, instead of by a lint pass. Whether the wiki itself
  lives in git or in a writable collection is the wiki's decision.
- What is stored about a link is the same for everyone; its status is computed per
  reader, and a hidden target is indistinguishable from one the service does not hold.
- One service instance; other catalogues and services are external references with
  optional URL templates. No graph database at this scale.

## Appendix A. Scientific profile and the DELPHI mapping

### A.1 What the DELPHI corpus provides

Measured on 2026-11-19:

- Frontmatter: `authors`, `author_count`, `collaborations`, `report_numbers`, `doi`,
  `arxiv`, `inspire`, `cds`, `conferences`, `year`, `category`, `kind`, `languages`,
  `keywords`.
- `md/authors.jsonl`: per document an ordered author list (name, affiliations, some
  INSPIRE, ORCID and ROR ids) and, for theses, supervisors. 183,214 author entries; 351
  documents have more than 100 authors (up to 2,446).
- Per-document cards: `topics`, `detectors`, `data_years`.
- References exist only as bibliography sections in the Markdown, clean for born-digital
  documents and noisy for scans. Nothing is extracted yet.
- The source documents contain no wikilinks; the wiki pages built from them upstream do.

### A.2 The `scientific` profile

```
{
  "fields": {
    "year":           {"type": "int"},
    "kind":           {"type": "keyword"},
    "category":       {"type": "keyword"},
    "languages":      {"type": "keyword[]"},
    "authors":        {"type": "person[]"},
    "supervisors":    {"type": "person[]"},
    "collaborations": {"type": "keyword[]"},
    "keywords":       {"type": "keyword[]"},
    "topics":         {"type": "keyword[]", "from": "card.topics"},
    "detectors":      {"type": "keyword[]", "from": "card.detectors"},
    "data_years":     {"type": "keyword[]", "from": "card.data_years"},
    "title":          {"type": "text"}
  },
  "aliases": {
    "doi":     {"from": "doi",            "normalise": ["trim", "lower", "strip_prefix:https://doi.org/"]},
    "arxiv":   {"from": "arxiv",          "normalise": ["trim", "lower", "strip_prefix:arxiv:", "replace:v[0-9]+$:"]},
    "report":  {"from": "report_numbers", "normalise": ["trim", "upper", "collapse_space"]},
    "inspire": {"from": "inspire"},
    "cds":     {"from": "cds"}
  },
  "link_kinds": ["cites", "erratum_of", "supersedes", "see_also"],
  "url_templates": {
    "doi": "https://doi.org/{value}",
    "arxiv": "https://arxiv.org/abs/{value}",
    "inspire": "https://inspirehep.net/literature/{value}"
  }
}
```

A filter `authors: "Hamacher"` with `authors: {"max_len": 100}` finds a member's own
notes without every collaboration paper.

### A.3 DELPHI ingest mapping

- The ingest CLI merges `authors.jsonl` (names in order, supervisors) and the card into
  the metadata it posts. Affiliations and person ids are kept in the metadata as
  delivered but are not declared fields.
- References: once delphi-knowledge-base extracts reference lists (from INSPIRE or the
  bibliography sections), the ingest CLI posts them as `payload` links of kind `cites`,
  with the cited identifier as `scheme:value` when known and the raw text always.
