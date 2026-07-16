# Stage 2 Evaluation Results

Evaluation date: 2026-07-16. Model: `llama3.2:3b`. Dense embedding:
`nomic-embed-text`. Sparse model: `Qdrant/bm25`.

| Pipeline | Page hit | MRR | Source metadata |
|---|---:|---:|---:|
| Dense top-10 | 95.56% | 0.7506 | 100% |
| Hybrid top-10, alpha 0.6 | 93.33% | 0.7654 | 100% |

The router reached 100% exact accuracy, macro precision, macro recall, and
multi-book exact accuracy across 40 questions.

Hybrid improved the average rank of relevant pages but missed one additional
page target at top-10. Dense therefore remains the default. LLM reranking was
verified end-to-end and retained the correct Player Rules page 25 for a Second
Wind query, but added roughly 2.5 minutes on the local 3B model. It remains an
opt-in experiment.

Raw reports:

- `results/dense.json`
- `results/hybrid.json`
- `results/router-final.json`
