# PPX — 高精度 PDF / 图片解析工具

<p align="center">
  <img src="docs/images/logo.png" alt="PPX Logo" width="200" />
</p>

[![PyPI version](https://img.shields.io/pypi/v/memect-ppx.svg)](https://pypi.org/project/memect-ppx/)
[![Python](https://img.shields.io/badge/python-%3E%3D3.12-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-LGPL--3.0-blue)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-linux%20%7C%20macOS%20%7C%20docker-lightgrey)](https://github.com)

[English](README.md) | 简体中文

---

## 简介

**PPX** 是一款高精度文档解析工具，可将 PDF、图片等格式转化为结构化的机器可读内容。它同时支持传统 OCR 引擎与多种大语言模型（LLM）后端，适用于金融报告、学术论文、扫描件等各类文档的批量处理场景。

## 功能特性

- **多格式支持**：PDF、PNG、JPG 及图片目录批量处理
- **多后端引擎**：内置 OCR 引擎，同时支持 DeepSeek-OCR、PaddleOCR-VL、GLM-OCR 等大模型后端
- **CPU / GPU 双模式**：按需选择，支持 CUDA 加速
- **灵活配置**：命令行参数、环境变量、本地配置文件三种方式任意组合
- **Docker 支持**：提供完整的 Docker Compose 一键部署方案
- **目录批量解析**：一次命令解析整个目录下的所有 PDF 和图片

## 环境要求

| 组件            | 版本要求                                 |
|-----------------|------------------------------------------|
| Python          | >= 3.12（Mac Intel 须 <= 3.13）          |
| CUDA（可选）    | 与所选 `onnxruntime-gpu` 版本一致        |
| Docker（可选）  | >= 23                                    |

## 安装

### 方式一：通过 PyPI 安装（推荐）

```bash
# 创建虚拟环境（Python >= 3.12）
uv venv -p 3.12
source .venv/bin/activate

# 安装 OpenCV（必须）
# 如存在冲突，先执行：
# uv pip uninstall opencv-python opencv-contrib-python opencv-contrib-headless opencv-contrib-python-headless
uv pip install opencv-contrib-python --no-config

# CPU 版本
uv pip install memect-ppx
uv pip install onnxruntime --no-config

# GPU 版本（CUDA）
uv pip install memect-ppx[cuda]
uv pip install onnxruntime-gpu --no-config  # 必须安装
```

### 方式二：从源码安装

```bash
git clone <repo-url>
cd ppx
uv venv -p 3.12
source .venv/bin/activate

# 安装依赖
uv sync --no-install-project

# GPU 支持（可选）
uv sync --extra cuda

# 手动安装以下两个包（必须）
uv pip install opencv-contrib-python --no-config
uv pip install onnxruntime --no-config  # 或 onnxruntime-gpu
```

> **注意**：由于 `opencv`、`onnxruntime`、`torch` 可能被第三方库锁定不同版本，建议使用 `requirements.txt` 方式管理依赖，详见 [requirements.cpu.txt](requirements.cpu.txt) / [requirements.cuda.txt](requirements.cuda.txt)。

## 快速开始

### 基础解析

```bash
# 解析单个 PDF（自动判断是否使用 OCR）
./app parse a.pdf

# 强制使用 OCR
./app parse a.pdf --ocr yes

# 禁用 OCR
./app parse a.pdf --ocr no

# 解析图片
./app parse 1.png
```

### 批量处理

```bash
# 解析目录下所有 PDF 和图片
./app parse dir1

# 指定输出目录
./app parse dir1 -o dir2

# 将目录下所有图片视为连续多页整体解析
./app parse dir1 --images
```

### 使用大模型后端

PPX 支持通过大语言模型进行高精度 OCR 解析，默认兼容 OpenAI API 协议：

```bash
# 使用 DeepSeek-OCR
./app parse a.pdf --backend deepseek \
  --deepseek '{"base_url":"http://127.0.0.1:4000/v1","model":"deepseek-ocr-2","api_key":""}'

# 使用 PaddleOCR-VL
./app parse a.pdf --backend paddle \
  --paddle '{"base_url":"http://127.0.0.1:4001/v1","model":"paddleocr-vl","api_key":""}'

# 使用 GLM-OCR
./app parse a.pdf --backend glm \
  --glm '{"base_url":"http://127.0.0.1:4002/v1","model":"glmocr","api_key":""}'
```

### 本地配置文件

频繁使用时，建议将参数写入配置文件，避免每次重复输入：

```bash
mkdir conf

# 支持 .py 文件（settings={} 语法）或 .json 文件
# 参考 src/memect/conf/settings.custom.py
vi conf/settings.py
vi conf/log.py

# 配置写好后，只需指定 backend
ppx parse a.pdf --backend deepseek
```

也可通过命令行动态覆盖：

```bash
./app parse 1.pdf --set xx.xx.xx=1
./app parse 1.pdf --backend deepseek --url http://127.0.0.1:4000/v1
```

## 启动大模型服务

PPX 大模型后端基于 [vLLM](https://github.com/vllm-project/vllm) 部署，以下为各模型启动命令：

### DeepSeek-OCR-2（约需 20G 显存）

```bash
vllm serve ./hub/deepseek-ai/DeepSeek-OCR-2 \
  --served-model-name deepseek-ocr-2 \
  --logits-processors vllm.model_executor.models.deepseek_ocr:NGramPerReqLogitsProcessor \
  --mm-processor-cache-gb 0 \
  --no-enable-prefix-caching \
  --port 4000 \
  --gpu-memory-utilization 0.8
```

### PaddleOCR-VL / PaddleOCR-VL-1.5（约需 10G 显存）

```bash
vllm serve ./hub/PaddlePaddle/PaddleOCR-VL \
  --served-model-name paddleocr-vl \
  --trust-remote-code \
  --max-num-batched-tokens 16384 \
  --no-enable-prefix-caching \
  --mm-processor-cache-gb 0 \
  --gpu-memory-utilization 0.5 \
  --port 4001
```

> 也可将 `PaddleOCR-VL` 替换为 `PaddleOCR-VL-1.5`，模型名与端口保持一致，无需修改配置。

### GLM-OCR（约需 10G 显存）

```bash
# 需要 transformers >= 5.3.0
uv pip install "transformers>=5.3.0"

vllm serve ./hub/ZhipuAI/GLM-OCR \
  --served-model-name glmocr \
  --max-num-batched-tokens 16384 \
  --max-model-len 16384 \
  --speculative-config '{"method": "mtp", "num_speculative_tokens": 1}' \
  --gpu-memory-utilization 0.5 \
  --port 4002
```

> GLM-OCR 模型地址：[ModelScope](https://modelscope.cn/models/ZhipuAI/GLM-OCR)

## Docker 部署

### Docker Compose（推荐）

```bash
cd x2x

# 构建所有镜像（TAG 默认为当天日期，如 20260324）
TAG=$(date +%Y%m%d) docker compose build

# 构建单个服务
TAG=$(date +%Y%m%d) docker compose build apiserver

# 推送到镜像仓库
TAG=20260324 docker compose push apiserver deepseek

# 其他节点拉取
TAG=20260324 docker compose pull apiserver deepseek

# 启动 apiserver
TAG=20260324 docker compose up apiserver

# 启动 apiserver + DeepSeek 模型
TAG=20260324 docker compose up apiserver deepseek
```

通过 `.env` 文件自定义配置：

```bash
cp .env.sample .env
# 编辑 .env 修改相关参数
```

### Docker Build（单独构建）

```bash
# docker >= 23；低于 23 请使用 docker buildx build
docker build --target apiserver -t x2x-apiserver .
docker build --target deepseek  -t x2x-deepseek  .
docker build --target paddle    -t x2x-paddle    .
docker build --target glm       -t x2x-glm       .
docker build --target llm       -t x2x-llm       .
```

### Docker Run（单独启动）

```bash
# 启动 apiserver（GPU 模式）
docker run --gpus all -it --rm -p 9527:9527 x2x-apiserver

# 启动 apiserver（CPU 模式）
docker run -it --rm -p 9527:9527 x2x-apiserver

# 启动大模型服务
docker run --gpus all -it --rm -p 4000:4000 x2x-deepseek
docker run --gpus all -it --rm -p 4001:4001 x2x-paddle
docker run --gpus all -it --rm -p 4002:4002 x2x-glm

# 挂载外部模型目录
docker run --gpus all -it --rm -p 4003:4003 \
  -v ./hub:/apps/llm/hub x2x-llm vllm serve ...
```

> **GPU 显存控制提示**：可通过 `--gpu-memory-utilization 0.5 -dp 2` 等参数调整显存占用。

## 常见问题

**Q: Mac Intel 平台能否使用 GPU 加速？**
> 不支持。Mac Intel 平台仅支持 CPU 模式，且 Python 版本须在 3.12 到 3.13 之间。

**Q: 如何解决 OpenCV 版本冲突？**

先卸载所有 opencv 相关包，再重新安装：

```bash
uv pip uninstall opencv-python opencv-contrib-python opencv-contrib-headless opencv-contrib-python-headless
uv pip install opencv-contrib-python --no-config
```

**Q: onnxruntime 与 onnxruntime-gpu 能否共存？**
> 不能。请根据实际需求二选一安装，GPU 版本须与系统 CUDA 版本匹配。

## 许可证

本项目基于 [GNU Lesser General Public License v3.0 (LGPL-3.0)](LICENSE) 开源。

LGPL-3.0 允许将本项目作为库链接到你的应用中（包括商业应用），但对本项目自身代码的修改须以相同协议开源。
