# Verified RAG Citations

A chatbot for Philippine public documents (starting with the national budget) where every
citation is checked against tamper-proof records on [Prismo](https://prismo.technology/).

- ✅ The cited passage is word-for-word part of the official document set published on Prismo.
- ❌ The passage was edited, invented, or isn't from that document set.

Private document sets keep their text in private storage. Only their fingerprint (Merkle root)
goes on Prismo, so authorized users can cite them with proof.

> Status: working demo, deployed on Prismo Glassnet (testnet) with sample documents.

## How it works

1. Documents are split into passages, and each passage is hashed (keccak256). Private passages
   get a random 32-byte salt first, so nobody can confirm a guessed text against its hash.
2. The hashes are combined into one Merkle root, which is published to the `CorpusRegistry`
   contract on Prismo Glassnet. Only the root and metadata go on-chain.
3. A question is answered by an LLM using only retrieved passages, cited as `[P1]`, `[P2]`, …
   Claude (Anthropic API) answers from public passages. Any question that touches a private
   passage is answered by a local model (Ollama) instead, so private text never leaves the laptop.
4. For each citation, the app re-hashes the passage text it shows you and asks the contract to
   check the Merkle proof against the published root.

| Badge | Meaning |
|---|---|
| ✅ Verified | The displayed text matches the root published on Prismo |
| ❌ Not verified | The displayed text does not match (edited or wrong source) |
| ❌ Unknown source | The answer cites a label that was not among the retrieved passages |
| ⚠️ Quote not found | A quoted phrase is not in the cited passage (shown next to the badge) |
| ⚪ Unable to verify | The chain could not be reached. Never shown as ✅ |

## Live deployment on Prismo Glassnet

| | |
|---|---|
| RPC | `https://rpc.glassnet.prismo.network` |
| Chain ID | `101001000` |
| Gas token | test USDC ([faucet](https://faucet.glassnet.prismo.network)) |
| Explorer | https://explorer.glassnet.prismo.network |
| Contract | `CorpusRegistry` at [`0x5a134Ed6Ef85889566F6b77d206D610EF1A5815b`](https://explorer.glassnet.prismo.network/address/0x5a134Ed6Ef85889566F6b77d206D610EF1A5815b) |
| Verified source | [Code tab on the explorer](https://explorer.glassnet.prismo.network/address/0x5a134Ed6Ef85889566F6b77d206D610EF1A5815b#code) |
| Owner (publisher) | `0xD3D260571718e28e2978B0521ED5190cfa352D09` |

Published corpora:

| Corpus ID | Name | Passages | Type | Publish transaction |
|---|---|---|---|---|
| 0 | Sample public docs (v1) | 6 | public | [0xab84…ec56](https://explorer.glassnet.prismo.network/tx/0xab8492e1ed3202269390e0f3c1252317cb4d59512733e73aa176dfe626bcec56) |
| 1 | Sample private memo (synthetic) (v1) | 3 | private | [0xea0c…f0d1](https://explorer.glassnet.prismo.network/tx/0xea0c229f816af8901551dd2837a22661b18f6e08f9646d864cd36d6b4caff0d1) |

The sample documents are in `data/samples/`. The private memo is fictional and labelled as such.

Glassnet only accepts legacy (type-0) transactions; the deploy and publish commands handle that.
Source: [Prismo Builder Guide](https://prismo.technology/builder-guide).

## Setup on Windows

Everything runs directly on Windows. No WSL or Linux needed. All commands are for PowerShell,
run from the project folder unless noted.

### 1. Install the tools

| Tool | Download | Note |
|---|---|---|
| Python 3.12 | https://www.python.org/downloads/ | Tick **"Add python.exe to PATH"** on the first screen |
| Node.js LTS (22 or newer) | https://nodejs.org/ | Default options are fine |
| Git | https://git-scm.com/download/win | Default options are fine |
| Ollama | https://ollama.com/ | For private-document answers |

Then pull the local model and allow PowerShell to run the virtual environment (one time):
```powershell
ollama pull gemma3:4b
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### 2. Get the code and install dependencies
```powershell
git clone https://github.com/zazazzz-exe/VERIFIED-RAG-CITATIONS.git
cd VERIFIED-RAG-CITATIONS
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cd blockchain
npm install
npx hardhat build --build-profile production
cd ..
copy .env.example .env
```
Search uses a small local embedding model (`all-MiniLM-L6-v2`, about 80 MB) that downloads
itself the first time it runs.

### 3. Fill in `.env`
`.env` is gitignored. Never commit it or share its contents.

| Setting | Value |
|---|---|
| `NETWORK` | `glassnet` (or `local`, see below) |
| `RPC_URL` | `https://rpc.glassnet.prismo.network` |
| `REGISTRY_ADDRESS` | Empty until you deploy (step 4) |
| `PRIVATE_KEY` | Key of a testnet-only wallet (see Wallet safety). Needed only to deploy and publish |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | Your Claude key and model ID, for public answers |
| `AUDITOR_TOKEN` | A passphrase that unlocks private documents. Leave empty to disable them |

Check the setup at any time with:
```powershell
python -m vrag check
```

### 4. Deploy, ingest, publish, and index
Only the owner of a registry can publish to it, so anyone running this project deploys their own:
```powershell
python -m vrag deploy        # prints the address; put it in REGISTRY_ADDRESS in .env

python -m vrag ingest data\samples --pattern "public-*" --slug samples-public --name "Sample public docs" --version v1
python -m vrag ingest data\samples --pattern "private-*" --private --slug samples-private --name "Sample private memo (synthetic)" --version v1

python -m vrag publish --slug samples-public
python -m vrag publish --slug samples-private

python -m vrag index --slug samples-public
python -m vrag index --slug samples-private
```
To add your own PDFs, put them in `data\public\` or `data\private\` (both gitignored) and run the
same three commands with a new slug, for example `--slug gaa-2026 --name "GAA 2026" --version v1`.

To verify your contract's source on the explorer (from `blockchain\`):
```powershell
npx hardhat verify blockscout --network glassnet <REGISTRY_ADDRESS> <OWNER_ADDRESS>
```

### 5. Run the web app
```powershell
streamlit run app\streamlit_app.py
```
Open http://localhost:8501. The app only listens on this computer (see `.streamlit/config.toml`).
It refuses to start if the RPC's chain ID does not match `NETWORK`.

## Demo

These are the four demo cases. Each was run against the Glassnet deployment above.

1. **Ask → ✅.** Type *What is a Merkle proof and how big is it?* Claude answers from the public
   samples, and each citation shows ✅ Verified. Open a citation to see the passage text, its ID,
   the corpus, and a link to the contract on the explorer.
2. **Tamper → ❌.** Turn on **Tamper demo** in the sidebar. The first cited passage gets one
   character changed and is labelled *(modified for demo)*. Its badge turns ❌ Not verified,
   because the hash of the displayed text no longer matches the published root. The other
   citations stay ✅. Turn it off to return to ✅.
3. **Unknown source → ❌.** If the model cites a label it was not given (for example `[P9]` when
   only `[P1]`–`[P3]` were retrieved), that citation shows ❌ Unknown source. Claude does not do
   this on its own, so this case cannot be triggered from the app on demand. It is covered by the
   automated tests (`tests/test_app.py`, `tests/test_rag_verify.py`).
4. **Private receipt → ✅.** Enter your `AUDITOR_TOKEN` under **Private documents** and ask
   *How much is allocated to the Riverside Footbridge and how is it split?* The answer comes from
   Ollama (shown under the answer) and the citation shows ✅. Open it and click **Export receipt**.
   Then click **Lock** (or open the app in a private browser window), paste the receipt into
   **Verify a citation receipt**, and click **Verify receipt**: it shows ✅ Verified and the corpus
   name and version, without needing the passage text. A receipt is only a hash, a proof, and
   chain details, never the text or salt.

The same checks work from the terminal:
```powershell
python -m vrag ask "What is a Merkle proof and how big is it?"
python -m vrag ask "How much is allocated to the Riverside Footbridge?" --auditor --export-receipts receipts
python -m vrag verify-receipt receipts\receipt-samples-private-P1.json
python -m vrag ask "What is a Merkle proof?" --provider ollama    # compare providers on public questions
```
`--provider anthropic` is refused for questions that include private passages.

## Local development

Run a local chain instead of Glassnet:
```powershell
cd blockchain; npx hardhat node     # separate terminal; chain ID 31337
```
In `.env`, set `NETWORK=local`, `RPC_URL=http://127.0.0.1:8545`, use account #0's key printed by
`npx hardhat node` as `PRIVATE_KEY`, then deploy, publish, and index as in step 4. Restarting
`npx hardhat node` wipes the chain, so deploy and publish again after each restart.

Tests:
```powershell
pytest -q                          # Python tests
cd blockchain; npx hardhat test    # contract tests, including the Python/Solidity Merkle fixture
```
`npx hardhat test` rebuilds the contract without the optimizer. Run
`npx hardhat build --build-profile production` again before deploying.

## Wallet safety

- Create a new wallet account used only for Glassnet testing. Never use a wallet with real funds.
- Put its private key only in `.env`, which is gitignored. Never commit, paste, or screenshot it.
- Test USDC from the [faucet](https://faucet.glassnet.prismo.network) has no real value.

## Known limitations

- **A ✅ proves the passage is real, not that the answer is right.** The model can still summarize
  a real passage wrongly. The app always shows the original passage so readers can compare.
- **Claude answers are not deterministic.** `claude-opus-5-5` rejects the `temperature` setting,
  so the same question can get differently worded answers. Ollama runs at temperature 0.
  Verification does not depend on the wording.
- **Unknown-source and tamper cases are demos.** Unknown source cannot be triggered from the UI,
  and the tamper toggle changes only the first citation.
- **Glassnet can be briefly unreachable.** One test run returned ⚪ Unable to verify during a short
  RPC outage. Asking again fixed it. The app never shows ✅ when the chain cannot be reached.
- **Each corpus file remembers one network.** `out/corpora/<slug>.json` stores one corpus ID,
  registry, and chain. Publishing to another registry replaces that record, and there is no command
  to link a local corpus to a corpus already published elsewhere.
- **Re-ingesting a private corpus changes its root.** Its salts are random, so a re-ingested private
  corpus must be published again as a new corpus. Public corpora give the same root every time.
- **Private storage is a local file.** Private text and salts live in `out/private.db` on one
  machine, unencrypted. Back it up: without the salts, private citations cannot be verified.
- **Receipts reveal metadata.** A receipt shows the document ID, page, and passage index (not the
  text). The chain also shows each corpus's name, version, and passage count.
- **The auditor token is demo-level access control**, not production authentication.
- **The embedding model is English-only.** Search quality on Filipino text has not been tested.
- **Passage splitting is heuristic.** Tables and multi-column PDF layouts may split awkwardly.
  Only the sample TXT files have been run end to end; no real budget PDF has been published yet.
- **Prismo's built-in vector database has no public API yet**, so the project uses Chroma.
