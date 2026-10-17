"""Run the supplied Track B harness against the local RAG implementation."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evaluation"))
from evaluation_harness import OFFER_TEST_CASES, RAGEvaluator  # noqa: E402
from rag.offer_rag import GENERATION_MODEL, OfferRAG  # noqa: E402

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--strategy", default="final", choices=["A", "B", "C", "final"])
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--output", default=str(ROOT / "docs" / "lab3-evidence" / "rag_eval.csv"))
    args = parser.parse_args()
    rag = OfferRAG(region=args.region, offline=args.offline)
    evaluator = RAGEvaluator(lambda customer, question: rag.generate(customer, question, args.strategy), region=args.region)
    results = evaluator.run(OFFER_TEST_CASES)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(args.output, index=False)
    print(f"Judge model: {GENERATION_MODEL}; saved: {args.output}")

if __name__ == "__main__":
    main()
