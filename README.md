# 说明

## 安装

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
#常用环境变量，可以附加在命令前面
$export CUDA_VISIBLE_DEVICES=0
#国内建议使用modelscope，下面的模型ID也是相对modelscope，huggingface的可能有所不同
$export VLLM_USE_MODELSCOPE=True

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

## Docker 部署

```bash
cp .env.sample .env
docker compose up -d --build
```

如果本机构建访问 Debian 官方源较慢，可只在构建阶段设置镜像源：

```bash
DEBIAN_MIRROR=https://mirrors.aliyun.com/debian \
PYPI_INDEX_URL=https://mirrors.aliyun.com/pypi/simple \
docker compose build ppx
```

CUDA 镜像默认把 `onnxruntime-gpu` 固定到 `PPX_ONNXRUNTIME_GPU_VERSION=1.23.2`，避免新版 wheel 需要 CUDA 13 动态库而当前安装的是 CUDA 12 Python 运行库。

GPU 服务需要宿主机已安装 NVIDIA Container Toolkit，可用 compose profile 启动：

```bash
COMPOSE_PROFILES=gpu PPX_GPU_IMAGE=hub.wenyinhulian.cn/docparser/ppx-gpu:260702 PPX_GPU_DEVICE_ID=0 docker compose up -d ppx-gpu
```

多卡机器上可用 `PPX_GPU_DEVICE_ID=0` 选择宿主机显卡。

正式 Linux GPU 镜像建议用脚本构建，默认产出 `hub.wenyinhulian.cn/docparser/ppx-gpu:<YYMMDD>`，例如 `hub.wenyinhulian.cn/docparser/ppx-gpu:260702`：

```bash
DEBIAN_MIRROR=https://mirrors.cloud.tencent.com/debian \
PYPI_INDEX_URL=https://mirrors.aliyun.com/pypi/simple \
scripts/build-gpu-image.sh
```

脚本同时会打一个本机便捷 alias `ppx:gpu`。发布到私有镜像仓库时可在 Docker 已登录后执行：

```bash
scripts/build-gpu-image.sh --push
```

需要固定 tag 时可显式指定：

```bash
PPX_IMAGE_TAG=260702 scripts/build-gpu-image.sh
```

默认启动一个 `ppx` API/Web 服务，容器内监听 `9527`，宿主端口由 `PPX_PORT` 控制。常用挂载目录：

- `PPX_DATA_DIR_HOST`：上传、任务和错误文件，默认 `./data`
- `PPX_LOG_DIR_HOST`：日志目录，默认 `./logs`
- `PPX_CONF_DIR_HOST`：可选配置目录，默认 `./conf`，需要覆盖默认配置时放 `settings.py`

常用运行变量：

- `PPX_LOG_LEVEL`：日志级别，默认 `INFO`
- `PPX_CPU`：强制 CPU，可设 `all`、`ocr`、`layout`、`table`、`formula`
- `PPX_FORMULA`：公式识别模型，默认 `formula-pp`，也可设 `formula-mfr`、`paddle`、`glm`

PDF 服务目录、文件限制、任务队列等业务配置不建议继续堆环境变量；按项目约定在 `conf/settings.py` 里只覆盖需要改的配置项。例如：

```python
settings = {
    "pdf_service.data_dir": "./data/pdf",
    "pdf_service.task_manager.max_running_size": 4,
    "pdf_service.pdf.max_page_count": 2000,
}
```

外部大模型服务通过 URL 配置：`PPX_DEEPSEEK_URL`、`PPX_PADDLE_URL`、`PPX_GLM_URL`、`PPX_BAIDU_URL`。如果模型服务跑在宿主机，Docker 内可使用 `http://host.docker.internal:<port>/v1`；如果跑在同一 compose 网络，改成对应服务名。

安全上不建议把该服务直接暴露到公网。当前 `/api/parse`、`/api/parse/state`、`/admin/gc` 等接口未做鉴权，生产环境应放在内网或反向代理鉴权之后。
