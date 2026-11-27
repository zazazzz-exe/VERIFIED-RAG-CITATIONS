import { expect } from "chai";
import { network } from "hardhat";

const { ethers } = await network.create();

const ROOT = ethers.keccak256(ethers.toUtf8Bytes("root"));

async function deploy() {
  const [owner, other] = await ethers.getSigners();
  const registry = await ethers.deployContract("CorpusRegistry", [owner.address]);
  return { registry, owner, other };
}

describe("CorpusRegistry", () => {
  it("lets the owner publish and returns sequential corpus IDs", async () => {
    const { registry } = await deploy();
    expect(await registry.publish.staticCall(ROOT, "GAA", "v1", false, 3)).to.equal(0n);
    await registry.publish(ROOT, "GAA", "v1", false, 3);
    expect(await registry.publish.staticCall(ROOT, "GAA", "v2", false, 4)).to.equal(1n);
    await registry.publish(ROOT, "GAA", "v2", false, 4);
    expect(await registry.corpusCount()).to.equal(2n);
  });

  it("emits CorpusPublished with the right fields", async () => {
    const { registry } = await deploy();
    await expect(registry.publish(ROOT, "Memo", "v1", true, 2))
      .to.emit(registry, "CorpusPublished")
      .withArgs(0n, ROOT, "Memo", "v1", true);
  });

  it("stores the corpus data", async () => {
    const { registry } = await deploy();
    await registry.publish(ROOT, "GAA", "v1", false, 3);
    const c = await registry.getCorpus(0);
    expect(c.root).to.equal(ROOT);
    expect(c.name).to.equal("GAA");
    expect(c.version).to.equal("v1");
    expect(c.isPrivate).to.equal(false);
    expect(c.passageCount).to.equal(3n);
    expect(c.publishedAt).to.be.greaterThan(0n);
  });

  it("reverts when a non-owner publishes", async () => {
    const { registry, other } = await deploy();
    await expect(registry.connect(other).publish(ROOT, "GAA", "v1", false, 3))
      .to.be.revertedWithCustomError(registry, "OwnableUnauthorizedAccount")
      .withArgs(other.address);
  });

  it("rejects a zero root", async () => {
    const { registry } = await deploy();
    await expect(registry.publish(ethers.ZeroHash, "GAA", "v1", false, 3)).to.be.revertedWithCustomError(
      registry,
      "EmptyRoot",
    );
  });

  it("rejects a zero passage count", async () => {
    const { registry } = await deploy();
    await expect(registry.publish(ROOT, "GAA", "v1", false, 0)).to.be.revertedWithCustomError(
      registry,
      "NoPassages",
    );
  });

  it("returns false from verifyPassage for an unknown corpus ID", async () => {
    const { registry } = await deploy();
    const hash = ethers.keccak256(ethers.toUtf8Bytes("p"));
    expect(await registry.verifyPassage(0, [], "doc", 1, 0, hash)).to.equal(false);
    await registry.publish(ROOT, "GAA", "v1", false, 1);
    expect(await registry.verifyPassage(1, [], "doc", 1, 0, hash)).to.equal(false);
  });

  it("reverts getCorpus for an unknown corpus ID", async () => {
    const { registry } = await deploy();
    await expect(registry.getCorpus(0)).to.be.revertedWithCustomError(registry, "UnknownCorpus");
  });

  it("has no function that changes or deletes a published root", async () => {
    const { registry } = await deploy();
    const names = registry.interface.fragments
      .filter((f) => f.type === "function")
      .map((f) => (f as any).name as string);
    expect(names).to.not.include.members(["setRoot", "update", "remove", "deleteCorpus"]);
  });
});
