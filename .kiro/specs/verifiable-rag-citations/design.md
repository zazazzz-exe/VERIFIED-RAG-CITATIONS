# Design Document

## Overview

The system has two phases:

1. **Publish (once per corpus version):** documents → passages → content hashes → Merkle tree →
   root published to `CorpusRegistry` on Prismo Glassnet. Passages are also embedded and indexed
   in Chroma for search.
2. **Ask (every question):** question → search → LLM answer with `[P#]` labels → each citation is
   re-hashed from its displayed text and checked on-chain with its Merkle proof → UI shows status.

Only roots and metadata go on-chain. Passage text, embeddings, salts, and trees stay local.

## Architecture

```mermaid
flowchart LR
    subgraph Local["Windows laptop"]
        Docs[PDF / TXT] --> Ingest[vrag.ingest]
        Ingest --> Merkle[vrag.merkle]
        Merkle --> Store[(out/corpora/*.json<br/>out/private.db)]
        Ingest --> Index[vrag.index]
        Index <--> Embed[(local embedding model<br/>all-MiniLM-L6-v2)]
        Index --> Chroma[(out/chroma)]
        App[Streamlit app] --> RAG[vrag.rag]
        RAG --> Index
        RAG -- private passages --> Ollama[(Ollama<br/>gemma3:4b)]
        RAG --> Verify[vrag.verify]
        Verify --> Store
    end
    RAG -- public passages only --> Claude[Anthropic Claude API]
    subgraph Prismo["Prismo Glassnet (chain 101001000)"]
        Registry[CorpusRegistry]
    end
    Merkle -- publish root --> Chain[vrag.chain]
    Verify -- verifyPassage eth_call --> Chain
    Chain --> Registry
```

### Publish sequence

```mermaid
sequenceDiagram
    participant P as Publisher (CLI)
    participant I as ingest + merkle
    participant S as store
    participant X as index (Chroma)
    participant C as CorpusRegistry
    P->>I: python -m vrag ingest --slug gaa-2026 data/public/
    I->>I: extract, split, normalize, hash, build tree
    I->>S: save corpus file (+ private.db if private)
    P->>C: python -m vrag publish --slug gaa-2026 (legacy tx)
    C-->>P: corpusId (from CorpusPublished event)
    P->>S: record corpusId in corpus file
    P->>X: python -m vrag index --slug gaa-2026
```

### Ask sequence

```mermaid
sequenceDiagram
    participant U as User
    participant A as Streamlit app
    participant R as rag
    participant O as LLM (Claude if all passages public, Ollama if any private)
    participant V as verify
    participant C as CorpusRegistry
    U->>A: question
    A->>R: ask(question, authorized, provider=None)
    R->>R: embed question locally
    R->>R: Chroma top-K (public only unless authorized)
    R->>R: choose provider (private passage present -> PRIVATE_LLM_PROVIDER)
    R->>O: chat(prompt with [P1]..[PK])
    O-->>R: answer citing [P#]
    R->>V: citations
    V->>V: recompute contentHash from displayed text
    V->>C: verifyPassage(corpusId, proof, docId, page, index, contentHash)
    C-->>V: true / false
    V-->>A: statuses ✅ ❌ ⚠️ ⚪
    A-->>U: answer + badges + passages
```

## Components and Interfaces

### `vrag/config.py`
Loads `.env` with python-dotenv into a frozen dataclass.

| Variable | Default | Notes |
|---|---|---|
| `NETWORK` | `local` | `local` or `glassnet` |
| `RPC_URL` | `http://127.0.0.1:8545` | Glassnet: `https://rpc.glassnet.prismo.network` |
| `REGISTRY_ADDRESS` | (required for publish/verify) | Deployed CorpusRegistry |
| `PRIVATE_KEY` | (required for publish only) | Testnet-only wallet |
| `LLM_PROVIDER` | `anthropic` | Answers questions with only public passages (`anthropic` or `ollama`) |
| `PRIVATE_LLM_PROVIDER` | `ollama` | Answers questions that include private passages. `anthropic` is rejected at startup |
| `ANTHROPIC_API_KEY` | (required when Claude is used) | `.env` only, never logged |
| `ANTHROPIC_MODEL` | (required when Claude is used) | Claude model ID, confirmed with a test call |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama on the same laptop |
| `OLLAMA_MODEL` | `gemma3:4b` | `llama3.1:8b` also works |
| `TOP_K` | `5` | |
| `MIN_SIMILARITY` | `0.35` | Cosine similarity cut-off; tune on real data |
| `AUDITOR_TOKEN` | (empty = private features disabled) | |
| `EXPLORER_URL` | `https://explorer.glassnet.prismo.network` | |

