# Points for Discussion with the CERN ML Help Desk

DELPHI Knowledge Base — Dietrich Liko, MBI Vienna

## Introduction

The DPHEP initiative preserves collider data, but the data remain usable only as long as the
knowledge around them survives: how the detector worked, how the software behaves, and why
analysis choices were made. For the LEP experiments, the people who carry this knowledge are
becoming fewer. As a pilot project, we are building a knowledge base for DELPHI that serves both
physicists and AI agents.

The corpus comprises roughly 3,300 documents (113,000 pages): 1,560 DELPHI internal notes,
920 conference contributions, 460 publications and 320 theses. About 40 % of them were
digitised from scans. The internal notes are particularly valuable, as they record detector and
analysis details that never made it into the formal papers. All documents are converted to
Markdown and indexed for keyword and semantic search. On top of this, an LLM-generated wiki of
linked topic pages, organised by a taxonomy and to be reviewed by former collaborators, is hosted
on the web ([cern.ch/liko/delphi-kb](https://cern.ch/liko/delphi-kb)). A separate MCP server lets
agents navigate the legacy FORTRAN code. The heavy LLM work runs on the CERN AI Gateway.

The project was presented at the workshop *Knowledge Bases and Agents for CERN* (19–23
October 2026): D. Liko, U. Schwickerath, "Keeping LEP-era knowledge usable: a DELPHI
knowledge base for physicists and AI agents",
[indico.cern.ch/event/1716863/contributions/7317467](https://indico.cern.ch/event/1716863/contributions/7317467).

**Purpose of this meeting.** We would like to discuss a range of topics with people who have more
experience in these areas. The points below include concrete questions, but we see the meeting
just as much as an exchange of experiences: what we tried, what worked and what did not, and
what you have seen elsewhere.

---

## 1) How the knowledge base was built

The knowledge base was built with a map-reduce strategy in two phases:

1. **Document summaries (map).** `qwen3.8-27b-fp16` summarised each document. For most
   documents this was done in a single pass; long documents were summarised recursively,
   chapter by chapter, and the chapter summaries were then combined. Depending on the
   document type, it also extracted results and performance numbers, recorded the symbols
   used in the text for normalisation, and assigned the relevant taxonomy terms. Degradation of
   the scan was also detected and recorded.
2. **Topic summaries (reduce).** A more powerful model (`glm-5.3-flash-int4`) wrote
   summaries per topic, using OpenCode as a fast way to implement the agentic workflow. Most
   topics have a large number of associated documents. Guided by the phase-1 summaries, the
   model read these documents agentically, and it was instructed to use the hybrid search to
   find material that might have been missed. This means intense agent activity, as often on the
   order of 100 papers had to be read for one topic.

A separate issue was iteration: when a text failed the programmatic checks for wrongly extracted
references, it had to be regenerated.

As the experiment is finished, there was no need to implement an update procedure for new
documents. Such a procedure is foreseen for the future.

---

## 2) OCR and PDF conversion of physics documents

For notes and papers that are only available on paper, we used Docling with Tesseract. Docling
uses a dedicated model to recognise formulas, which worked well. However, inline symbols in the
running text, such as $\alpha_s$, were not decoded. These are essential for physics texts, so a
second correction pass with a VLM (`qwen3.8-27b-fp16` on the AI Gateway) was needed. The same
applies to older digitally created documents that lack Unicode mapping tables.

Surprisingly, we saw similar problems with modern PDFs, where symbols are also not recognised
correctly. One way around this would be to process the HTML5 versions from ar5iv, which however
only exist for papers with LaTeX source on arXiv; another is again to use VLMs.

We plan to evaluate modern OCR tools such as Nougat, olmOCR, Chandra, or Mistral OCR on the
documents that phase 1 flagged as degraded. What experience do you have with these tools, or
with OCR of physics documents in general? Which tools would you include in such an evaluation,
and how would you run them at CERN?

---

## 3) Hybrid search

We use QMD (Query Markup Documents, [github.com/tobi/qmd](https://github.com/tobi/qmd)) as our
hybrid search tool. It combines query expansion, HyDE, BM25 lexical search and semantic vector
search; results are fused with Reciprocal Rank Fusion and reordered by a reranker. QMD runs three
local GGUF models:

| Role | Model used by QMD | Base model |
|---|---|---|
| Query expansion | [tobil/qmd-query-expansion-1.7B-gguf](https://huggingface.co/tobil/qmd-query-expansion-1.7B-gguf) (Q4_K_M) | [Qwen/Qwen3-1.7B](https://huggingface.co/Qwen/Qwen3-1.7B), fine-tuned with LoRA |
| Embeddings (default) | [ggml-org/embeddinggemma-300M-GGUF](https://huggingface.co/ggml-org/embeddinggemma-300M-GGUF) (Q8_0) | [google/embeddinggemma-300m](https://huggingface.co/google/embeddinggemma-300m) |
| Embeddings (alternative, multilingual) | [Qwen/Qwen3-Embedding-0.6B-GGUF](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF) (Q8_0) | [Qwen/Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) |
| Reranking | [ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF](https://huggingface.co/ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF) | [Qwen/Qwen3-Reranker-0.6B](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B) |

QMD is meant to be a lightweight tool that runs on the same machine as the coding harness. This
works well on a Mac with Apple Silicon, but evidently not on lxplus: lexical and semantic search ran
with acceptable speed, while query expansion and reranking had to be disabled, which is perhaps
not surprising without a GPU.

QMD itself is built on SQLite (FTS5 and sqlite-vec) with local models, so moving to CERN PaaS
means re-implementing the pipeline rather than installing QMD: DBOD (PostgreSQL, with pgvector
for the vectors) for storage and search, and the AI Gateway for the models. Which building blocks
would you use for hybrid search at CERN?

### 3.1 Query expansion

QMD uses a Qwen3-1.7B model fine-tuned (LoRA) specifically to produce typed query expansions
(lexical keywords, dense-retrieval sentences, and a HyDE passage). We don't expect a similar
model on the AI Gateway, so we are considering a prompt-based implementation with a general
model. Alternatively, such a small model could be deployed through ml.cern.ch model serving.
Which approach would you recommend?

### 3.2 HyDE

HyDE (Hypothetical Document Embeddings) lets an LLM write a short hypothetical answer to the
query and searches with the embedding of that answer instead of the query itself. We did not find
this particularly useful for particle physics: small models have too little domain knowledge, so the
hypothetical passage often points in the wrong direction. What is your experience?

### 3.3 Lexical search

The gold standard for lexical search is BM25, not the GIN/tsvector full-text search supported on
DBOD. A BM25 implementation exists as the PostgreSQL extension
[pg_textsearch](https://github.com/timescale/pg_textsearch) (Tiger Data/Timescale, PostgreSQL
licence), which supports PostgreSQL 17 and 18. It has to be loaded via `shared_preload_libraries`,
so it requires a server-side configuration change, and it is not among the standard extensions
listed in the DBOD documentation. We think this or a similar feature would be useful beyond our
use case. How do others at CERN handle lexical ranking, and who would be the right contact to
discuss adding such an extension?

### 3.4 Semantic search

The AI Gateway offers `embeddinggemma-300m` and `e5-large-v2`. Our corpus is partly
multilingual, so `e5-large-v2` (English-only) is not an option; `embeddinggemma-300m` is also
QMD's default model. We would like to evaluate Qwen3-Embedding as well, which QMD supports
as an alternative for multilingual corpora. It is more compute-intensive, but it would be an
interesting benchmark on our sample of physics texts. Which embedding models have worked well
for scientific texts in your experience?

We are also aware of BERT-based embedding models trained specifically on physics literature,
such as PhysBERT and AccPhysBERT (LBNL). Do you have experience with such models, and how
do they compare with general-purpose models for retrieval? Two practical points for us are that
BERT models are typically limited to 512 tokens, which would require smaller chunks than we use
now, and that they are mostly English-only.

### 3.5 Chunking

We use the chunking implemented in QMD: documents are split into chunks of about 900 tokens
with 15 % overlap, and break points are chosen preferably at chapter and section headings, so
chunks mostly follow the document structure. For longer chapters we are considering semantic
chunking, i.e. placing the boundaries where the topic changes rather than at a fixed length. What is
your experience with semantic chunking, or with other chunking strategies for long scientific
documents?

### 3.6 Bulk embedding and model references

Bulk insertion of text is compute-intensive, so we run it separately on a GPU instance of ml.cern.ch
(Nvidia T4 16 GB; the personal quota is one Nvidia GPU). Query embeddings would then come from
the AI Gateway, so the vectors must be compatible. For this we need, for each embedding model
served on the gateway:

- the exact Hugging Face repository and revision (commit),
- the precision or quantisation used for serving,
- the inference engine and its version,
- the maximum input length and how longer inputs are handled (truncation),
- the output dimension (full, or truncated via Matryoshka),
- pooling and whether vectors are normalised.

Where is this information best found — is the "Model Info" section in the AI Gateway UI the
intended reference? And how would you organise bulk embedding at CERN?

### 3.7 Embedding prompts

Both candidate models expect task prompts:

- EmbeddingGemma: queries as `task: search result | query: {query}`, documents as
  `title: {title} | text: {content}`.
- Qwen3-Embedding: queries as `Instruct: {task description}\nQuery:{query}`,
  documents without instruction. Qwen reports 1–5 % better retrieval with instructions and
  recommends writing them in English.

QMD adjusts the prompt format automatically for each model family. How are these prompts
handled on the gateway side, and what should the client do?

### 3.8 Reranking

QMD uses Qwen3-Reranker-0.6B. The gateway currently lists no reranker. Reranking could also be
done with a general model via prompts (asking whether a document is relevant). For a continuous
score, this requires log-probabilities of the answer tokens; QMD scores relevance this way (yes/no
with logprob confidence). How would you implement reranking with the gateway as it is today, and
are there plans in this direction?

---

## 4) Agentic use of gateway models

### 4.1 INT4 degradation

We observed a degradation of agentic capabilities with the INT4 model (`glm-5.3-flash-int4`).
We worked around this by preparing a complete lookup of the relevant document passages and
supplying it as a large context. Even so, significant iterations were needed to correct false
quotations. Have you seen similar effects, and how do you deal with them?

### 4.2 Comparison with external models

A test with GLM-5.3 on OpenRouter and with Claude Sonnet 5.5 was significantly more successful,
which is perhaps no surprise, as the gateway model is the smaller Flash variant and quantised to
INT4. We now plan to compare with `glm-5.3-fp8-vertex`. Since it runs on Google Cloud (Vertex
AI), what should we consider regarding quotas, costs, and which data may be sent to it?

### 4.3 Caching

We observed better caching with GLM-5.3 on OpenRouter but have not studied it further. Out of
curiosity: how is the CERN installation set up (vLLM or SGLang, prefix caching)?

### 4.4 Where to run agents

Running OpenCode in a dedicated pod on CERN PaaS would give container isolation (see section
6), but it may exceed the intended use of the service: PaaS is meant for web applications and
services, while topic summaries are long-running, compute-heavy batch work with intense agent
activity. Alternatives would be ml.cern.ch (interactive sessions, pipelines) or batch jobs, as long as
they can reach the AI Gateway. Where would you run such agentic workloads at CERN?

---

## 5) Licensing

We would provide Markdown versions of documents for agents and links to the originals for human
readers. The DELPHI notes have been released; the open questions concern published papers and
preprints:

- **Papers published before SCOAP3 (started 1 January 2014):** the journal versions are
  generally under publisher copyright. Can Markdown versions of these, or of the
  corresponding preprints, be provided?
- **CERN preprints:** under the CERN Open Access Policy (since 2014), CERN publications,
  including arXiv submissions, should carry an open licence with CC-BY as default. What
  applies to older CERN preprints (e.g. CERN-EP reports of the LEP era)?
- **arXiv:** most e-prints use the default arXiv licence, which grants distribution rights to arXiv
  only and no reuse rights to others; submissions before 2004 carry an equivalent assumed
  licence. We would therefore need case-by-case rules based on the licence in the arXiv
  metadata.
- **Text and data mining:** the research TDM exception (EU DSM Directive, Art. 3) covers
  analysis, but not necessarily public redistribution of the converted text.

How have other projects at CERN handled this, and who would be the right contact, for example the
Scientific Information Service?

---

## 6) Security

Reading data from remote sources such as arXiv carries a risk of prompt injection. This has already
been observed: hidden instructions aimed at LLM reviewers were found in arXiv papers in 2025,
mainly in computer science. To our knowledge it has not yet appeared in hep-ph or hep-ex.

The risk is larger for agents than for plain summarisation: our agents run in OpenCode with tool
access, so a manipulated document could trigger actions, not only distort a summary. We
restricted OpenCode's permissions to what is reasonable for the workflow, but such configuration-
level restrictions may not be sufficient, in particular when the agent is deployed outside a container.
This is different in a dedicated pod on CERN PaaS, which isolates the agent from other systems.
However, the pod still holds credentials (AI Gateway key, database access) and has network
access, so a manipulated document could still try to misuse or leak them. How do you sandbox
agents with tool access, and are there CERN guidelines or established mitigation procedures?

---

## Outcomes (recorded 2026-11-16, after the meeting)

What came out of the discussion, and what it means for pg-retrieve.

- **Lexical search.** Check the existing DBOD PostgreSQL 17 instance for `pg_textsearch`
  and the pgvector version before asking anyone. Todo, with the `check-postgres-target` skill.
- **Embedding models.** Qwen3-Embedding is the alternative to test. We run it ourselves on a
  T4 instance at ml.cern.ch. The help desk had no details on how the gateway embedding
  models are served; there was some discussion that the CERN models may be hosted with
  wrong options. Bulk embedding therefore needs our own compatibility test: embed the same
  texts through the gateway and locally, and compare the vectors.
- **Embedding prompts.** No answer. Assume the client must add the task prefixes itself.
- **Query expansion.** Support both: running the `tobil/qmd-query-expansion` model
  (locally or served), and a prompt-based expansion with a gateway model. HyDE stays
  possible but needs evaluation before it is switched on.
- **Embeddings backends.** Same principle: local models as in QMD, and an OpenAI-compatible
  endpoint, selectable by config.
- **Reranking.** Same principle. Test whether a hosted model returns `top_logprobs`; if not,
  implement our own scoring.
- **Chunking.** No recommendation. We evaluate ourselves.
- **PaaS.** No information. Assume it is acceptable to recreate embeddings when the
  embedding computation runs remotely.
- **Caching and model choice.** No information on the gateway's serving stack. The gateway
  is heavily used, so it is not very strong. pg-retrieve does not need GLM-5.3; it needs a
  small model for query expansion and reranking.
- **Vertex AI models.** Dropped.
- **Licensing and agent sandboxing.** Not issues for this part of the work.
