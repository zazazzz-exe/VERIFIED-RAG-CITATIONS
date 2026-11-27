from types import SimpleNamespace

import pytest
from eth_account import Account

from vrag import chain, merkle
from vrag.chain import ChainError, WrongChainError
from vrag.cli import main
from vrag.config import ROOT, Config
from vrag.ingest import ingest_folder
from vrag.store import load_corpus, record_publication, save_corpus

# Public Hardhat test account #0. Never use it on a real network.
HARDHAT_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
SAMPLES = ROOT / "data" / "samples"


# --- connect -----------------------------------------------------------------

class FakeEth:
    def __init__(self, chain_id=31337, fail=False):
        self._chain_id, self._fail = chain_id, fail

    @property
    def chain_id(self):
        if self._fail:
            raise ConnectionError("refused")
        return self._chain_id


def patch_web3(monkeypatch, eth):
    """Replace chain.Web3 with a fake whose instances expose the given `eth`."""

    class FakeWeb3:
        HTTPProvider = staticmethod(lambda *args, **kwargs: None)

        def __init__(self, provider=None):
            self.eth = eth

    monkeypatch.setattr(chain, "Web3", FakeWeb3)
    return FakeWeb3

def test_connect_accepts_matching_chain_id(monkeypatch):
    patch_web3(monkeypatch, FakeEth(31337))
    assert chain.connect(Config(network="local")).eth.chain_id == 31337


def test_connect_rejects_wrong_chain_id(monkeypatch):
    patch_web3(monkeypatch, FakeEth(31337))
    with pytest.raises(WrongChainError) as err:
        chain.connect(Config(network="glassnet", rpc_url="http://example.invalid"))
    assert "101001000" in str(err.value) and "31337" in str(err.value)


def test_connect_reports_unreachable_rpc(monkeypatch):
    patch_web3(monkeypatch, FakeEth(fail=True))
    with pytest.raises(ChainError):
        chain.connect(Config(network="local"))


# --- send_legacy_tx ----------------------------------------------------------

class FakeCall:
    def __init__(self):
        self.built = None

    def estimate_gas(self, params, block_identifier=None):
        self.params = params
        self.block = block_identifier
        return 100_000

    def build_transaction(self, params):
        self.built = params
        return {"to": "0x" + "11" * 20, "value": 0, "data": "0x", "nonce": params["nonce"],
                "chainId": params["chainId"], "gas": params["gas"], "gasPrice": params["gasPrice"],
                "type": 0}


class FakeSendEth:
    chain_id = 31337
    gas_price = 7

    def __init__(self):
        self.raw = None

    def get_transaction_count(self, address, block_identifier=None):
        self.nonce_block = block_identifier
        return 4

    def send_raw_transaction(self, raw):
        self.raw = bytes(raw)
        return b"\x01" * 32

    def wait_for_transaction_receipt(self, tx_hash, timeout):
        return {"status": 1, "contractAddress": None}


def test_send_legacy_tx_builds_a_type0_transaction():
    eth = FakeSendEth()
    call = FakeCall()
    chain.send_legacy_tx(SimpleNamespace(eth=eth), call, HARDHAT_KEY)

    assert call.built["gasPrice"] == 7 and call.built["gas"] == 120_000  # +20% margin
    assert call.built["nonce"] == 4 and call.built["chainId"] == 31337
    assert call.block == "latest" and eth.nonce_block == "latest"  # never "pending" (Glassnet quirk)
    assert "maxFeePerGas" not in call.built and "maxPriorityFeePerGas" not in call.built
    # A legacy transaction is a bare RLP list (first byte >= 0xc0); typed ones start with 0x01/0x02.
    assert eth.raw[0] >= 0xC0
    decoded = Account.recover_transaction(eth.raw)
    assert decoded == Account.from_key(HARDHAT_KEY).address


def test_send_legacy_tx_reports_reverted_transaction():
    eth = FakeSendEth()
    eth.wait_for_transaction_receipt = lambda h, timeout: {"status": 0}
    with pytest.raises(ChainError, match="reverted"):
        chain.send_legacy_tx(SimpleNamespace(eth=eth), FakeCall(), HARDHAT_KEY)


def test_missing_key_and_registry_errors_do_not_leak_key():
    with pytest.raises(ChainError, match="PRIVATE_KEY"):
        chain._account(Config())
    with pytest.raises(ChainError) as err:
        chain._account(Config(private_key="not-a-key"))
    assert "not-a-key" not in str(err.value)
    with pytest.raises(ChainError, match="REGISTRY_ADDRESS"):
        chain._registry(Config(), SimpleNamespace(eth=None))


# --- store / cli bookkeeping -------------------------------------------------

def test_record_publication_updates_only_the_json(tmp_path):
    corpus = ingest_folder(SAMPLES, "samples", "Samples", "v1", pattern="public-*")
    save_corpus(corpus, tmp_path)
    record_publication("samples", 3, "0xabc", 31337, tmp_path)
    loaded = load_corpus("samples", tmp_path)
    assert (loaded.corpus_id, loaded.registry, loaded.chain_id) == (3, "0xabc", 31337)
    assert loaded.root == corpus.root