`EXPECTED_CHAIN_IDS = {"local": 31337, "glassnet": 101001000}`.

### `vrag/text.py`
- `normalize(text: str) -> str` — NFC, collapse whitespace, strip.
- `content_hash(text: str, salt: bytes = ZERO32) -> bytes` —
  `keccak(abi_encode(["bytes32", "string"], [salt, normalize(text)]))`.

### `vrag/merkle.py`
- `leaf_hash(doc_id: str, page: int, index: int, content_hash: bytes) -> bytes` —
  `keccak(keccak(abi_encode(["string","uint32","uint32","bytes32"], [...])))`.
- `hash_pair(a, b) -> bytes` — `keccak(min(a,b) + max(a,b))`.
- `build_tree(leaves: list[bytes]) -> list[list[bytes]]` — levels from leaves to root, leaves
  kept in passage order; an odd last node is promoted unchanged.
- `root(levels) -> bytes`, `proof(levels, leaf_index) -> list[bytes]`,
  `verify(proof, root, leaf) -> bool` (mirror of OpenZeppelin, used in tests).
- Empty corpus is an error. A single-leaf tree has `root == leaf` and an empty proof.

### `vrag/ingest.py`
- `extract_pages(path) -> list[str]` — PyMuPDF for PDF (`page.get_text()`), whole file as page 1 for TXT.
- `split_passages(page_text) -> list[str]` — split on blank lines, then merge short paragraphs and
  split long ones on sentence boundaries to stay within 300–1,200 characters.
- `ingest_folder(folder, slug, name, version, private) -> Corpus` — deterministic: files sorted by
  name, passages in reading order.

### `vrag/store.py`
- Public corpus file `out/corpora/<slug>.json`:
  ```json
  {
    "slug": "gaa-2026", "name": "GAA 2026", "version": "v1", "private": false,
    "corpusId": 0, "root": "0x…", "registry": "0x…", "chainId": 101001000,
    "passages": [
      {"docId": "gaa-2026-vol1", "page": 12, "index": 0,
       "text": "…", "contentHash": "0x…", "leafIndex": 0}
    ],
    "levels": [["0x…", "…"], ["…"], ["0x<root>"]]
  }
  ```
- Private corpora use the same file layout but `text` is omitted and `salt` is never present.
  Text and salt live in `out/private.db`:
  `passages(slug TEXT, doc_id TEXT, page INT, idx INT, text TEXT, salt BLOB, PRIMARY KEY(slug, doc_id, page, idx))`.
- `load_corpus(slug)`, `get_passage(slug, doc_id, page, index, authorized) -> Passage | None`.

### `vrag/index.py`
- `embed(texts) -> list[list[float]]` with Chroma's local `all-MiniLM-L6-v2` model (ONNX,
  no server or key), called explicitly so the model is pinned.
- One Chroma collection `passages` in `out/chroma`, cosine distance, IDs
  `<slug>:<docId>:p<page>:<index>`, metadata `{slug, private, docId, page, index}`.
- Private passages are indexed (the index is local, server-side) but filtered out with
  `where={"private": False}` for unauthorized searches.
- `search(question, k, authorized) -> list[(passage_ref, similarity)]`.

### `vrag/chain.py`
- `connect(config) -> Web3` — checks `w3.eth.chain_id` against `EXPECTED_CHAIN_IDS[NETWORK]`,
  raises `WrongChainError` on mismatch.
- `send_legacy_tx(w3, tx) -> receipt` — one shared helper that sets `type=0`,
  `gasPrice=w3.eth.gas_price`, `nonce`, `chainId`, estimated `gas` (+20% margin), signs with
  `PRIVATE_KEY`, sends, and waits for the receipt. Used by both `deploy` and `publish`.
- `deploy() -> str` — deploys `CorpusRegistry(owner = deployer address)` from the Hardhat
  production artifact, prints the address and explorer link.
