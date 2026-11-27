# Requirements Document

## Introduction

Verified RAG Citations is a chatbot that answers questions about Philippine public
documents (starting with national budget documents) and attaches a checkable proof to
every citation. Document sets are fingerprinted into Merkle roots that are published on
Prismo Glassnet. The app verifies each citation against the on-chain root and shows
whether it is genuine. Private document sets keep their text in private storage while
their roots are still published on Prismo.

## Glossary

- **Corpus**: a named, versioned set of documents (for example "GAA 2026 v1").
- **Passage**: a paragraph-sized piece of a document, identified by docId, page, and index.
- **Content hash**: keccak256 fingerprint of a passage's normalized text and salt.
- **Leaf**: hash of a passage's ID fields and content hash; the bottom level of the Merkle tree.
- **Merkle root**: the single hash that represents every leaf in a corpus.
- **Merkle proof**: the sibling hashes needed to rebuild the root from one leaf.
- **CorpusRegistry**: the smart contract on Prismo that stores corpus roots and verifies proofs.
- **Authorized session**: an app session unlocked with the auditor token.
- **Citation receipt**: a JSON object (corpus ID, passage ID fields, content hash, proof)
  that lets anyone check a citation exists in a corpus without seeing its text.

## Requirements

### Requirement 1: Document ingestion

**User Story:** As a publisher, I want to turn PDF and text documents into passages, so that
they can be fingerprinted and searched.

#### Acceptance Criteria

1. WHEN the publisher runs the ingest command on a folder of PDF or TXT files THE SYSTEM SHALL
   extract the text page by page and split it into passages of roughly one paragraph
   (target 300–1,200 characters).
2. THE SYSTEM SHALL give every passage a stable ID made of docId (file name without extension),
   page number (1-based), and index within the page (0-based).
3. WHEN the same files are ingested twice THE SYSTEM SHALL produce identical passages, IDs,
   and Merkle root.
4. THE SYSTEM SHALL normalize passage text (Unicode NFC, collapsed whitespace, trimmed) before
   hashing and SHALL store the normalized text.
5. IF a file cannot be read or contains no extractable text THEN THE SYSTEM SHALL report the
   file name and skip it without stopping the whole ingest.

### Requirement 2: Merkle commitment

**User Story:** As a publisher, I want one fingerprint that represents every passage, so that
publishing it on Prismo locks the whole corpus.

#### Acceptance Criteria

1. THE SYSTEM SHALL compute content hashes, leaves, internal nodes, and proofs exactly as
   specified in the steering file `tech.md` (Merkle tree format).
2. WHEN a corpus is marked private THE SYSTEM SHALL generate a random 32-byte salt per passage
   and store it only in private storage.
3. THE SYSTEM SHALL produce a Merkle proof for any passage in a corpus.
4. THE SYSTEM SHALL include a cross-language test where proofs generated in Python are accepted
   by the Solidity contract, and modified proofs or leaves are rejected.

### Requirement 3: On-chain corpus registry

**User Story:** As a citizen, I want corpus roots stored on Prismo, so that nobody can secretly
change the documents after publishing.

#### Acceptance Criteria

1. THE CorpusRegistry SHALL let only its owner publish a corpus with a root, name, version,
   private flag, and passage count, and SHALL return a new numeric corpus ID.
2. WHEN a corpus is published THE CorpusRegistry SHALL emit a `CorpusPublished` event with the
   corpus ID, root, name, version, and private flag.
3. THE CorpusRegistry SHALL never allow a published root to be changed or deleted. A corrected
   document set is published as a new version with a new corpus ID.
4. THE CorpusRegistry SHALL provide a read-only `verifyPassage` function that takes a corpus ID,
   proof, docId, page, index, and content hash, computes the leaf itself, and returns true only
   if the proof matches the stored root.
5. IF `verifyPassage` is called with an unknown corpus ID THEN THE CorpusRegistry SHALL return false.
6. IF a non-owner calls `publish` THEN THE CorpusRegistry SHALL revert.

### Requirement 4: Network safety and secrets

**User Story:** As a developer, I want the app to connect only to the intended chain, so that
results are never checked against the wrong network.

#### Acceptance Criteria

1. THE SYSTEM SHALL read the network (`local` or `glassnet`), RPC URL, contract address, and
   private key from environment variables loaded from `.env`.
2. WHEN the app or CLI starts THE SYSTEM SHALL compare the RPC's chain ID with the expected one
   (31337 for local, 101001000 for glassnet) and SHALL stop with a clear error if they differ.
3. THE SYSTEM SHALL send only legacy (type-0) transactions with an explicit `gasPrice`.
4. THE SYSTEM SHALL never write private keys to logs, files other than `.env`, or the repository.

### Requirement 5: Retrieval and answer generation

