Here are the main alternatives to **qmd** ([tobi/qmd](https://github.com/tobi/qmd)), Tobi Lütke's local CLI search engine for Markdown. It combines BM25, vector search and LLM reranking, and it has an MCP server.

**Projects built on qmd's design**

- **[picoqmd](https://github.com/altSeneca/picoqmd)**: the closest drop-in. It does the same thing (local hybrid search plus an MCP server), but ships as a single Go binary of about 15 MB. It doesn't need Node.js and runs on a Raspberry Pi.
- **[ehc-io/qmd](https://github.com/ehc-io/qmd)** ("Query Markdown"): a separate MCP server for hybrid search over Markdown knowledge bases.
- **[lazyqmd](https://alexanderzeitler.com/articles/introducing-lazyqmd-a-tui-for-qmd/)**: not a replacement. It adds a terminal UI on top of qmd.

**Other tools with a similar purpose** (from general knowledge; I haven't checked their current state)

- **ck** or **semtools**: "semantic grep" CLIs, lighter-weight than qmd.
- **basic-memory**: a Markdown knowledge base with an MCP server, aimed at agent memory.
- **Khoj**: self-hosted search and chat over your notes and documents. It's heavier and includes a web UI.
- **txtai** or a **ChromaDB**-based MCP server: build-your-own options if you want control over the embedding and indexing steps.
- **Obsidian Smart Connections**: if your notes are already in Obsidian.
- **ripgrep + fzf**: no semantic search, but often enough for plain keyword lookup.

If you want qmd's hybrid search without the Node dependency, picoqmd is the one to try. If you tell me your setup (OS, number of documents, whether you need MCP for agents), I can narrow it down.

Sources:
- [tobi/qmd on GitHub](https://github.com/tobi/qmd)
- [picoqmd on GitHub](https://github.com/altSeneca/picoqmd)
- [ehc-io/qmd on GitHub](https://github.com/ehc-io/qmd)
- [Introducing lazyqmd (Alexander Zeitler)](https://alexanderzeitler.com/articles/introducing-lazyqmd-a-tui-for-qmd/)
