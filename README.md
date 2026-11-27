# Verified RAG Citations

A chatbot for Philippine public documents (starting with the national budget) where every
citation is checked against tamper-proof records on [Prismo](https://prismo.technology/).

- ✅ The cited passage is word-for-word part of the official document set published on Prismo.
- ❌ The passage was invented, edited, or isn't from that document set.

Private document sets keep their text in private storage. Only their fingerprint (Merkle root)
goes on Prismo, so authorized users can cite them with proof.

> Status: planning. The implementation plan (Kiro spec) is kept locally in
> `.kiro/specs/verifiable-rag-citations/` and is not published in this repository.

## How it works

1. Documents are split into passages, and each passage is hashed (keccak256).
2. The hashes are combined into one Merkle root, which is published to the `CorpusRegistry`
   contract on Prismo Glassnet.
3. A question is answered by an LLM using only retrieved passages, cited as `[P1]`, `[P2]`, …
   Claude (Anthropic API) answers from public passages. Any question that touches a private
   passage is answered by a local model (Ollama) instead, so private text never leaves the laptop.
4. For each citation, the app re-hashes the passage text it shows you and asks the contract to
   check the Merkle proof against the published root.

The design (kept locally in `.kiro/specs/verifiable-rag-citations/design.md`) has the full architecture.

## Prismo Glassnet (testnet)

| | |
|---|---|
| RPC | `https://rpc.glassnet.prismo.network` |
| Chain ID | `101001000` |
| Gas token | test USDC ([faucet](https://faucet.glassnet.prismo.network)) |
| Explorer | https://explorer.glassnet.prismo.network |

Glassnet only accepts legacy (type-0) transactions; the project's deploy and publish commands
handle that. Source: [Prismo Builder Guide](https://prismo.technology/builder-guide).

### Deployed contract

| | |
|---|---|
| Contract | `CorpusRegistry` |
| Address | [`0x5a134Ed6Ef85889566F6b77d206D610EF1A5815b`](https://explorer.glassnet.prismo.network/address/0x5a134Ed6Ef85889566F6b77d206D610EF1A5815b) |
| Source (verified) | [explorer, Code tab](https://explorer.glassnet.prismo.network/address/0x5a134Ed6Ef85889566F6b77d206D610EF1A5815b#code) |
| Owner (publisher) | `0xD3D260571718e28e2978B0521ED5190cfa352D09` |

Verify the source yourself after a deploy (from `blockchain\`):
```powershell
npx hardhat build --build-profile production
npx hardhat verify blockscout --network glassnet <REGISTRY_ADDRESS> <OWNER_ADDRESS>
```

## Setup on Windows

Everything runs directly on Windows. No WSL or Linux needed.

### 1. Install the tools
Install each of these with its normal Windows installer:

| Tool | Download | Note |
|---|---|---|
| Python 3.12 | https://www.python.org/downloads/ | Tick **"Add python.exe to PATH"** on the first screen |
| Node.js LTS (22 or newer) | https://nodejs.org/ | Default options are fine |
| Git | https://git-scm.com/download/win | Default options are fine |
| VS Code | https://code.visualstudio.com/ | You already have this |
| Ollama | https://ollama.com/ | You already have this |

Then close and reopen VS Code so it sees the new tools.

### 2. Local model (for private documents)
Search uses a small embedding model that downloads itself the first time it runs (about 80 MB).
Private-document answers use Ollama. In the VS Code terminal (``Ctrl+` ``):
```powershell
ollama pull gemma3:4b
```
Public answers use the Claude API: put `ANTHROPIC_API_KEY` and `ANTHROPIC_MODEL` in `.env`.

### 3. Check everything is installed
```powershell
python --version     # 3.12.x
node --version       # v22 or newer
git --version
ollama list          # shows both models
```

### 4. Allow PowerShell to run the Python virtual environment (one time)
```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```
Answer `Y`. This lets `.venv\Scripts\Activate.ps1` run later.

### 5. Clone the project in VS Code
1. Press **Ctrl+Shift+P**, type **Git: Clone**, and press Enter.
2. Paste `https://github.com/zazazzz-exe/VERIFIED-RAG-CITATIONS.git` and pick a folder.
3. Click **Open** when VS Code asks.
4. In the terminal: `copy .env.example .env`

### 6. Install Kiro
Pick one:
- **Kiro CLI (terminal):** in PowerShell, run `irm 'https://cli.kiro.dev/install.ps1' | iex`,
  reopen the terminal, then run `kiro-cli` in the project folder. Sign in the first time.
- **Kiro extension for VS Code** (needs VS Code 1.145 or later): see
  https://kiro.dev/docs/getting-started/installation/

### 7. Build with Kiro
The Kiro spec and steering files live in `.kiro/`, which is kept out of this repository. If you
have them locally, Kiro reads `.kiro/steering/` and `.kiro/specs/verifiable-rag-citations/`, and
you can work through the tasks in order, for example:
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
