# Verified RAG Citations

A chatbot for Philippine public documents (starting with the national budget) where every
citation is checked against tamper-proof records on [Prismo](https://prismo.technology/).

- ✅ The cited passage is word-for-word part of the official document set published on Prismo.
- ❌ The passage was invented, edited, or isn't from that document set.

Private document sets keep their text in private storage. Only their fingerprint (Merkle root)
goes on Prismo, so authorized users can cite them with proof.

> Status: planning. The implementation plan is in
> [`.kiro/specs/verifiable-rag-citations/`](.kiro/specs/verifiable-rag-citations/) and is meant to be
> built step by step with Kiro CLI.

## How it works

1. Documents are split into passages, and each passage is hashed (keccak256).
2. The hashes are combined into one Merkle root, which is published to the `CorpusRegistry`
   contract on Prismo Glassnet.
3. A question is answered by a local LLM (Ollama) using only retrieved passages, cited as `[P1]`, `[P2]`, …
4. For each citation, the app re-hashes the passage text it shows you and asks the contract to
   check the Merkle proof against the published root.

See [`design.md`](.kiro/specs/verifiable-rag-citations/design.md) for the full architecture.

## Prismo Glassnet (testnet)

| | |
|---|---|
| RPC | `https://rpc.glassnet.prismo.network` |
| Chain ID | `101001000` |
| Gas token | test USDC ([faucet](https://faucet.glassnet.prismo.network)) |
| Explorer | https://explorer.glassnet.prismo.network |

Glassnet only accepts legacy (type-0) transactions, so deploy commands use `--legacy`.
Source: [Prismo Builder Guide](https://prismo.technology/builder-guide).

## Setup on Windows

Everything except Ollama runs inside WSL (Ubuntu). Ollama stays on Windows.

### 1. Install WSL and Ubuntu
In PowerShell (as Administrator):
```powershell
wsl --install -d Ubuntu
```
Restart, open **Ubuntu** from the Start menu, and create a Linux username and password.

### 2. Let WSL reach Ollama on Windows
Create `C:\Users\<you>\.wslconfig` with:
```ini
[wsl2]
networkingMode=mirrored
```
Then run `wsl --shutdown` in PowerShell and reopen Ubuntu. Now `http://localhost:11434`
inside WSL reaches Ollama on Windows. (Mirrored networking needs Windows 11 22H2 or later.)

In PowerShell, download the models:
```powershell
ollama pull llama3.1:8b
ollama pull nomic-embed-text
```
Check from Ubuntu: `curl http://localhost:11434/api/tags` should list both models.

### 3. Install tools in Ubuntu
```bash
sudo apt update && sudo apt install -y git python3 python3-venv python3-pip curl unzip
curl -L https://foundry.paradigm.xyz | bash && source ~/.bashrc && foundryup
curl -fsSL https://cli.kiro.dev/install | bash
```
Check: `python3 --version` (3.11+), `forge --version`, `kiro-cli --version`.

### 4. Open the project in VS Code
1. Install [VS Code](https://code.visualstudio.com/) on Windows and its **WSL** extension.
2. In Ubuntu:
   ```bash
   git clone https://github.com/zazazzz-exe/VERIFIED-RAG-CITATIONS.git
   cd VERIFIED-RAG-CITATIONS
   cp .env.example .env
   code .
   ```
3. VS Code opens connected to WSL. Open the terminal with ``Ctrl+` `` and run `kiro-cli`.
   Sign in the first time.

### 5. Build with Kiro CLI
Kiro CLI reads the steering files in `.kiro/steering/` and the spec in
`.kiro/specs/verifiable-rag-citations/`. Work through the tasks in order, for example:
```
Execute task 1 from .kiro/specs/verifiable-rag-citations/tasks.md
```
Review each task's changes before moving to the next one.

## Wallet safety

- Create a new MetaMask account used only for Glassnet testing.
- Put its private key only in `.env`, which is gitignored. Never commit it.
- Get test USDC from the [faucet](https://faucet.glassnet.prismo.network). It has no real value.

## Limitations

- A ✅ proves the passage is real, not that the chatbot's summary of it is correct. The app always
  shows the original passage so readers can compare.
- The auditor token is demo-level access control, not production authentication.
- Prismo's built-in vector database has no public API yet, so the project uses Chroma.
