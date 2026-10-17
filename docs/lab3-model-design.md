# Lab 3 Track B — Policy-grounded retention offers

## Design

`rag/offer_rag.py` uses Amazon Titan Text Embeddings V2 (`amazon.titan-embed-text-v2:0`) to embed the four supplied policy documents. Titan was selected because it is available in the existing Bedrock account, produces 1,024-dimension vectors, and keeps embeddings and generation under the same IAM and billing boundary. The vectors are normalized and indexed in a local FAISS `IndexFlatIP`; this is a free, exact cosine-similarity index appropriate for this small policy corpus. OpenSearch Serverless would add operational cost and complexity without improving this corpus.

Documents are split on paragraph boundaries into chunks of at most 900 characters with a 150-character overlap. The overlap preserves a heading/table relationship or a sentence crossing a paragraph boundary, while the small chunk size keeps retrieval focused on a specific entitlement or restriction. Every chunk has a stable `POL-…-NNN` or `KB-…-NNN` ID. The FAQ is indexed for recall, but generation instructions treat the governing policy excerpts as authoritative.

The production generator is Claude Haiku 4.5 through the `us.anthropic.claude-haiku-4-5-20251001-v1:0` Bedrock inference profile. Haiku is suitable for short, constrained copy and makes an inexpensive per-customer call. The request sets `temperature=0` only; it deliberately does not set `top_p`, because Claude 4+ rejects the pair. The prompt requires 2–3 sentences, a category from `top_categories`, a policy-supported incentive, a 45-day single-use/account-tied offer, and no invented discounts, codes, products, availability, or return/shipping exceptions.

The final retriever uses two passes: an entitlement query for the feature-store tier and a constraint query for final sale/retention-offer restrictions. Results are deduplicated from both passes. I did not add a re-ranker: with fewer than 60 chunks, exact FAISS search plus the two targeted queries is transparent and auditable; a second model call would add latency and cost with no measured evidence of benefit. Strategies A (entitlement), B (constraints), and C (hybrid) remain selectable in `rag/run_evaluation.py` so they can be measured with the supplied harness.

## Tier discrepancy handling

The Lab 2 feature-store `loyalty_tier` is derived from total lifetime value, whereas `POL-LOY-011` uses trailing 12-month merchandise spend. The pipeline supplies the feature-store tier to guide retrieval, but phrases tier-dependent benefits conditionally in production (for example, “if your current tier is Gold”). The system of record should replace that value at send time. This avoids claiming an entitlement for a near-threshold customer that the policy tier does not actually grant.

## Prompt-template corrections

The three planted Template 3 errors were corrected:

- Gold spans $2,000–$4,999.99, not $2,000–$9,999.99.
- Platinum begins at $5,000, not $10,000.
- No tier receives free expedited shipping; it is charged at full rate for every tier.

## Evaluation method and limits

Run the supplied evaluator with `python -m rag.run_evaluation --output docs/lab3-evidence/rag_eval.csv` after installing `rag/requirements.txt`, then repeat two more times and report the mean/range below. `RAGEvaluator` explicitly passes the Bedrock LLM and Titan embeddings to `ragas.evaluate`, so it never defaults to an unavailable OpenAI judge. The pipeline adds the customer profile as a `contexts` entry because the answer relies on it; without that context, the judge marks category and tier facts as unsupported.

No production RAGAS score is claimed in this repository yet: this workspace has no AWS credentials, and its Windows Python 3.14 environment cannot currently install RAGAS's `scikit-network` dependency. Do not substitute an offline/template run for RAGAS results. Run the pinned requirements in the course's supported Python environment, record the exact Haiku judge model, three runs, mean/range, and rerun any NaN case caused by throttling. Faithfulness is expected to be the most actionable metric; answer relevance is constrained by the harness asking an instruction rather than a natural customer question, and context recall is capped by grading commentary absent from the corpus.

| Metric | Run 1 | Run 2 | Run 3 | Mean | Range |
| --- | ---: | ---: | ---: | ---: | ---: |
| Faithfulness | pending | pending | pending | pending | pending |
| Answer relevancy | pending | pending | pending | pending | pending |
| Context recall | pending | pending | pending | pending | pending |
