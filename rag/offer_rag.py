"""Policy-grounded retention-offer RAG pipeline for Lab 3 Track B.

The production path uses Titan Text Embeddings V2 plus a local FAISS index and
Claude Haiku through its Bedrock inference profile.  ``offline=True`` exists
only to make the policy controls and evidence reproducible without AWS access.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
POLICY_DIR = ROOT / "northstar-policy-docs"
EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"
GENERATION_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
CHUNK_SIZE = 900
CHUNK_OVERLAP = 150


@dataclass(frozen=True)
class Chunk:
    id: str
    doc_id: str
    text: str


def _doc_id(text: str) -> str:
    match = re.search(r"\*\*Document ID:\*\*\s*(\S+)", text)
    return match.group(1) if match else "UNKNOWN"


def build_chunks(policy_dir: Path = POLICY_DIR) -> list[Chunk]:
    """Split Markdown policies into bounded chunks with adjacent context."""
    chunks: list[Chunk] = []

    for path in sorted(policy_dir.glob("*.md")):
        source = path.read_text(encoding="utf-8")
        doc_id = _doc_id(source)

        # paragraphs = [p.strip() for p in re.split(r"\n\s*\n", source) if p.strip()]
        sections = [p.strip() for p in re.split(r"#+", source) if p.strip()]

        ordinal = 0
        current = ""

        def emit(text: str) -> None:
            nonlocal ordinal
            text = text.strip()
            if text:
                ordinal += 1
                chunks.append(Chunk(f"{doc_id}-{ordinal:03d}", doc_id, text))

        for section in sections:
            # Split a single oversized paragraph into bounded pieces.
            paragraphs = [p.strip() for p in re.split(r"\n\s*\n", section) if p.strip()]
            for paragraph in paragraphs[1:]:
                pieces = [
                    paragraphs[0] + ": " + paragraph[i : i + CHUNK_SIZE]
                    for i in range(0, len(paragraph), CHUNK_SIZE)
                ]

                for piece in pieces:
                    if len(piece) < 7 + len(paragraphs[0]):
                        continue
                    if len(pieces) < 2:
                        emit(piece)
                    else:
                        candidate = f"{current}\n\n{piece}".strip()
                        if current and len(candidate) > CHUNK_SIZE:
                            previous = current
                            emit(previous)

                            overlap = previous[-CHUNK_OVERLAP:]
                            current = f"{overlap}\n\n{piece}".strip()

                            # Overlap itself can make the chunk too large.
                            if len(current) > CHUNK_SIZE:
                                current = piece
                        else:
                            current = candidate

        if current:
            emit(current)

    return chunks


def _lexical_vector(text: str, dimensions: int = 1024) -> np.ndarray:
    """Stable offline stand-in; AWS runs always use Titan embeddings."""
    vector = np.zeros(dimensions, dtype="float32")
    for token in re.findall(r"[a-z0-9]+", text.lower()):
        vector[int(hashlib.sha256(token.encode()).hexdigest(), 16) % dimensions] += 1
    norm = np.linalg.norm(vector)
    return vector / norm if norm else vector


class OfferRAG:
    def __init__(self, region: str = "us-east-1", offline: bool = False):
        self.region = region
        self.offline = offline
        self.chunks = build_chunks()
        self._vectors: np.ndarray | None = None
        self._faiss_index = None
        self._client = None

    def _bedrock(self):
        if self._client is None:
            import boto3

            self._client = boto3.client("bedrock-runtime", region_name=self.region)
        return self._client

    def _embed(self, texts: list[str]) -> np.ndarray:
        if self.offline:
            return np.vstack([_lexical_vector(t) for t in texts])
        response_vectors = []
        for text in texts:
            response = self._bedrock().invoke_model(
                modelId=EMBEDDING_MODEL,
                body=('{"inputText": ' + __import__("json").dumps(text) + "}"),
                contentType="application/json",
                accept="application/json",
            )
            response_vectors.append(
                __import__("json").loads(response["body"].read())["embedding"]
            )
        vectors = np.asarray(response_vectors, dtype="float32")
        return vectors / np.maximum(
            np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12
        )

    def _index(self) -> np.ndarray:
        if self._vectors is None:
            self._vectors = self._embed([chunk.text for chunk in self.chunks])
            import faiss

            self._faiss_index = faiss.IndexFlatIP(self._vectors.shape[1])
            self._faiss_index.add(self._vectors)
        return self._vectors

    @staticmethod
    def queries(customer: dict[str, Any], strategy: str) -> list[str]:
        tier = customer["loyalty_tier"]
        entitlement = f"loyalty tier {tier} benefits points earn rate free shipping threshold return window birthday reward"
        constraints = "final sale non-returnable exclusions retention offer valid 45 days single-use cannot stack"
        hybrid = f"tier {tier} entitlements shipping thresholds for a customer who shops {'online' if customer.get('online_to_store_ratio', 0) > 0.5 else 'in store'}"
        if strategy == "A":
            return [entitlement]
        if strategy == "B":
            return [constraints]
        if strategy == "C":
            return [hybrid]
        if strategy == "final":
            return [entitlement, constraints]
        raise ValueError("strategy must be A, B, C, or final")

    def retrieve(
        self, customer: dict[str, Any], strategy: str = "final", k: int = 4
    ) -> list[Chunk]:
        self._index()
        q = self._embed(self.queries(customer, strategy))
        # A small local IndexFlatIP is exact cosine search after normalization.
        _, indexes = self._faiss_index.search(q, k)
        selected: list[int] = []
        for index in indexes.flatten():
            if int(index) not in selected:
                selected.append(int(index))
        return [self.chunks[index] for index in selected[:k]]

    @staticmethod
    def _profile_context(customer: dict[str, Any]) -> str:
        safe = {
            key: customer.get(key)
            for key in (
                "loyalty_tier",
                "churn_probability",
                "days_since_last_purchase",
                "total_lifetime_value",
                "avg_order_value",
                "category_diversity_score",
                "online_to_store_ratio",
                "top_categories",
            )
        }
        return "Customer profile: " + "; ".join(
            f"{key}={value}" for key, value in safe.items()
        )

    @staticmethod
    def _fallback_offer(customer: dict[str, Any]) -> str:
        """Policy-safe baseline used exclusively when --offline is selected."""
        tier = customer["loyalty_tier"]
        category = customer["top_categories"][0]
        benefit = {
            "Bronze": "your 1x points earn rate and free standard shipping on orders of $75 or more",
            "Silver": "your 1.25x points earn rate and 12-hour early sale access",
            "Gold": "your 1.5x points earn rate and free standard shipping",
            "Platinum": "your 2x points earn rate and $100 annual gear credit, if available",
        }[tier]
        return f"Return to NorthStar for {category} and make the most of {benefit}. Your retention offer is single-use, tied to your account, and valid for 45 days; shop {category} when you are ready."

    def generate(
        self,
        customer: dict[str, Any],
        question: str,
        strategy: str = "final",
    ) -> dict[str, Any]:
        chunks = self.retrieve(customer, strategy)
        profile_context = self._profile_context(customer)
        policy_contexts = [f"[{c.id}] {c.text}" for c in chunks]

        contexts = [profile_context] + policy_contexts

        if self.offline:
            answer = self._fallback_offer(customer)
        else:
            system = """You generate concise, personalized NorthStar retention offers.
    Return ONLY a customer-facing offer in 2–3 sentences.

    Use only facts supported by the supplied customer profile, policy excerpts,
    and any explicitly provided approved promotion.

    - Personalize the offer using relevant customer details.
    - Recommend exactly one category from top_categories when available.
    - Highlight one or two relevant, verified benefits.
    - Never invent discounts, bonus points, codes, products, availability,
    minimum purchases, deadlines, or redemption restrictions.
    - Do not present an existing benefit as a newly granted promotion.
    - Never infer trailing-12-month qualifying spend from lifetime value.
    - Do not promise an upgrade or a distance to the next tier without
    verified qualifying spend.
    - Do not promise tier-specific benefits unless the current policy tier
    has been verified.
    - Mention targeted-offer restrictions or expiration only when an
    approved promotion explicitly provides those terms.
    - Never promise free expedited shipping.
    - If no approved promotion is supplied, use existing verified benefits.
    - Return only the offer, with no heading or explanation."""

            prompt = (
                f"Customer profile:\n{profile_context}\n\n"
                f"Policy excerpts:\n"
                f"{chr(10).join(policy_contexts)}\n\n"
                f"Request: {question}"
            )

            response = self._bedrock().converse(
                modelId=GENERATION_MODEL,
                system=[{"text": system}],
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"temperature": 0},
            )
            answer = response["output"]["message"]["content"][0]["text"].strip()

        return {
            "answer": answer,
            "contexts": contexts,
            "chunk_ids": [c.id for c in chunks],
        }