- `publish(corpus) -> int` — calls `publish(...)` through `send_legacy_tx` and reads `corpusId`
  from the `CorpusPublished` event.
- `verify_passage(corpus_id, proof, doc_id, page, index, content_hash) -> bool` — `eth_call`.
- `get_corpus(corpus_id)`.
- ABI and bytecode loaded from
  `blockchain/artifacts/contracts/CorpusRegistry.sol/CorpusRegistry.json`
  (Hardhat build output; build with `--build-profile production` so the deployed bytecode matches
  what `hardhat verify` expects).

### `vrag/rag.py`
- `ask(question, authorized, provider=None) -> Answer` — search; if best similarity <
  `MIN_SIMILARITY`, return a "not found in the documents" answer with no LLM call. Otherwise
  choose the provider: `PRIVATE_LLM_PROVIDER` if any retrieved passage is private, else
  `LLM_PROVIDER`; an explicit `provider` overrides this only for public-only questions, and
  `anthropic` with a private passage raises an error before any network call. Build the prompt,
  call the provider (Claude Messages API with no sampling parameters, or Ollama `/api/chat` at
  `temperature=0`), and parse
  labels with regex `\[P(\d+)\]`.
- Prompt rules: answer only from the passages; cite `[P#]` after each claim; put exact quotes in
  double quotes; say "The documents do not say" when unsure.
- `Answer = {text, citations: [Citation], passages: {label: PassageRef}, provider, model}`.

### `vrag/verify.py`
- `check_citation(label, answer, passages, displayed_text, authorized) -> CitationResult`
  1. Label not in `passages` → ❌ Unknown source.
  2. Recompute `content_hash(displayed_text, salt)` (salt from private.db for private passages,
     zero for public). Recompute the leaf with `merkle.leaf_hash`.
  3. Get the proof from the stored levels using `leafIndex`.
  4. `chain.verify_passage(...)` → ✅ / ❌. Network error → ⚪ Unable to verify.
  5. Quote check: every `"…"` that is followed by this label must be a substring of
     `normalize(displayed_text)`; otherwise add ⚠️ Quote not found.
- `make_receipt(citation) -> dict` and `verify_receipt(receipt) -> ReceiptResult`.
  Receipt JSON: `{corpusId, docId, page, index, contentHash, proof, registry, chainId}`.
  `verify_receipt` refuses receipts whose `chainId` or `registry` differ from the config.

### `vrag/cli.py` (`python -m vrag …`)
| Command | Purpose |
|---|---|
| `deploy` | Deploy CorpusRegistry, print address for `.env` |
| `ingest <folder> --slug --name --version [--private]` | Build corpus file (+ private.db) |
| `publish --slug` | Publish root, save corpusId |
| `index --slug` | Embed and add to Chroma |
| `ask "<question>" [--auditor]` | Ask from the terminal, print statuses |
| `verify-receipt <file.json>` | Check a receipt |
| `fixtures` | Write `blockchain/test/fixtures/merkle.json` |
| `check` | Print network, chain ID, registry, LLM providers (key set, Ollama reachable) |

### `app/streamlit_app.py`
- Sidebar: network + chain ID, registry link to explorer, auditor token field, tamper demo toggle,
  receipt verifier (paste JSON).
- Main: chat input, answer, citation list with badges; expander per citation with passage text,
  passage ID, corpus name/version, "Export receipt" button (authorized + private only).
- Tamper toggle replaces the first digit (or first character) of the displayed passage before
  verification and labels the passage "(modified for demo)".

### `blockchain/contracts/CorpusRegistry.sol`

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {MerkleProof} from "@openzeppelin/contracts/utils/cryptography/MerkleProof.sol";

