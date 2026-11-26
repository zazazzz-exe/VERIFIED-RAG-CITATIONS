---
inclusion: always
---

# Tech stack and rules

## Stack
- Python 3.11+ (ingest, Merkle tree, RAG, verifier, CLI, web app)
- PyMuPDF for PDF text extraction
- Ollama for both the LLM and embeddings (runs on the user's Windows laptop)
  - Chat model: `llama3.1:8b` (configurable with `OLLAMA_MODEL`)
  - Embedding model: `nomic-embed-text` (configurable with `OLLAMA_EMBED_MODEL`)
- Chroma (`chromadb`) as the local vector database. Embeddings are computed by Ollama
  and passed to Chroma explicitly; do not rely on Chroma's default embedding function.
- web3.py for talking to the blockchain
- Streamlit for the web app
- pytest for Python tests
- Solidity `0.8.24`, Foundry, OpenZeppelin Contracts v5 (`MerkleProof`, `Ownable`)

## Development environment
- The user is on Windows. All development happens inside WSL 2 (Ubuntu), opened in
  VS Code with the WSL extension. Write shell commands for bash, not PowerShell.
- Ollama runs on Windows, not in WSL. Reach it through `OLLAMA_URL`
  (default `http://localhost:11434`, which works with WSL mirrored networking).

## Prismo Glassnet (public testnet)
- RPC: `https://rpc.glassnet.prismo.network`
- Chain ID: `101001000`
- Gas token: test USDC from https://faucet.glassnet.prismo.network
- Explorer (Blockscout): https://explorer.glassnet.prismo.network
- Node: cdk-erigon (Polygon CDK zk-rollup)

### Glassnet rules (verified against the live RPC)
- Glassnet accepts ONLY legacy (type-0) transactions. Always use `gasPrice`, never
  `maxFeePerGas`/`maxPriorityFeePerGas`. Use `--legacy` with `forge script` and `cast send`.
- PUSH0 is supported. Compile with `evm_version = "shanghai"` to avoid newer opcodes.
- Verify contracts with `forge verify-contract --verifier blockscout
  --verifier-url https://explorer.glassnet.prismo.network/api/`.

## Merkle tree format (must match on both sides)
Python builds trees and proofs. Solidity verifies them with OpenZeppelin
`MerkleProof.verifyCalldata`. Both sides MUST use exactly this format:
- Text normalization before hashing: Unicode NFC, collapse all whitespace runs to a
  single space, strip leading/trailing whitespace.
- `contentHash = keccak256(abi.encode(bytes32 salt, string normalizedText))`
  - Public passages: `salt = 0x00…00`
  - Private passages: random 32-byte salt per passage, stored only in private storage
    (stops people from confirming guessed text against public hashes)
- `leaf = keccak256(bytes.concat(keccak256(abi.encode(string docId, uint32 page, uint32 index, bytes32 contentHash))))`
  (double hash, same as OpenZeppelin's StandardMerkleTree)
- Internal node = keccak256 of the two children sorted ascending (commutative), the same
  as OpenZeppelin `Hashes.commutativeKeccak256`.
- Odd node at a level is promoted to the next level unchanged (no duplication).
- A cross-language test (Python-generated fixture checked by a Foundry test) must pass
  before any other Merkle-related work is considered done.

## Security rules
- Never hardcode or commit private keys. Read `PRIVATE_KEY` from `.env` (gitignored).
- Use a dedicated testnet-only wallet, never a main wallet.
- Verification must recompute the hash from the passage text shown to the user,
  never trust a stored hash or leaf.
- Private passage text and salts must never be sent to the chain, logged, or shown to
  unauthorized sessions.
- The app must refuse to run if the connected chain ID does not match the configured network.

## Commands
- Python setup: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
- Python tests: `pytest -q`
- Contract build/tests: `cd contracts && forge build && forge test -vv`
- Local chain: `anvil` (chain ID 31337)
- Web app: `streamlit run app/streamlit_app.py`
