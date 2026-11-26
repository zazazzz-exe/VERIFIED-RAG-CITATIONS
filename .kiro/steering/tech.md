---
inclusion: always
---

# Tech stack and rules

## Stack
- Python 3.12 (ingest, Merkle tree, RAG, verifier, deploy/publish CLI, web app)
- PyMuPDF for PDF text extraction
- Ollama for both the LLM and embeddings (installed on the user's Windows laptop)
  - Chat model: `llama3.1:8b` (configurable with `OLLAMA_MODEL`)
  - Embedding model: `nomic-embed-text` (configurable with `OLLAMA_EMBED_MODEL`)
- Chroma (`chromadb`) as the local vector database. Embeddings are computed by Ollama
  and passed to Chroma explicitly; do not rely on Chroma's default embedding function.
- web3.py for all blockchain transactions (deploy, publish) and reads (verify)
- Streamlit for the web app
- pytest for Python tests
- Solidity `0.8.24`, Hardhat 3 (TypeScript, Mocha + ethers toolbox), OpenZeppelin Contracts v5
  (`MerkleProof`, `Ownable`). Hardhat is used to compile, test, run the local chain, and
  verify the contract on the explorer. Deploying and publishing are done from Python.

## Development environment: Windows only
- The user is on Windows 11 and uses VS Code. Everything runs natively on Windows.
  Do NOT use WSL, bash-only syntax, or Linux-only tools.
- Write terminal commands for PowerShell (the VS Code default terminal on Windows).
  - Activate the venv with `.venv\Scripts\Activate.ps1`
  - Use `python` (or `py -3.12`), not `python3`
  - Use backslashes in commands that take Windows paths; use `pathlib` in Python code
- Node.js 22 LTS or newer is required by Hardhat 3.
- Ollama runs on the same machine at `http://localhost:11434` (`OLLAMA_URL`).
- Open files with `encoding="utf-8"` explicitly in Python (Windows defaults differ).

## Prismo Glassnet (public testnet)
- RPC: `https://rpc.glassnet.prismo.network`
- Chain ID: `101001000`
- Gas token: test USDC from https://faucet.glassnet.prismo.network
- Explorer (Blockscout): https://explorer.glassnet.prismo.network
- Node: cdk-erigon (Polygon CDK zk-rollup)

### Glassnet rules (verified against the live RPC)
- Glassnet accepts ONLY legacy (type-0) transactions. Blocks have no `baseFeePerGas`, and
  `eth_maxPriorityFeePerGas` / `eth_feeHistory` return errors. In web3.py always build
  transactions with `"type": 0` and `"gasPrice": w3.eth.gas_price`; never set
  `maxFeePerGas` or `maxPriorityFeePerGas`.
- PUSH0 is supported. Compile with `evmVersion: "shanghai"` to avoid newer opcodes.
- Verify contracts with `npx hardhat verify blockscout --network glassnet <address> <owner>`,
  using a `chainDescriptors` entry for chain 101001000 that points at the Glassnet explorer.

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
- A cross-language test (Python-generated fixture checked by a Hardhat test) must pass
  before any other Merkle-related work is considered done.

## Security rules
- Never hardcode or commit private keys. Read `PRIVATE_KEY` from `.env` (gitignored).
- Use a dedicated testnet-only wallet, never a main wallet.
- Verification must recompute the hash from the passage text shown to the user,
  never trust a stored hash or leaf.
- Private passage text and salts must never be sent to the chain, logged, or shown to
  unauthorized sessions.
- The app must refuse to run if the connected chain ID does not match the configured network.

## Commands (PowerShell, from the repo root unless noted)
- Python setup: `python -m venv .venv; .venv\Scripts\Activate.ps1; pip install -r requirements.txt`
- Python tests: `pytest -q`
- Contract setup (in `blockchain\`): `npm install`
- Contract build (in `blockchain\`): `npx hardhat build --build-profile production`
- Contract tests (in `blockchain\`): `npx hardhat test`
- Local chain (in `blockchain\`, separate terminal): `npx hardhat node` (chain ID 31337)
- Deploy: `python -m vrag deploy`
- Web app: `streamlit run app\streamlit_app.py`