contract CorpusRegistry is Ownable {
    struct Corpus {
        bytes32 root;
        string name;
        string version;
        bool isPrivate;
        uint32 passageCount;
        uint64 publishedAt;
    }

    Corpus[] private _corpora;

    event CorpusPublished(uint256 indexed corpusId, bytes32 root, string name, string version, bool isPrivate);

    constructor(address initialOwner) Ownable(initialOwner) {}

    function publish(bytes32 root, string calldata name, string calldata version, bool isPrivate, uint32 passageCount)
        external onlyOwner returns (uint256 corpusId);           // require root != 0, passageCount > 0

    function corpusCount() external view returns (uint256);
    function getCorpus(uint256 corpusId) external view returns (Corpus memory);

    function leafOf(string calldata docId, uint32 page, uint32 index, bytes32 contentHash)
        public pure returns (bytes32);                           // double keccak of abi.encode

    function verifyPassage(uint256 corpusId, bytes32[] calldata proof, string calldata docId,
        uint32 page, uint32 index, bytes32 contentHash) external view returns (bool);
        // false if corpusId >= length; else MerkleProof.verifyCalldata(proof, root, leafOf(...))
}
```

### `blockchain/hardhat.config.ts` (Hardhat 3, ESM)

Created with `npx hardhat --init` → "A TypeScript Hardhat project using Mocha and Ethers.js",
then trimmed to this project. Intended shape (check option names against the installed
Hardhat 3 version):

```ts
import { defineConfig } from "hardhat/config";
import hardhatToolboxMochaEthers from "@nomicfoundation/hardhat-toolbox-mocha-ethers";

const solc = { version: "0.8.24", settings: { evmVersion: "shanghai" } };

export default defineConfig({
  plugins: [hardhatToolboxMochaEthers],          // includes hardhat-verify
  solidity: {
    profiles: {
      default: solc,
      production: { ...solc, settings: { ...solc.settings, optimizer: { enabled: true, runs: 200 } } },
    },
  },
  networks: {
    glassnet: { type: "http", chainType: "generic", url: "https://rpc.glassnet.prismo.network" },
  },
  chainDescriptors: {
    101001000: {
      name: "Prismo Glassnet",
      blockExplorers: {
        blockscout: {
          name: "Glassnet Explorer",
          url: "https://explorer.glassnet.prismo.network",
          apiUrl: "https://explorer.glassnet.prismo.network/api",
        },
      },
    },
  },
});
```

- The `glassnet` network has no `accounts`: Hardhat never signs transactions in this project.
  It is only needed for `hardhat verify`.
- OpenZeppelin is installed with `npm install @openzeppelin/contracts@^5`.
- Local chain: `npx hardhat node` (JSON-RPC at `http://127.0.0.1:8545`, chain ID 31337, with
  funded test accounts printed at startup; use account #0's key in `.env` for local work).
- Verify after deploying to Glassnet:
  `npx hardhat verify blockscout --network glassnet <REGISTRY_ADDRESS> <OWNER_ADDRESS>`.

## Data Models

```python
@dataclass(frozen=True)
class Passage:
    slug: str; doc_id: str; page: int; index: int
    text: str | None        # None for private passages in unauthorized sessions
    content_hash: bytes; leaf_index: int; private: bool

@dataclass
class CitationResult:
    label: str; passage: Passage | None
    status: Literal["verified", "not_verified", "unknown_source", "unable"]
    quote_warnings: list[str]
```

## Error Handling

| Situation | Behavior |
|---|---|
| Wrong chain ID | Stop at startup with expected vs actual chain ID |
| RPC unreachable during verify | ⚪ Unable to verify (never ✅) |
| LLM provider unreachable / not configured | Error naming the provider and the setting to fix (`OLLAMA_URL` + `ollama pull`, or `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL`); the key is never shown |
| Private passage with `anthropic` forced | Refuse before any network call |
| Corpus not published yet (`corpusId` missing) | Skip it in search and warn |
| Unreadable PDF | Skip file, list it at the end of ingest |
| Publish tx fails / insufficient USDC | Show the error and the faucet URL |
| Receipt for another chain/registry | Reject with a clear reason |

## Testing Strategy

- **Cross-language Merkle test (critical):** `python -m vrag fixtures` writes sample passages,
  content hashes, leaves, root, and proofs to `blockchain/test/fixtures/merkle.json`. A Hardhat
  TypeScript test reads the file, deploys the contract on Hardhat's simulated network, publishes
  the root, and asserts that `leafOf` matches every Python leaf, every proof verifies, and a
  changed leaf, changed proof element, or wrong corpus ID fails.
- **Python unit tests:** normalization, content hash with/without salt, tree with 1, 2, 3, and
  many leaves, proof round-trips, deterministic ingest of `data/samples/`, citation parsing,
  quote check, receipt rejection for wrong chain.
- **Contract tests:** owner-only publish, event fields, immutability (no setter), unknown ID returns false.
- **Manual end-to-end on Glassnet:** the four demo cases in Requirement 9.4.
