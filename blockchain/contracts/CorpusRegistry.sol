// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {MerkleProof} from "@openzeppelin/contracts/utils/cryptography/MerkleProof.sol";

/// @title CorpusRegistry
/// @notice Stores Merkle roots of document corpora. A published root can never be changed
/// or deleted; a corrected document set is published as a new corpus with a new ID.
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

    event CorpusPublished(
        uint256 indexed corpusId,
        bytes32 root,
        string name,
        string version,
        bool isPrivate
    );

    error EmptyRoot();
    error NoPassages();
    error UnknownCorpus(uint256 corpusId);

    constructor(address initialOwner) Ownable(initialOwner) {}

    /// @notice Publish a new corpus root. Only the owner may call this.
    function publish(
        bytes32 root,
        string calldata name,
        string calldata version,
        bool isPrivate,
        uint32 passageCount
    ) external onlyOwner returns (uint256 corpusId) {
        if (root == bytes32(0)) revert EmptyRoot();
        if (passageCount == 0) revert NoPassages();

        corpusId = _corpora.length;
        _corpora.push(
            Corpus({
                root: root,
                name: name,
                version: version,
                isPrivate: isPrivate,
                passageCount: passageCount,
                publishedAt: uint64(block.timestamp)
            })
        );

        emit CorpusPublished(corpusId, root, name, version, isPrivate);
    }

    function corpusCount() external view returns (uint256) {
        return _corpora.length;
    }

    function getCorpus(uint256 corpusId) external view returns (Corpus memory) {
        if (corpusId >= _corpora.length) revert UnknownCorpus(corpusId);
        return _corpora[corpusId];
    }

    /// @notice Leaf hash: double keccak256 of the ABI-encoded passage fields.
    function leafOf(
        string calldata docId,
        uint32 page,
        uint32 index,
        bytes32 contentHash
    ) public pure returns (bytes32) {
        return keccak256(bytes.concat(keccak256(abi.encode(docId, page, index, contentHash))));
    }

    /// @notice True only if the proof links the computed leaf to the stored root.
    /// Returns false for an unknown corpus ID.
    function verifyPassage(
        uint256 corpusId,
        bytes32[] calldata proof,
        string calldata docId,
        uint32 page,
        uint32 index,
        bytes32 contentHash
    ) external view returns (bool) {
        if (corpusId >= _corpora.length) return false;
        return MerkleProof.verifyCalldata(
            proof,
            _corpora[corpusId].root,
            leafOf(docId, page, index, contentHash)
        );
    }
}
