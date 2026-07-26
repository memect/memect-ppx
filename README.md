<p align="center">
  <img src="docs/images/logo.png" alt="PPX Logo" width="60" style="vertical-align:middle"/> &nbsp;<strong style="font-size:1.5em">PPX — High-Accuracy PDF & Image Parser</strong>
</p>

[![PyPI version](https://img.shields.io/pypi/v/memect-ppx.svg)](https://pypi.org/project/memect-ppx/)
[![PyPI downloads](https://img.shields.io/pypi/dm/memect-ppx.svg)](https://pypi.org/project/memect-ppx/)
[![Python](https://img.shields.io/badge/python-%3E%3D3.12-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-orange)](LICENSE)
[![Issues](https://img.shields.io/github/issues/memect/memect-ppx)](https://github.com/memect/memect-ppx/issues)

[简体中文](README_zh-CN.md) | English

---

**Convert PDF and images to structured Markdown / JSON — locally, accurately, production-ready.**

PPX is a source-available document parsing engine built for high-fidelity extraction of text, tables, figures, formulas, and layout from PDFs and images. It ships with a built-in OCR + layout pipeline and optionally offloads recognition to state-of-the-art LLM backends (DeepSeek-OCR, PaddleOCR-VL, GLM-OCR).

- **What output do I get?** — Markdown and JSON; every object carries page coordinates.
- **Do I need a GPU?** — No. The default backend runs on CPU. GPU (CUDA) is optional for throughput.
- **Does it handle scanned PDFs?** — Yes. OCR is applied automatically when native text is absent.
- **Can I use my own LLM?** — Yes. Any OpenAI-compatible endpoint is accepted via `--backend`.
- **Is it embeddable?** — Free for personal, research, and noncommercial use. For commercial use, contact `contact@memect.co`.

---

## Install

```bash
#>=3.12
$uv venv -p 3.12
#Linux/Mac
$source .venv/bin/activate
#Windows
#.venv\Scripts\activate

#如果下载包很慢，可以如下设置
#export UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple/
$uv pip install memect-ppx
#安装其他依赖的包，避免冲突，可选参数，默认: --gpu auto，也就是如果有显卡的，自动安装对应的库，如果不想，--gpu no
#--gpu auto|no|cuda|cann|dml
#--headless  如果在docker等环境中，可能需要这个
$ppx install
#下载依赖的模型，因为需要从huggingface中下载，默认已经设置好代理，如果需要取消或者设置其他
#export HF_ENDPOINT=https://huggingface.co
$ppx download
```

## 源代码方式

```bash
$git clone https://github.com/memect/memect-ppx.git
$cd memect-ppx
$uv venv -p 3.12
#每次代码更新了，建议执行一次下面3个步骤
#如果下载包很慢，可以如下设置
#export UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple/
$uv sync --no-install-project
$./ppx install
$./ppx download
```

## 执行

```bash
$ppx parse --help
#源代码模式，请使用"./ppx"替代"ppx"
#默认解析
$ppx parse a.pdf
#大模型解析，指定url即可，目前仅仅支持deepseek-ocr，paddleocr-vl，glm-ocr等模型
$ppx parse a.pdf --llm http://127.0.0.1:4000/v1
#如果使用的模型的名字不包含deepseek，paddle，glm等，需要指定，如下：
$ppx parse a.pdf --llm '{"name":"deepseek","base_url":"http://127.0.0.1:4000/v1","model":"xxxx","api_key":""}'

#如果经常使用，可以写到配置文件中
$mkdir conf
#可以为json文件或者py文件: settings={}
#参考src/memect/conf/settings.custom.py 语法
$vi conf/settings.py
$vi conf/log.py
#如果在配置文件中写好了路径和模型等，就不需要在命令行再指定
$ppx parse a.pdf --backend deepseek

```

PPX uses the pipeline mode by default. The parsed Markdown is typically written
to `output/doc.md` when `-o output/` is provided.

Use `--html` when you also want an HTML export. PPX will write `doc.html`
alongside the regular outputs in the output directory.

---

## What Problems Does This Solve?

| Problem | How PPX Handles It |
| ------- | ------------------ |
| Native-text PDF with invisible/garbled characters | Detects encoding anomalies; falls back to OCR per page |
| Scanned document with no embedded text | Full-page OCR or vLLM backend |
| Complex table spanning multiple columns/rows | LLM-based structural parsing, `colspan`/`rowspan` preserved |
| Math-heavy academic paper | LaTeX formula extraction |
| Batch processing thousands of files | Directory-level `parse dir/` with `-o output/` |

---

## Example Outputs

### Mixed table content

This example shows a mixed table scenario where the table body contains
editable text, while much of the header area is still image-based.

Input snippet:

![Input snippet](docs/release/assets/局部图片/ori_image.png)

Markdown output:

![Markdown output](docs/release/assets/局部图片/pdf2md.png)

JSON output:

![JSON output](docs/release/assets/局部图片/pdf2json.png)

### Scanned English table

This example shows a scanned English table parsing result.

Markdown output:

![Scanned table Markdown output](docs/release/assets/英文扫描件表格解析/pdf2md.png)

JSON output:

![Scanned table JSON output](docs/release/assets/英文扫描件表格解析/pdf2json.png)

---

## Benchmarks

See [docs/BENCHMARKS.md](docs/BENCHMARKS.md) for benchmark results, citation,
attribution, and compliance notes.

---

## Capability Matrix

| Capability | Default (Local) | DeepSeek-OCR | PaddleOCR-VL | GLM-OCR |
| ---------- | :-------------: | :----------: | :----------: | :-----: |
| Text extraction | ✅ | ✅ | ✅ | ✅ |
| Per-character coordinates | ✅ | ❌ | ❌ | ❌ |
| Table structure (colspan / rowspan) | ✅ | ✅ | ✅ | ✅ |
| Formula → LaTeX | ✅ | ✅ | ✅ | ✅ |
| Figure region extraction | ✅ | ✅ | ✅ | ✅ |
| CPU-only mode | ✅ | ✅ | ✅ | ✅ |
| CUDA acceleration | ✅ | ✅ | ✅ | ✅ |
| No external service required | ✅ | ❌ | ❌ | ❌ |

---

## Which Backend Should I Use?

| Scenario | Recommended Backend |
| -------- | ------------------- |
| Privacy-sensitive documents, air-gapped environment | `default` |
| Highest accuracy on complex layouts | `deepseek` |
| Good accuracy, lighter GPU footprint (~10 GB) | `paddle` |
| Fast inference with speculative decoding | `glm` |
| Quick integration test / CI pipeline | `default` (CPU) |

---

## Quick Start

### Default pipeline mode
## GPU加速

1. ocr
  4090会快一些，2080，3090可能比现代的cpu慢

2. table
   gpu快3-5倍

3. layout
   gpu快3-5倍

4. formula
  gpu快几倍，特别是对于复杂的公式，可以到达十几倍，所以，如果有大量的公式，建议在gpu下执行，
  或者通过"--formula http://xxx/v1"  配置使用大模型(paddle/glm)

  或者：--formula mfr   gpu快，cpu慢
       --formula pp    gpu慢，cpu快
      
  如果不要把公式转换为latex, --formula no

## 启动模型

```bash
ppx parse <input_path> -o <output_path>

#这个选项的值，根据显存要求设置
#--gpu-memory-utilization

#需要大概20G
#https://modelscope.cn/models/deepseek-ai/DeepSeek-OCR-2
#不能够使用vllm==0.19.1执行，生成乱码
$vllm serve deepseek-ai/DeepSeek-OCR-2 --served-model-name deepseek-ocr-2 \
--logits-processors vllm.model_executor.models.deepseek_ocr:NGramPerReqLogitsProcessor \
--mm-processor-cache-gb 0 \
--no-enable-prefix-caching \
--port 4000 \
--gpu-memory-utilization 0.8 


#需要10G
#https://modelscope.cn/models/PaddlePaddle/PaddleOCR-VL
$vllm serve PaddlePaddle/PaddleOCR-VL \
  --served-model-name paddleocr-vl \
  --trust-remote-code \
  --max-num-batched-tokens 16384 \
  --no-enable-prefix-caching \
  --mm-processor-cache-gb 0 \
  --gpu-memory-utilization 0.5\
  --port 4001

#需要10G
#也可以启动1.5版本，模型名字，端口号等都一样，配置等就都不需要改变
#https://modelscope.cn/models/PaddlePaddle/PaddleOCR-VL-1.5
$vllm serve PaddlePaddle/PaddleOCR-VL-1.5 \
  --served-model-name paddleocr-vl \
  --trust-remote-code \
  --max-num-batched-tokens 16384 \
  --no-enable-prefix-caching \
  --mm-processor-cache-gb 0 \
  --gpu-memory-utilization 0.5\
  --port 4001

$vllm serve PaddlePaddle/PaddleOCR-VL-1.6 \
  --served-model-name paddleocr-vl \
  --trust-remote-code \
  --max-num-batched-tokens 16384 \
  --no-enable-prefix-caching \
  --mm-processor-cache-gb 0 \
  --gpu-memory-utilization 0.5\
  --port 4001

#https://modelscope.cn/models/ZhipuAI/GLM-OCR
$vllm serve ZhipuAI/GLM-OCR \
--served-model-name glmocr \
--max-num-batched-tokens 16384 \
--max-model-len 16384 \
--speculative-config '{"method": "mtp", "num_speculative_tokens": 1}' \
--gpu-memory-utilization 0.5 \
--port 4002
```

### Parse a single file

```bash
# Auto-detect whether OCR is needed
ppx parse report.pdf

# Force OCR on every page
ppx parse report.pdf --ocr yes

# Skip OCR entirely
ppx parse report.pdf --ocr no

# Parse an image
ppx parse scan.png

# Also export HTML
ppx parse report.pdf -o output/ --html
```

### Batch processing

```bash
# Parse all PDFs and images in a directory
ppx parse docs/

# Write output to a specific directory
ppx parse docs/ -o output/
```

### Use an LLM backend

```bash
# DeepSeek-OCR (requires ~20 GB VRAM via vLLM)
ppx parse report.pdf --backend deepseek \
  --deepseek '{"base_url":"http://127.0.0.1:4000/v1","model":"deepseek-ocr-2","api_key":""}'

# PaddleOCR-VL (requires ~10 GB VRAM)
ppx parse report.pdf --backend paddle \
  --paddle '{"base_url":"http://127.0.0.1:4001/v1","model":"paddleocr-vl","api_key":""}'

# GLM-OCR (requires ~10 GB VRAM)
ppx parse report.pdf --backend glm \
  --glm '{"base_url":"http://127.0.0.1:4002/v1","model":"glmocr","api_key":""}'
```

### Persist configuration

Tired of typing the same flags? Drop a config file:

```bash
mkdir conf
# conf/settings.py  (Python dict) or conf/settings.json
# Reference: src/memect/conf/settings.custom.py
```

```python
# conf/settings.py
settings = {
    "pdf_parser.deepseek.model.base_url": "http://127.0.0.1:4000/v1",
    "pdf_parser.paddle.model.base_url": "http://127.0.0.1:4001/v1",
    "pdf_parser.glm.model.base_url": "http://127.0.0.1:4002/v1",
}
```

Now just run:

```bash
ppx parse report.pdf --backend deepseek
```

---

## Use from python

PPX can be used directly as a library. If you call it repeatedly, a single global `Parser` instance is usually enough.

```python
from memect.pdf.parser import Parser
from memect.pdf.base import KDocument, KDocumentFactory

# If you call it repeatedly, a single global parser is usually enough.
# If no arguments are passed, the default settings are used.
with Parser() as parser:
    doc = KDocument("/path/your.pdf")
    parser.parse(doc)

# Batch parsing with multiprocessing and default settings.
doc = KDocumentFactory("/path/your.pdf", params=None)
docs = [doc]
Parser.batch(docs, max_workers=1)
```

---

## CLI Reference

```text
ppx parse <path> [OPTIONS]

Arguments:
  path          PDF file, image file, or directory

Options:
  --backend     default | deepseek | paddle | glm   (default: default)
  --ocr         yes | no | auto                      (default: auto)
  --table       no | ybk | wbk | auto | llm          (default: auto)
  --html        Write HTML output (`doc.html`)
  --json        Write structured JSON output (`doc.json`)
  --pages       Page range, e.g. "1-5,10"
  --mode        page | tree                    (default: page)
  -o, --output  Output directory
```

HTML example:

```bash
./ppx parse example/专利证书_1.pdf -o output/ --html
```

Other subcommands:

```text
ppx start               Launch HTTP API server
```

## Output Format

Each parsed document is written to `<input>.out/`:

```text
report.pdf.out/
├── doc.md          # full document in Markdown
├── doc.html        # optional HTML export when --html is enabled
├── doc.json        # full structured data with per-object coordinates
├── pages/          # per-page breakdown (one entry per page)
└── images/         # extracted figures/images (present when figures are detected)
```

| Path | Description |
| ---- | ----------- |
| `doc.md` | Markdown with figure references |
| `doc.html` | Optional positioned HTML preview/export generated by `--html` |
| `doc.json` | JSON tree: document → pages → objects, each with bounding-box coordinates |
| `pages/` | Per-page Markdown and JSON, useful for page-level processing |
| `images/` | Extracted image regions; only present when the document contains figures |

---

## Platform Support

| Platform | Python | CPU | CUDA | Notes |
| -------- | ------ | :-: | :--: | ----- |
| Linux | >= 3.12 | ✅ | ✅ | Recommended for production |
| macOS (Apple Silicon) | >= 3.12 | ✅ | ❌ | |
| macOS (Intel) | 3.12 – 3.13 | ✅ | ❌ | Capped by OpenVINO |
| Windows | >= 3.12 | ✅ | ✅ | Community-tested |

CUDA requires NVIDIA driver + CUDA 12.x and `onnxruntime-gpu` built for that CUDA version.

---

## Docker Deployment

```bash
cp .env.sample .env
docker compose up -d --build
```

If your build environment has slow access to the official Debian mirrors, set mirror overrides at build time only:

```bash
DEBIAN_MIRROR=https://mirrors.aliyun.com/debian \
PYPI_INDEX_URL=https://mirrors.aliyun.com/pypi/simple \
docker compose build ppx
```

The CUDA image pins `onnxruntime-gpu` to `PPX_ONNXRUNTIME_GPU_VERSION=1.23.2` by default, preventing newer wheels that require CUDA 13 libraries from being installed alongside a CUDA 12 Python runtime.

GPU services require the NVIDIA Container Toolkit on the host. Start them via the compose profile:

```bash
COMPOSE_PROFILES=gpu PPX_GPU_IMAGE=hub.wenyinhulian.cn/docparser/ppx-gpu:260702 PPX_GPU_DEVICE_ID=0 docker compose up -d ppx-gpu
```

On multi-GPU machines, use `PPX_GPU_DEVICE_ID=0` to select the host GPU.

For production Linux GPU images, use the build script. The default output tag is `hub.wenyinhulian.cn/docparser/ppx-gpu:<YYMMDD>`, e.g. `hub.wenyinhulian.cn/docparser/ppx-gpu:260702`:

```bash
DEBIAN_MIRROR=https://mirrors.cloud.tencent.com/debian \
PYPI_INDEX_URL=https://mirrors.aliyun.com/pypi/simple \
scripts/build-gpu-image.sh
```

The script also creates a local convenience alias `ppx:gpu`. To push to a private registry after logging in to Docker:

```bash
scripts/build-gpu-image.sh --push
```

To pin a specific tag:

```bash
PPX_IMAGE_TAG=260702 scripts/build-gpu-image.sh
```

By default, one `ppx` API/web service starts inside the container listening on port `9527`; the host port is controlled by `PPX_PORT`. Common mount directories:

- `PPX_DATA_DIR_HOST`: uploads, tasks, and error files — default `./data`
- `PPX_LOG_DIR_HOST`: log directory — default `./logs`
- `PPX_CONF_DIR_HOST`: optional config directory — default `./conf`; place `settings.py` here to override defaults

Common runtime variables:

- `PPX_LOG_LEVEL`: log level — default `INFO`
- `PPX_CPU`: force CPU for some or all models — `all`, `ocr`, `layout`, `table`, `formula`
- `PPX_FORMULA`: formula recognition model — default `formula-pp`; also accepts `formula-mfr`, `paddle`, `glm`

For PDF service settings (directories, file limits, task queue size), prefer `conf/settings.py` over environment variables:

```python
settings = {
    "pdf_service.data_dir": "./data/pdf",
    "pdf_service.task_manager.max_running_size": 4,
    "pdf_service.pdf.max_page_count": 2000,
}
```

External LLM services are configured via URL: `PPX_DEEPSEEK_URL`, `PPX_PADDLE_URL`, `PPX_GLM_URL`, `PPX_BAIDU_URL`. If the model service runs on the host, use `http://host.docker.internal:<port>/v1` inside Docker; if it is in the same compose network, use the service name instead.

Avoid exposing this service directly to the public internet. The `/api/parse`, `/api/parse/state`, and `/admin/gc` endpoints are unauthenticated; in production, place the service behind an internal network or reverse proxy with authentication.

---

## Launching LLM Services

PPX LLM backends are served via [vLLM](https://github.com/vllm-project/vllm).

```bash
# 常用环境变量，可以附加在命令前面
export CUDA_VISIBLE_DEVICES=0
# 国内建议使用 ModelScope，下面的模型 ID 也是相对 ModelScope，HuggingFace 的可能有所不同
export VLLM_USE_MODELSCOPE=True
```

### DeepSeek-OCR-2 (~20 GB VRAM)

[ModelScope](https://modelscope.cn/models/deepseek-ai/DeepSeek-OCR-2) — note: vllm==0.19.1 produces garbled output, use a newer version.

```bash
vllm serve deepseek-ai/DeepSeek-OCR-2 \
  --served-model-name deepseek-ocr-2 \
  --logits-processors vllm.model_executor.models.deepseek_ocr:NGramPerReqLogitsProcessor \
  --mm-processor-cache-gb 0 \
  --no-enable-prefix-caching \
  --gpu-memory-utilization 0.8 \
  --port 4000
```

### PaddleOCR-VL / PaddleOCR-VL-1.5 (~10 GB VRAM)

[ModelScope PaddleOCR-VL](https://modelscope.cn/models/PaddlePaddle/PaddleOCR-VL) · [PaddleOCR-VL-1.5](https://modelscope.cn/models/PaddlePaddle/PaddleOCR-VL-1.5)

```bash
# PaddleOCR-VL
vllm serve PaddlePaddle/PaddleOCR-VL \
  --served-model-name paddleocr-vl \
  --trust-remote-code \
  --max-num-batched-tokens 16384 \
  --no-enable-prefix-caching \
  --mm-processor-cache-gb 0 \
  --gpu-memory-utilization 0.5 \
  --port 4001

# PaddleOCR-VL-1.5 (same model name and port — config unchanged)
vllm serve PaddlePaddle/PaddleOCR-VL-1.5 \
  --served-model-name paddleocr-vl \
  --trust-remote-code \
  --max-num-batched-tokens 16384 \
  --no-enable-prefix-caching \
  --mm-processor-cache-gb 0 \
  --gpu-memory-utilization 0.5 \
  --port 4001
```

### GLM-OCR (~10 GB VRAM)

[ModelScope](https://modelscope.cn/models/ZhipuAI/GLM-OCR)

```bash
vllm serve ZhipuAI/GLM-OCR \
  --served-model-name glmocr \
  --max-num-batched-tokens 16384 \
  --max-model-len 16384 \
  --speculative-config '{"method": "mtp", "num_speculative_tokens": 1}' \
  --gpu-memory-utilization 0.5 \
  --port 4002
```

## Train Layout Model

```bash
# Clone ppx or install it, then install PaddleX in the current directory
$cd ppx
$git clone https://github.com/PaddlePaddle/PaddleX.git
# Use a proxy in China
$git clone https://gh-proxy.org/https://github.com/PaddlePaddle/PaddleX.git

$cd Paddlex
$uv venv -p 3.12
$source .venv/bin/activate
# Check the official docs for the GPU version matching your setup
$uv pip install paddlepaddle-gpu==3.3.0 --index-url https://www.paddlepaddle.org.cn/packages/stable/cu126/
$uv pip install -e ".[base]"
$uv pip install pip
# Install the plugin
$paddlex install PaddleOCR


# Install labelme for image annotation
$uv tool install labelme

# Put the images to train on in the images directory
$mkdir -p layout-project/images

# Generate pre-annotations; supports "l" and "plus_l" models
$./ppx train layout l --dir layout-project

$cd layout-project & labelme images --labels label-l.txt --output labelme-l

# Run training
$./ppx train layout l --dir layout-project --device gpu:0  --export-onnx

$./ppx train layout l --dir layout-project 

```

---

## FAQ

### Does PPX support password-protected PDFs?

Not currently. Strip the password with a tool like `qpdf` before passing the file to PPX.

### How do I resolve `opencv` version conflicts?

Uninstall all existing opencv variants first, then reinstall:

```bash
uv pip uninstall opencv-python opencv-contrib-python \
                  opencv-python-headless opencv-contrib-python-headless
uv pip install opencv-contrib-python --no-config
```

### `ImportError: libGL.so.1` on Linux servers

Install the headless OpenCV variant instead:

```bash
uv pip install opencv-python-headless
```

Or install the system library: `sudo apt-get install -y libgl1`

### Can `onnxruntime` and `onnxruntime-gpu` coexist?

No. Install exactly one. The GPU variant must match your system's CUDA version.

### Can I use PPX on Mac with GPU acceleration?

No. Neither Apple Silicon nor Intel Macs support CUDA. The CPU backend works on both.

### Can I embed PPX in a commercial product?

Not under the default license. PPX is free for personal, research, and noncommercial use. For commercial use, contact `contact@memect.co`.

### How do I parse only specific pages?

```bash
ppx parse report.pdf --pages "1-5,10,15-20"
```

---

## Product Experience

Web experience for pdf2x: <https://pdf2x.cn/>

[Apply for a free API key](https://pdf2x.cn/api/apikey/page) to call the API.

Mini Program experience:

![pdf2x Mini Program code](docs/images/pdf2x.jpg)

---

## Contributing

We welcome bug reports, feature requests, and pull requests.

1. Fork the repository and create a feature branch.
2. Run tests: `uv run pytest`
3. Submit a PR — please describe the motivation and include test cases.

See [CONTRIBUTING.md](CONTRIBUTING.md) for full guidelines.

---

## Star History

<a href="https://www.star-history.com/?repos=memect%2Fmemect-ppx&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=memect/memect-ppx&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=memect/memect-ppx&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=memect/memect-ppx&type=date&legend=top-left" />
 </picture>
</a>

---

## License

PPX is released under the [PolyForm Noncommercial License 1.0.0](LICENSE).

PPX is free for personal, research, and noncommercial use. For commercial use, contact `contact@memect.co`.

For bundled third-party code and assets, see [NOTICE](NOTICE) and [docs/THIRD_PARTY_LICENSES.md](docs/THIRD_PARTY_LICENSES.md). Those files document attribution and redistribution review items for vendored components and bundled resources shipped with this repository.