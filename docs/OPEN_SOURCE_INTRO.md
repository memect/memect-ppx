# PPX 开源介绍

## 一句话介绍

PPX 是一款面向真实文档场景的开源 PDF / 图片解析引擎，支持将输入文档转换为结构化 Markdown 和 JSON，并尽可能保留文本、表格、图形、公式和版面结构信息。

它既可以作为本地命令行工具使用，也可以作为 Python 库或服务端能力嵌入到更大的文档理解系统中。

PPX 的中文表达借用了“皮皮虾”这个名字。我们希望它像皮皮虾一样，在复杂信息环境中高速感知、精准捕捉，并将混乱内容拆解为结构化数据。

开源地址：

- GitHub: <https://github.com/memect/memect-ppx>
- PyPI: <https://pypi.org/project/memect-ppx/>
- 产品站: <https://pdf2x.cn/>
- 小程序: `#小程序://PDF2x/fXZ4o2YV4BwU7Vg`

## 我们为什么开源 PPX

文档解析一直是很多知识处理、RAG、内容审核、归档检索、文档数字化场景中的基础能力，但在真实业务里，问题并不只是“把 PDF 转成文本”这么简单。

真正困难的地方在于：

- PDF 往往不是规则文本，而是混合了原生文本、扫描图、复杂表格、页眉页脚、脚注、图片、公式和多栏排版。
- 只抽取纯文本通常不够，很多下游任务需要结构化结果、对象级坐标和版面上下文。
- 实际部署环境差异很大，有的场景要求本地离线，有的场景追求最高精度，有的场景需要 GPU 加速和批处理能力。

PPX 的目标不是做一个“只能在演示样例上好看”的解析工具，而是提供一套更接近生产环境的、可组合、可扩展、可服务化的解析能力。

## PPX 能做什么

PPX 主要面向 PDF 和图片文档解析，输出 Markdown、JSON 等结构化结果，并保留页面坐标信息，便于后续做：

- 大语言模型应用中的文档预处理
- 知识抽取与文档理解
- RAG 文档预处理
- 企业知识库构建
- Agent 的文档接入与知识消费
- 表格结构识别与重建
- 学术论文、报告、合同、财报等复杂文档解析
- 文档服务化接入与批量处理

核心能力包括：

- 文本提取与 OCR 自动切换
- 图文混排文档解析
- 表格结构识别，保留 `colspan` / `rowspan`
- 公式提取并转为 LaTeX
- 页面对象级坐标输出
- 本地默认后端与多种 LLM 后端切换

## 设计思路

PPX 采用“默认本地解析 + 可选大模型增强”的双路径设计。

对于隐私敏感、离线部署、快速接入场景，可以直接使用默认本地后端；对于复杂版面、高精度识别场景，也可以切换到 DeepSeek-OCR、PaddleOCR-VL、GLM-OCR 等后端。

这种设计带来的价值是：

- 能适配不同部署约束，而不是强绑定某一种推理栈
- 能在成本、精度、速度之间做更现实的权衡
- 能让开发者先低门槛跑通，再逐步升级复杂能力

## 项目特点

### 1. 面向结构化结果，而不只是纯文本

PPX 输出的不只是可读文本，还包括页面对象、坐标、表格结构和版面信息，方便后续继续做索引、检索、渲染、对齐和可视化。

### 2. 默认可本地运行

项目默认支持 CPU 环境，不强制依赖远程服务。对于很多本地开发、受限环境或数据敏感场景，这一点很重要。

### 3. 支持多后端扩展

除了默认解析链路，PPX 也支持接入多种 OCR / 文档理解大模型后端，方便按需求切换精度和推理成本。

### 4. 更适合工程落地

项目提供命令行入口、Python API、服务化能力和配置机制，而不是单一的研究原型接口。

## 适合哪些场景

PPX 适合下面这些类型的工作：

- 做大语言模型应用和文档接入的团队
- 做文档理解、信息抽取、RAG 预处理的研发团队
- 需要解析复杂 PDF 的平台和基础设施团队
- 做企业知识库、知识问答、Agent 系统的团队
- 需要自动化处理报告、论文、合同、报表、扫描件的业务系统
- 希望搭建自有文档解析服务的开发者
- 关注 PDF 结构化输出、OCR、表格解析的开源社区用户

## 基础用法

### 1. 安装

使用 `uv`：

```bash
uv pip install memect-ppx onnxruntime opencv-contrib-python
```

使用 `pip`：

```bash
pip install memect-ppx onnxruntime opencv-contrib-python
```

### 2. 默认 pipeline 模式解析

