# The DELPHI corpus as ingest source

Verified 2026-11-16 against the sibling repository `../delphi-knowledge-base` and its data
on EOS. This answers the "where does the Markdown come from" question for the reference
deployment. Other corpora only need to follow the same shape: Markdown with YAML
frontmatter, plus optional metadata sidecars.

## Locations

Base directory `$DELPHI_KB_EOS` = `/eos/user/l/liko/delphi-knowledge-base`.

| Path | Content |
|---|---|
| `md/<category>/<year>/<id>.md` | 3,277 Markdown files, 230 MB, 224 M characters (avg 68 k per document) |
| `md/manifest.jsonl` | one line per document: id, paths to md, pdf and docling JSON, char count, heading and symbol-fix statistics, frontmatter hash |
| `md/authors.jsonl` | author lists |
| `docling/<category>/<year>/<id>__<src>.json.gz` | Docling JSON per document, with page provenance per text item |
| `pdf/<category>/<year>/<id>__<src>.pdf` | source PDFs |
| `wiki/docs/<category>/<year>/<id>.md` | one wiki page per document (4,515, includes documents without full text) |
| `wiki/{topics,detector,software,timeline}/` | LLM synthesis pages |
| `wiki-cards/<id>.json` | LLM card per document: summary, topics, detectors, software, datasets, measurements |
| `qmd/{gemma,qwen}.sqlite` | the existing qmd indexes, 1.1 GB and 1.3 GB |

Documents per category: notes 1,556, conference 907, publications 458, theses 318,
other 32, reviews 6. Document ids are `delphi-<yyyy>-<nnn>-<series>-<nnn>` for notes and
`ins<recid>` or `cds<recid>` otherwise. They are stable and should be pg-retrieve's
document ids.

## Languages

From the frontmatter `languages` field: about 3,080 documents are English, about 200 are
not (French, German, Italian, Spanish, Portuguese, a few others; mostly theses). The
language codes mix ISO 639-2 and 639-1 (`fre` and `fr`, `ger` and `de`), so normalise at
ingest. The non-English documents are long, so they are about 30% of the pages.

## Frontmatter

Every file starts with YAML frontmatter from the catalog. Fields seen in a sample:

- identity: `id`, `title`, `category`, `kind`, `year`, `date`
- catalog: `authors`, `author_count`, `collaborations`, `report_numbers`, `journal`,
  `doi`, `arxiv`, `inspire`, `cds`, `conferences`, `refereed`, `official`, `record`
- content: `languages`, `keywords`, `abstract`, `symbols` (spelling variants of physics
  symbols used in the text, for lexical search)
- conversion: `source.pdf`, `source.docling`, `source.mode` (native or OCR),
  `source.pages`, `tables`, `pictures`, `formulas`, `text_ok`, `converted_at`

The body is Docling's Markdown export: the catalog title as the single `#` heading, then
`##` and deeper headings restored by the repository's heading heuristics, LaTeX math in
`$...$`, and tables. Author lists often appear as a code block.

## Metadata for filters

The filter fields named in CLAUDE.md come from two places:

- `category`, `kind`, `year`, `languages`, `keywords`: frontmatter.
- `topics`, `detectors`, `software`, `datasets.data_years`, `datasets.phase`: the card
  JSON in `wiki-cards/<id>.json` (`source_md_hash` ties it to the Markdown version).

The ingest CLI therefore reads the Markdown and, when present, the card sidecar.

## Page provenance: not in the Markdown

The Markdown has no page markers at all. The Docling JSON has them: every text item
carries `prov[].page_no` and a character span. Two ways to get page ranges on chunks:

1. The upstream repository adds page markers at export (its backlog lists
   "Page markers: docling's `page_break_placeholder` in the export, for page citations and
   for tracing search chunks back to a page"). pg-retrieve's chunker then reads the
   markers. Preferred: one source of truth, and the wiki needs the same markers.
2. pg-retrieve's ingest reads the Docling JSON next to the Markdown and maps chunk
   character offsets to pages. Fallback if the markers do not arrive.

Until one of the two exists, chunks carry heading path and character offsets, and page
provenance is reported as unknown rather than guessed.

## What the existing qmd indexes tell us

Both indexes were built on a T4 on 2026-10-16 over the six Markdown categories plus the
`wiki-docs` collection (7,792 documents in all).

| Index | Model | Dimension | Chunks | Size | Embed speed on T4 |
|---|---|---|---|---|---|
| gemma | embeddinggemma-300M Q8_0 (GGUF) | 768 | 147,232 | 1.1 GB | ~30 chunks/s |
| qwen | Qwen3-Embedding-0.6B Q8_0 (GGUF) | 1024 | 150,603 | 1.3 GB | ~9 chunks/s |

- Chunking: about 900 tokens, about 19 chunks per document on average, up to 611 for the
  longest thesis. The two tokenizers chunk the same text slightly differently.
- qmd stores an `embed_fingerprint` per vector (a short hash of the model file). This is
  the same idea as the fingerprint in CLAUDE.md, just coarser.
- Sizing for PostgreSQL: 150k vectors of 1024 float32 are about 600 MB of data plus an
  HNSW index of similar size. Halfvec would halve both. This fits a DBOD instance with a
  2 GB `shared_buffers` cap, but not comfortably with both models at once.
- BM25 in qmd runs with a custom normaliser (`k8s/qmd/searchnorm.mjs`) that maps physics
  symbol spellings to one token (`αs`, `alpha_s`, `$\alpha_s$` to `alphasx`; `e+e-` to
  `epem`). It is applied to documents before indexing and to every lexical query. Without
  it, `e+e-` tokenises to single letters and symbol queries miss. pg-retrieve's lexical
  leg needs the same hook, on both backends, with the normaliser version recorded so a
  change rebuilds only the lexical index.
- Search on the lxplus CPU with qmd: lexical plus vector 7 to 8 s, with reranking 75 s
  and more, full hybrid several minutes. That is the baseline pg-retrieve replaces.