def test_reingesting_same_content_keeps_corpus_id(tmp_path):
    argv = ["ingest", str(SAMPLES), "--pattern", "public-*", "--slug", "s", "--name", "S",
            "--version", "v1", "--out-dir", str(tmp_path)]
    assert main(argv) == 0
    record_publication("s", 5, "0xabc", 31337, tmp_path)
    assert main(argv) == 0
    assert load_corpus("s", tmp_path).corpus_id == 5


OLD_REGISTRY = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
NEW_REGISTRY = "0x5a134Ed6Ef85889566F6b77d206D610EF1A5815b"


@pytest.fixture
def publish_env(tmp_path, monkeypatch):
    """A saved public corpus plus a fake chain, so `publish` never touches a real network."""
    save_corpus(ingest_folder(SAMPLES, "s", "S", "v1", pattern="public-*"), tmp_path)
    sent = []
    fake_w3 = SimpleNamespace(eth=SimpleNamespace(chain_id=101001000))

    def use(config):
        monkeypatch.setattr("vrag.cli.load_config", lambda: config)
        monkeypatch.setattr(chain, "connect", lambda cfg: fake_w3)
        monkeypatch.setattr(chain, "publish", lambda corpus, cfg, w3=None: sent.append(corpus.slug) or 7)

    return tmp_path, sent, use


def test_publish_refuses_a_corpus_already_published_on_this_registry(publish_env, capsys):
    out, sent, use = publish_env
    record_publication("s", 0, OLD_REGISTRY, 31337, out)
    use(Config(network="local", registry_address=OLD_REGISTRY.lower()))  # address case is ignored
    assert main(["publish", "--slug", "s", "--out-dir", str(out)]) == 1
    assert "already published" in capsys.readouterr().err
    assert sent == []  # no transaction


def test_publish_refuses_records_that_do_not_say_where_they_were_published(publish_env):
    out, sent, use = publish_env
    record_publication("s", 0, "", 31337, out)
    use(Config(network="local", registry_address=OLD_REGISTRY))
    assert main(["publish", "--slug", "s", "--out-dir", str(out)]) == 1
    assert sent == []


@pytest.mark.parametrize("old_registry, old_chain", [
    (OLD_REGISTRY, 31337),          # local node -> Glassnet
    (OLD_REGISTRY, 101001000),      # same chain, different registry
])
def test_publish_to_a_different_registry_replaces_the_record(publish_env, capsys, old_registry, old_chain):
    out, sent, use = publish_env
    record_publication("s", 0, old_registry, old_chain, out)
    use(Config(network="glassnet", registry_address=NEW_REGISTRY))
    assert main(["publish", "--slug", "s", "--out-dir", str(out)]) == 0
    assert sent == ["s"]
    assert "published to another network or registry" in capsys.readouterr().out
    saved = load_corpus("s", out)
    assert (saved.corpus_id, saved.registry, saved.chain_id) == (7, NEW_REGISTRY, 101001000)


def test_publish_to_the_same_registry_on_another_chain_id_is_allowed(publish_env):
    out, sent, use = publish_env
    record_publication("s", 0, NEW_REGISTRY, 31337, out)  # same address, different chain
    use(Config(network="glassnet", registry_address=NEW_REGISTRY))
    assert main(["publish", "--slug", "s", "--out-dir", str(out)]) == 0 and sent == ["s"]

def test_ingest_pattern_selects_files():
    public = ingest_folder(SAMPLES, "p", "P", "v1", pattern="public-*")
    private = ingest_folder(SAMPLES, "q", "Q", "v1", pattern="private-*")
    assert {p.doc_id for p in public.passages} == {"public-merkle-trees", "public-retrieval-augmented-generation"}
    assert {p.doc_id for p in private.passages} == {"private-synthetic-memo"}


# --- integration against `npx hardhat node` (skipped when it is not running) -----

@pytest.fixture
def local_config():
    config = Config(network="local", private_key=HARDHAT_KEY)
    try:
        chain.connect(config)
    except (ChainError, WrongChainError):
        pytest.skip("no local Hardhat node on 127.0.0.1:8545")
    if not chain.ARTIFACT_PATH.exists():
        pytest.skip("contract not built")
    return config


def test_deploy_publish_verify_on_local_node(local_config, tmp_path):
    address = chain.deploy(local_config)
    config = Config(network="local", private_key=HARDHAT_KEY, registry_address=address)
    corpus = ingest_folder(SAMPLES, "int", "Integration", "v1", pattern="public-*")
    corpus_id = chain.publish(corpus, config)

    info = chain.get_corpus(config, corpus_id)
    assert info["root"] == corpus.root and info["passageCount"] == len(corpus.passages)

    for p in corpus.passages:
        proof = merkle.proof(corpus.levels, p.leaf_index)
        assert chain.verify_passage(config, corpus_id, proof, p.doc_id, p.page, p.index, p.content_hash)
        assert not chain.verify_passage(config, corpus_id, proof, p.doc_id, p.page, p.index, bytes(32))
    assert not chain.verify_passage(config, corpus_id + 100, [], "x", 1, 0, bytes(32))
