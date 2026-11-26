# Implementation Plan

All commands run in PowerShell on Windows (see `.kiro/steering/tech.md`).

- [ ] 1. Set up the project skeleton
  - Create `requirements.txt` (pymupdf, chromadb, web3, python-dotenv, requests, streamlit, pytest)
  - Create the `vrag/` package with empty modules from `structure.md`, plus `vrag/__main__.py`
  - Implement `vrag/config.py` reading `.env` with the defaults in the design
  - Add `data/samples/` with two small public sample TXT files and one clearly-labelled
    synthetic private memo (fictional content only)
  - Confirm `python -m venv .venv`, `.venv\Scripts\Activate.ps1`, and `pip install -r requirements.txt` work
  - _Requirements: 4.1_

- [ ] 2. Text normalization and Merkle tree in Python
  - [ ] 2.1 Implement `vrag/text.py` (`normalize`, `content_hash`)
    - _Requirements: 1.4, 2.1, 2.2_
  - [ ] 2.2 Implement `vrag/merkle.py` (`leaf_hash`, `hash_pair`, `build_tree`, `root`, `proof`, `verify`)
    - _Requirements: 2.1, 2.3_
  - [ ] 2.3 Write pytest tests for normalization, salted/unsalted hashes, and trees with 1, 2, 3, and 10 leaves
    - _Requirements: 2.1, 2.3_

- [ ] 3. CorpusRegistry contract
  - [ ] 3.1 Create the Hardhat 3 project in `blockchain\` (`npx hardhat --init`, TypeScript +
    Mocha + Ethers.js), install `@openzeppelin/contracts@^5`, remove the template's sample
    contract/tests/Ignition module, and write `hardhat.config.ts` as in the design
    (solc 0.8.24, `evmVersion: "shanghai"`, production profile, `glassnet` network without
    accounts, `chainDescriptors` for chain 101001000)
    - _Requirements: 9.1, 9.2_
  - [ ] 3.2 Implement `blockchain\contracts\CorpusRegistry.sol` as specified in the design
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_
  - [ ] 3.3 Write `test\CorpusRegistry.test.ts`: owner-only publish, event fields, unknown corpus ID
    returns false, zero root and zero passage count rejected
    - _Requirements: 3.1, 3.2, 3.5, 3.6_

- [ ] 4. Cross-language Merkle check (must pass before continuing)
  - [ ] 4.1 Add `python -m vrag fixtures` that writes `blockchain\test\fixtures\merkle.json`
    (passages, content hashes, leaves, root, proofs)
    - _Requirements: 2.4_
  - [ ] 4.2 Add `test\MerkleFixture.test.ts` that loads the fixture, deploys the contract, publishes
    the root, checks every `leafOf` value and proof, and checks that tampered leaves, proofs, and
    corpus IDs fail. Run with `npx hardhat test`.
    - _Requirements: 2.4, 3.4_

- [ ] 5. Document ingestion and storage
  - [ ] 5.1 Implement `vrag/ingest.py` (PDF/TXT extraction, passage splitting, deterministic ordering)
    - _Requirements: 1.1, 1.2, 1.3, 1.5_
  - [ ] 5.2 Implement `vrag/store.py` (public corpus JSON, private SQLite with salts, passage lookup
    that hides private text from unauthorized callers)
    - _Requirements: 2.2, 7.1_
  - [ ] 5.3 Add the `ingest` CLI command and a test that ingesting `data/samples/` twice gives the same root
    - _Requirements: 1.3_

- [ ] 6. Blockchain connection, deploy, and publish (from Python)
  - [ ] 6.1 Implement `vrag/chain.py` (`connect` with chain ID check, shared `send_legacy_tx`,
    `deploy`, `publish`, `verify_passage`, `get_corpus`) using the Hardhat production artifact
    - _Requirements: 3.1, 4.2, 4.3, 4.4, 9.1_
  - [ ] 6.2 Add the `deploy`, `publish`, and `check` CLI commands. Start `npx hardhat node` in a
    second terminal, deploy locally, and publish the sample corpora
    - _Requirements: 4.2, 9.1_

- [ ] 7. Checkpoint: run `pytest -q` and `npx hardhat test` (in `blockchain\`), and confirm the sample
  corpora are published on the local Hardhat node and `check` reports chain ID 31337. Ask the user
  before continuing if anything fails.

- [ ] 8. Search index with Ollama and Chroma
  - [ ] 8.1 Implement `vrag/index.py` (Ollama embeddings, Chroma collection, public-only filter)
    - _Requirements: 5.1, 5.4, 5.5, 5.6_
  - [ ] 8.2 Add the `index` CLI command and index the sample corpora
    - _Requirements: 5.1_

- [ ] 9. RAG answers and citation verification
  - [ ] 9.1 Implement `vrag/rag.py` (similarity threshold, prompt, Ollama chat at temperature 0,
    `[P#]` parsing)
    - _Requirements: 5.2, 5.3, 5.6_
  - [ ] 9.2 Implement `vrag/verify.py` (recompute hash from displayed text, on-chain check, unknown
    source, quote check, unable-to-verify, receipts)
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 7.4, 7.5_
  - [ ] 9.3 Write tests for citation parsing, the quote check, and receipt rejection for a wrong
    chain ID or registry
    - _Requirements: 6.4, 6.5, 7.5_
  - [ ] 9.4 Add the `ask` and `verify-receipt` CLI commands
    - _Requirements: 5.1, 7.5_

- [ ] 10. Streamlit web app
  - [ ] 10.1 Build the chat page with answer, citation badges, and passage expanders with explorer links
    - _Requirements: 8.1, 8.2, 8.3_
  - [ ] 10.2 Add the auditor token unlock (constant-time compare), private search, and receipt export
    - _Requirements: 5.5, 7.2, 7.3, 7.4_
  - [ ] 10.3 Add the tamper demo toggle and the receipt verifier panel
    - _Requirements: 8.4, 8.5, 7.5_

- [ ] 11. Checkpoint: run the four demo cases on the local Hardhat node (ask → ✅, tamper → ❌,
  unknown source → ❌, private receipt → ✅). Ask the user before continuing if anything fails.

- [ ] 12. Deploy to Prismo Glassnet
  - [ ] 12.1 Switch `.env` to Glassnet, run `python -m vrag deploy`, verify the source with
    `npx hardhat verify blockscout --network glassnet <address> <owner>`, and record the address
    in the README
    - _Requirements: 9.1, 9.2, 9.3_
  - [ ] 12.2 Publish the sample corpora (and the user's budget PDFs, if added) to Glassnet and
    re-run the four demo cases
    - _Requirements: 9.4_
  - [ ] 12.3 Update the README with the demo steps, explorer links, and known limitations
    - _Requirements: 9.3_