**User Story:** As a citizen, I want to ask questions in plain language and get answers based
only on the official documents.

#### Acceptance Criteria

1. WHEN the user asks a question THE SYSTEM SHALL embed it with the local embedding model and
   retrieve the top K (default 5) most similar passages from Chroma.
2. THE SYSTEM SHALL give the LLM the question and the retrieved passages labeled `[P1]`…`[PK]`
   and instruct it to answer only from them and to cite labels for every claim.
3. IF no retrieved passage is relevant enough (similarity below a configurable threshold) THEN
   THE SYSTEM SHALL say it could not find the answer in the documents instead of calling the LLM.
4. WHEN the session is not authorized THE SYSTEM SHALL search only public corpora.
5. WHEN the session is authorized THE SYSTEM SHALL search public and private corpora.
6. IF the selected LLM provider is unreachable or not configured THEN THE SYSTEM SHALL show a
   clear error naming the provider and the setting to fix (`OLLAMA_URL`, or `ANTHROPIC_API_KEY` /
   `ANTHROPIC_MODEL`), without ever showing the API key.
7. WHEN every passage given to the LLM is public THE SYSTEM SHALL answer with the configured
   `LLM_PROVIDER` (default Claude). WHEN any passage is private THE SYSTEM SHALL answer with
   `PRIVATE_LLM_PROVIDER` (default Ollama) and SHALL NEVER send private passage text to the
   Anthropic API.
8. THE SYSTEM SHALL let the user force a provider for public-only questions to compare them,
   SHALL refuse to force Claude for a question that includes private passages, and SHALL show
   which provider and model produced each answer.

### Requirement 6: Citation verification

**User Story:** As a citizen, I want each citation marked as verified or not, so that I can
trust real sources and spot invented ones.

#### Acceptance Criteria

1. FOR every citation label in the answer THE SYSTEM SHALL recompute the content hash from the
   passage text that will be displayed, then call `verifyPassage` on Prismo with the proof.
2. WHEN `verifyPassage` returns true THE SYSTEM SHALL mark the citation ✅ Verified.
3. WHEN `verifyPassage` returns false THE SYSTEM SHALL mark the citation ❌ Not verified.
4. IF the answer cites a label that was not among the retrieved passages THEN THE SYSTEM SHALL
   mark it ❌ Unknown source.
5. WHEN the answer contains text in double quotes attributed to a citation THE SYSTEM SHALL check
   that the quoted text appears in the cited passage (after normalization) and SHALL mark it
   ⚠️ Quote not found if it does not.
6. IF the chain cannot be reached THEN THE SYSTEM SHALL mark citations ⚪ Unable to verify,
   never ✅.

### Requirement 7: Private corpora

**User Story:** As an auditor, I want to cite private documents with proof, so that internal
records can be checked without making them public.

#### Acceptance Criteria

1. WHEN a corpus is ingested as private THE SYSTEM SHALL store its passage text and salts in a
   local SQLite database (`out/private.db`) and SHALL NOT write them to public corpus files.
2. THE SYSTEM SHALL unlock an authorized session only when the user enters a token matching
   `AUDITOR_TOKEN` (compared in constant time).
3. WHEN an authorized user views a private citation THE SYSTEM SHALL show the text and verify it
   the same way as public citations.
4. THE SYSTEM SHALL let an authorized user export a citation receipt for any private citation.
5. WHEN any user pastes a citation receipt THE SYSTEM SHALL verify it on Prismo and show the corpus
   name, version, and result, without needing or showing the passage text.

### Requirement 8: Web app

**User Story:** As a citizen, I want a simple web page to ask questions and inspect citations.

#### Acceptance Criteria

1. THE app SHALL provide a chat box, the answer, and a list of citations with their status badges.
2. WHEN the user opens a citation THE app SHALL show the passage text (if allowed), passage ID,
   corpus name and version, and a link to the CorpusRegistry contract on the Glassnet explorer.
3. THE app SHALL show the connected network and chain ID.
4. THE app SHALL provide a "tamper demo" toggle that changes one character in a displayed
   passage before verification, so the ❌ result can be demonstrated.
5. THE app SHALL provide a citation receipt verifier (Requirement 7.5).

### Requirement 9: Deployment on Prismo Glassnet

**User Story:** As a developer, I want the contract live on Prismo, so that the project really
runs on Prismo technology.

#### Acceptance Criteria

1. THE project SHALL include a `python -m vrag deploy` command that deploys CorpusRegistry to
   Glassnet using legacy transactions and the Hardhat production build artifact.
2. THE project SHALL document how to verify the contract source on the Glassnet Blockscout explorer.
3. WHEN deployed THE README SHALL list the contract address and an explorer link.
4. THE end-to-end demo (ask → ✅, tamper → ❌, unknown source → ❌, private receipt → ✅) SHALL
   work against Glassnet.
