# Knowledge (RAG)

codx-junior indexes each project so the AI can find the right code and documents before it answers or acts.

![Knowledge](/images/NOV2025/codx-junior-rag-knowledge.png)

## How it works

1. Project files are split into chunks with a code-aware splitter.
2. Chunks are embedded with the project's **embeddings model** and stored in the
   project's knowledge index (embedded **LanceDB**).
3. When a chat or an agent needs context, codx-junior searches the index, filters the results by relevance and passes them to the model.
4. The background worker re-indexes files as they change when **watching** is on.

## Search

* **Similarity search** with a configurable number of documents and relevance cutoffs.
* **AI search**: the model rewrites the query, searches with several terms and validates the results.
* **Keywords** and a **knowledge graph** help connect related files.
* Agents use the `project_search` tool with one or many queries at once.

Try queries yourself from the knowledge search dialog.

## Knowledge settings

Open **Settings → Knowledge settings** to:

* see index statistics and the list of indexed files,
* reload a single path or the whole project,
* delete sources from the index,
* edit **ignore patterns**,
* add **external folders** and include **sub-projects**,
* tune the search type, document count, relevance cutoff and RAG distance,
* enable tag extraction and document enrichment.

Chats can also be indexed: give a chat **knowledge topics** and its content becomes searchable knowledge for the project.

## Storage backend

The vector store is **LanceDB**, embedded in the API process
(`api/codx/junior/knowledge/knowledge_lancedb.py`). There is no external vector
database and no optional backend to install.

| Setting | `CODX_JUNIOR_KNOWLEDGE_BACKEND` | What runs |
| --- | --- | --- |
| **LanceDB** (`knowledge_lancedb.py`) — default | `lancedb` | embedded: on-disk, inside the API process, no service. Native BM25 + dense vectors fused with RRF |
| disabled | `none` | nothing; every method returns empty results |

The `Knowledge` coordinator lives in
`api/codx/junior/knowledge/knowledge_manager.py`; the selector is
`api/codx/junior/knowledge/knowledge_backend.py`.

Knowledge is built in: no extra container, no profile, no flag. It also needs an
**embeddings model** (`deepseek`, `openrouter` and `groq` do not provide one, so on
those providers search runs **lexical only**). If the embeddings stack fails to load,
the dense leg is switched off with one warning and the lexical leg keeps answering —
knowledge never prevents the API from booting.