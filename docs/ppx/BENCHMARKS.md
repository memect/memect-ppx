# Benchmarks

PPX benchmark results in this repository are based on the OmniDocBench dataset
and evaluation pipeline.

## Benchmark Source

- Repository: [OpenDataLab / OmniDocBench](https://github.com/opendatalab/OmniDocBench/tree/main)
- Paper: [OmniDocBench: Benchmarking Diverse PDF Document Parsing with Comprehensive Annotations](https://arxiv.org/abs/2412.07626)

## Citation and Attribution

If you reference the benchmark results reported by PPX, please also cite
OmniDocBench and review the upstream repository for the latest benchmark
implementation details and dataset terms.

We thank the OmniDocBench authors and OpenDataLab for providing the public
benchmark and evaluation tooling used for PPX benchmarking.

## Compliance Note

- The OmniDocBench repository is published under Apache-2.0.
- OmniDocBench also states that its dataset is for research purposes only and
  not for commercial use.
- If you reuse benchmark data, derived evaluation assets, or reported results,
  review the upstream dataset terms and copyright statement before any
  commercial redistribution or marketing use.

## OmniDocBench 0410

| Method | Model Type | Size | Overall | TextEdit | FormulaCDM | TableTEDS | TableTEDS-S | Read OrderEdit |
| ------ | ---------- | ---- | ------: | -------: | ---------: | --------: | ----------: | -------------: |
| **PPX** | **Hybrid** | **-** | **93.24** | **0.064** | **94.71** | **91.42** | **94.03** | **0.15** |
| MinerU2.5-Pro | Specialized VLMs | 1.2B | 95.75 | 0.036 | 97.45 | 93.42 | 95.92 | 0.12 |
| GLM-OCR | Specialized VLMs | 0.9B | 95.22 | 0.044 | 97.18 | 92.83 | 95.39 | 0.133 |
| PaddleOCR-VL-1.5 | Specialized VLMs | 0.9B | 94.93 | 0.038 | 96.89 | 91.67 | 94.37 | 0.13 |
| PaddleOCR-VL | Specialized VLMs | 0.9B | 94.18 | 0.040 | 95.91 | 90.65 | 93.74 | 0.135 |
| Youtu-Parsing | Specialized VLMs | 2.5B | 93.74 | 0.044 | 93.63 | 92.02 | 95.00 | 0.116 |
| Ovis2.6-30B-A3B | General VLMs | 30B | 93.70 | 0.035 | 95.17 | 89.44 | 92.40 | 0.135 |
| Logics-Parsing-v2 | Specialized VLMs | 4B | 93.33 | 0.041 | 95.65 | 88.42 | 91.98 | 0.137 |
| FireRed-OCR | Specialized VLMs | 2B | 93.26 | 0.037 | 95.44 | 88.04 | 91.06 | 0.131 |
| MinerU-2.5 | Specialized VLMs | 1.2B | 93.04 | 0.045 | 95.77 | 87.88 | 91.47 | 0.13 |
| Gemini 3 Pro | General VLMs | - | 92.91 | 0.064 | 95.99 | 89.15 | 92.96 | 0.165 |
| Gemini 3 Flash | General VLMs | - | 92.62 | 0.066 | 95.16 | 89.29 | 93.51 | 0.172 |
| dots.ocr | Specialized VLMs | 3B | 90.77 | 0.048 | 89.95 | 87.18 | 90.58 | 0.138 |
| OpenDoc-0.1B | Specialized VLMs | 0.1B | 90.67 | 0.049 | 93.02 | 83.88 | 87.45 | 0.14 |
| DeepSeek-OCR 2 | Specialized VLMs | 3B | 90.25 | 0.050 | 91.84 | 83.89 | 87.75 | 0.144 |
| HunyuanOCR | Specialized VLMs | 1B | 89.95 | 0.088 | 87.68 | 91.01 | 93.23 | 0.171 |
| Qwen3-VL-235B | General VLMs | 235B | 89.78 | 0.063 | 92.55 | 83.07 | 86.75 | 0.166 |
| Dolphin-v2 | Specialized VLMs | 3B | 89.50 | 0.069 | 91.01 | 84.40 | 87.44 | 0.15 |
| OCRVerse | Specialized VLMs | 4B | 88.60 | 0.063 | 89.61 | 82.44 | 86.27 | 0.163 |
| MonkeyOCR-pro-3B | Specialized VLMs | 3B | 88.57 | 0.074 | 88.74 | 84.35 | 88.62 | 0.189 |
| GPT-5.2 | General VLMs | - | 86.59 | 0.114 | 88.21 | 82.95 | 87.93 | 0.193 |
| Dolphin-1.5 | Specialized VLMs | 0.3B | 86.52 | 0.094 | 87.49 | 81.43 | 84.82 | 0.167 |
| olmOCR | Specialized VLMs | 7B | 85.74 | 0.139 | 88.10 | 83.00 | 87.17 | 0.216 |
| Mistral OCR | Specialized VLMs | - | 85.66 | 0.097 | 89.91 | 76.78 | 80.93 | 0.171 |
| Kimi K2.5 | General VLMs | 1T | 84.53 | 0.107 | 83.50 | 80.76 | 84.00 | 0.211 |
| InternVL3.5-241B | General VLMs | 241B | 83.76 | 0.130 | 89.95 | 74.35 | 79.78 | 0.215 |
| Nanonets-OCR-s | Specialized VLMs | 3B | 83.61 | 0.108 | 81.46 | 80.18 | 84.51 | 0.213 |
| POINTS-Reader | Specialized VLMs | 3B | 83.37 | 0.096 | 85.72 | 73.98 | 77.40 | 0.198 |
| Marker | Pipeline Tools | - | 78.44 | 0.157 | 85.24 | 65.77 | 73.24 | 0.243 |
