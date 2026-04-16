# memect-ppx 技术文档

## 项目简介

memect-ppx 是一个 PDF / 图片文档解析工具，将输入文件转换为结构化的 Markdown和JSON。支持本地模型（默认）和多种 LLM 后端（DeepSeek、PaddleOCR、GLM），适用于高精度文档理解场景。

- Python >= 3.12
- 包名：`memect-ppx`
- 入口：`src/ppx.py` → `src/memect/cli.py`

---

## 架构概览

```
用户输入（PDF / 图片）
        │
        ▼
   CLI（cli.py）
        │
        ▼
   Parser.parse(doc)
        │
   ┌────┴────────────────────────┐
   │                             │
DEFAULT（本地模型）        LLM 后端（deepseek/paddle/glm）
   │                             │
RapidLayout                  vllm 服务
RapidOCR / YOLO              HTTP API
TableCls / LLMTable
   │
   └──────────────┐
                  ▼
         KDocument / KPage / KObject
                  │
                  ▼
        输出：md / json
```

---

## 核心模块

### CLI（`src/memect/cli.py`）

三个子命令：

| 命令 | 说明 |
|------|------|
| `parse` | 解析 PDF 或图片，输出多种格式 |
| `start` | 启动 HTTP API 服务 |
| `pdf2image` | 将 PDF 转换为图片 |

`parse` 命令主要参数：

```
--backend   default / deepseek / paddle / glm
--ocr       yes / no / auto
--table     no / ybk / wbk / auto / llm
--pages     指定页码范围
--remove-watermark  去水印
--formula   是否识别公式（LaTeX）
--mode      page / tree / ppt
```

---

### 数据模型（`src/memect/pdf/base.py`）

#### 文档层次

```
KDocument
  └── KPage[]
        ├── pdf_chars: KChar[]       # PDF 原始字符（含坐标、字体、颜色）
        ├── vobjects: VObject[]      # 版面识别结果
        └── objects: KObject[]       # 最终解析对象
```

#### KObject 子类

| 类 | 说明 |
|----|------|
| `KChar` | 单字符，含坐标、字体、粗斜体、颜色 |
| `KTextline` | 文本行 |
| `KTextbox` | 文本块 |
| `KFigure` | 图片区域 |
| `KTable` | 表格，支持 colspan/rowspan，可解析 HTML/OTSL 格式 |
| `KFormula` | 公式，输出 LaTeX |
| `KLine` / `KRect` | 线条 / 矩形 |
| `KPageHeader` / `KPageFooter` / `KPageFootnote` | 页眉 / 页脚 / 脚注 |

#### VObject

版面识别模型（layout model）的原始输出，包含区域坐标和类型标签，后续转换为对应 `KObject`。

#### 枚举类型

```python
Backend:   DEFAULT / DEEPSEEK / PADDLE / GLM
OCRMode:   YES / NO / AUTO
TableMode: NO / YBK / WBK / AUTO / LLM
ParseMode: PAGE / TREE / PPT
PageType:  ...
```

---

### 解析流程（`src/memect/pdf/parser.py`）

`Parser.parse(doc: KDocument)` 执行步骤：

1. **去水印**（可选，`remove_watermark=True`）
2. **PDF 转图片**（调用 pdf2image）
3. **按 backend 分发**：
   - `DEFAULT` → `DefaultParser`：本地模型流水线
     - `RapidLayoutModel`：版面区域检测
     - `RapidOCRModel` / `YOLODetectModel`：OCR 识别
     - `TableClsModel` + `LLMTableModel`：表格结构解析
     - `AutoLayoutModel`：自动版面后处理
   - `DEEPSEEK` / `PADDLE` / `GLM` → 对应 LLM 解析器，通过 HTTP 调用 vllm 服务
4. **输出**：`doc.md`、`doc.json`、`doc.docx`、`doc.pptx`、`doc.html`、`out.zip`

---

### 模型执行框架（`src/memect/pdf/model.py`）

#### Model（抽象基类）

- `execute()`：线程安全的公开接口
- `_execute()`：子类实现具体推理逻辑

内置模型：

| 模型类 | 用途 |
|--------|------|
| `RapidOCRModel` | 文字识别（CPU/GPU） |
| `RapidLayoutModel` | 版面区域检测 |
| `YOLODetectModel` | 目标检测（图片、表格等） |
| `YOLOClassifyModel` | 区域分类 |
| `LLMModel` | 通用 LLM 推理 |
| `LLMTableModel` | 表格结构 LLM 解析 |
| `TableClsModel` | 表格类型分类 |
| `AutoLayoutModel` | 版面自动后处理 |

#### ModelExecutor

- 支持多线程 / 多进程池
- 批处理（batch inference）
- 调度策略：FIFO / balance

#### ModelManager

统一管理多个 `ModelExecutor`，支持别名映射，供 `Parser` 按需调用。

---

## 安装

### CPU

```bash
uv pip install memect-ppx
uv pip install onnxruntime
```

### GPU（CUDA）

```bash
uv pip install "memect-ppx[cuda]"
uv pip install onnxruntime-gpu
```

---

## LLM 后端部署

通过 vllm 部署以下模型，各占独立端口：

| 后端 | 模型 | 显存需求 | 端口 |
|------|------|----------|------|
| `deepseek` | DeepSeek-OCR-2 | ~20G | 4000 |
| `paddle` | PaddleOCR-VL / PaddleOCR-VL-1.5 | ~10G | 4001 |
| `glm` | GLM-OCR | ~10G | 4002 |

使用示例：

```bash
ppx parse input.pdf --backend deepseek
ppx parse input.pdf --backend paddle --table llm
```

---

## Docker 部署

项目提供 `docker-compose.yml`，管理以下服务：

- `apiserver`：HTTP API 服务（对应 `ppx start`）
- `deepseek` / `paddle` / `glm`：各 LLM 推理服务

启动全部服务：

```bash
docker compose up -d
```

---

## HTTP API

`ppx start` 启动 REST API，接受与 CLI 相同的解析参数，返回解析结果（JSON 或文件流）。适用于服务化部署场景。

---

## 输出格式说明

| 格式 | 说明 |
|------|------|
| `.md` | Markdown，保留标题层级、表格、公式（LaTeX） |
| `.json` | 结构化 JSON，包含完整 `KDocument` 对象树 |