PPX 默认使用 pipeline 模式，无需额外指定：

```bash
ppx parse <input_path> -o <output_path>
```

例如：

```bash
ppx parse report.pdf -o output/
```

### 3. 解析单个 PDF

```bash
ppx parse document.pdf
```

默认输出目录为 `document.pdf.out/`，其中通常包含：

- `doc.md`
- `doc.json`
- 页面图片与中间结果文件

### 4. 解析图片

```bash
ppx parse scan.png
```

### 5. 批量解析目录

```bash
ppx parse docs/
ppx parse docs/ -o output/
```

### 6. 基本参数示例

```bash
# 自动判断是否需要 OCR
ppx parse report.pdf

# 强制 OCR
ppx parse report.pdf --ocr yes

# 跳过 OCR
ppx parse report.pdf --ocr no

# 指定页码范围
ppx parse report.pdf --pages "1-5,10,15-20"
```

### 7. 使用大模型后端

```bash
ppx parse report.pdf --backend deepseek
ppx parse report.pdf --backend paddle
ppx parse report.pdf --backend glm
```

待补充素材：

- [待补充] 一份公开可分发的示例输入文档
- [待补充] 一张输入与输出结果对比图
- [待补充] 一段 API 服务化调用示例

## Benchmark 与评测说明

PPX 的 benchmark 评测说明、结果、引用与合规边界已整理到
[BENCHMARKS.md](BENCHMARKS.md)。

## 为什么值得关注

我们认为 PPX 的价值不只在于“又一个 PDF parser”，而在于它尝试把文档解析这件事做得更接近真实工程需求：

- 既考虑本地部署，也考虑大模型增强
- 既考虑解析效果，也考虑输出结构和下游可用性
- 既提供 CLI，也提供库接口和服务化能力

如果你需要的不只是“把 PDF 变成一段字符串”，而是把文档变成可继续处理、可检索、可消费的结构化数据，PPX 会更有价值。

## 当前阶段

PPX 当前已经开放基础能力，并正在持续完善开源化工作，包括：

- 文档和安装体验整理
- 第三方资源与许可证边界梳理
- Benchmark 结果补充
- 测试与发布流程完善
- 社区协作规范建设

待补充素材：

- [待补充] 首个公开版本号
- [待补充] 开源发布日期
- [待补充] GitHub Star / 下载量 / 使用案例

## 欢迎参与

如果你正在做下面这些事情，欢迎关注和参与 PPX：

- 大语言模型应用中的文档处理
- 复杂文档解析
- OCR 与版面分析
- 表格理解与结构恢复
- 文档到 Markdown / JSON 的工程落地
- 文档理解系统、知识库系统、RAG 基础设施、Agent 文档接入

你可以通过以下方式参与：

- 提交 Issue 反馈问题和需求
- 提交 Pull Request 改进能力和文档
- 补充公开可分发的测试样例
- 分享你在真实场景中的使用方式和效果

## 素材补充清单

正式对外发布前，建议补充下面这些内容：

- [待补充] 一段更适合传播的短标题
- [待补充] 一张主视觉或解析结果示意图
- [待补充] 2 到 3 个真实应用场景案例
- [待补充] benchmark 结果截图和摘要表
- [待补充] 一段简短的团队/项目背景介绍
- [待补充] 发布时希望重点强调的差异化能力

## 可直接复用的短文案

### 版本 A：社区帖 / GitHub 动态

我们开源了 PPX，一个面向真实文档场景的 PDF / 图片解析引擎。它支持将文档转换为结构化 Markdown / JSON，保留文本、表格、图形、公式和页面坐标信息，既可本地运行，也可接入 DeepSeek-OCR、PaddleOCR-VL、GLM-OCR 等后端。适合文档理解、RAG 预处理、复杂 PDF 解析和服务化接入场景。

### 版本 B：群内转发 / 简介

PPX 是一个开源 PDF / 图片解析工具，目标是把复杂文档转换成更适合下游系统消费的结构化结果，而不只是抽一段文本。支持本地后端和多种大模型后端，适合做知识库、文档理解和 RAG 预处理。

### 版本 C：发布页标题副本

把复杂 PDF 和图片文档，转换成结构化 Markdown / JSON。

## 相关文档

- [README_zh-CN.md](../README_zh-CN.md)
- [README.md](../README.md)
- [TECH.md](TECH.md)
- [OPEN_SOURCE_CHECKLIST.md](OPEN_SOURCE_CHECKLIST.md)
- [RELEASE_BRANCH_FLOW.md](RELEASE_BRANCH_FLOW.md)
- [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)
