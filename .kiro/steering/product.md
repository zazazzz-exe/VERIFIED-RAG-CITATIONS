---
inclusion: always
---

# Product: Verified RAG Citations

A question-answering chatbot for Philippine public documents (starting with national
budget documents) where every citation can be checked against tamper-proof records
on the Prismo blockchain.

## The problem
AI chatbots sometimes invent or alter the sources they quote. Readers have no easy way
to tell a real quote from a made-up one.

## How it solves it
1. Documents are split into passages. Each passage is fingerprinted (hashed).
2. All fingerprints are combined into one Merkle root, which is published on Prismo.
3. When the chatbot answers, each cited passage comes with a Merkle proof.
4. The app checks every citation against the root on Prismo and shows ✅ or ❌.

## Why Prismo
Prismo (https://prismo.technology/) is an EVM-compatible L2 built for data that must be
publicly verifiable but privately protected. This project uses that directly:
- Public document sets: roots on Prismo prove the documents were not changed after publishing.
- Private document sets: the text stays in private storage, and only the root goes on Prismo.
  Authorized users can read and verify private citations. Anyone can confirm a private
  citation receipt exists in the official set without seeing its text.

## What a ✅ means (keep the UI honest about this)
A ✅ proves the cited passage is word-for-word part of the published document set.
It does NOT prove the chatbot's summary is correct. The UI must always show the original
passage text next to the answer.

## Users
- Citizen: asks questions about public documents.
- Auditor (authorized user): can also search and read private document sets.
- Publisher: ingests documents and publishes roots on Prismo.

## Out of scope for the MVP
- Encrypted feedback totals with Paillier (stretch goal, later spec)
- Prismo's built-in vector database (no public API yet; Chroma is used instead)
- Production-grade login, multiple publishers, governance
