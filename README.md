# 说明

## 安装

```bash
#>=3.12
$uv venv -p 3.12
$source .venv/bin/activate

#如果是在已经存在的环境中，建议先删除
#$uv pip uninstall opencv-python opencv-contrib-python opencv-contrib-headless opencv-contrib-python-headless
#$uv pip uninstall onnxruntime onnxruntime-gpu

#or opencv-contrib-python-headless
$uv pip install opencv-contrib-python --no-config

#cpu版本
$uv pip install memect-ppx
$uv pip install onnxruntime --no-config

#gpu版本
#安装依赖的cuda库，如果系统中已经全局安装，可以不安装，需要和onnxruntime-gpu的一致
#如果是其他版本，请根据onnxruntime-gpu的要求安装几个
$uv pip install memect-ppx[cuda]
#这个必须安装
$uv pip install onnxruntime-gpu --no-config


#安装方法二
$git clone 
$cd ppx
$uv venv -p 3.12
$uv sync --no-install-project
#如果需要使用gpu，如果系统中已经全局安装，可以不安装，或者安装另外的版本
$uv sync --extra cuda

#这两个必须手动安装
#or opencv-contrib-python-headless
$uv pip install opencv-contrib-python --no-config
#or onnxruntime-gpu 
$uv pip install onnxruntime --no-config



#默认解析
$./app parse a.pdf

#使用大模型解析，如果模型的base_url,model,api_key 和默认设置不一样，可以在命令行中指定，或者在./conf/settings.py中
$./app parse a.pdf --backend deepseek --deepseek '{"base_url":"http://127.0.0.1:4000/v1","model":"deepseek-ocr-2","api_key":""}'
$./app parse a.pdf --backend paddle   --paddle  '{"base_url":"http://127.0.0.1:4001/v1","model":"paddleocr-vl","api_key":""}'
$./app parse a.pdf --backend glm      --glm  '{"base_url":"http://127.0.0.1:4002/v1","model":"glmocr","api_key":""}'


#如果经常使用，可以写到配置文件中
$mkdir conf
#可以为json文件或者py文件: settings={}
#参考src/memect/conf/settings.custom.py 语法
$vi conf/settings.py
$vi conf/log.py

#如果在配置文件中写好了路径和模型等，就不需要在命令行再指定
$ppx parse a.pdf --backend deepseek
```

```bash
$git clone 
#mac intel，且需要使用onnxruntime等，只能够使用3.12<=python<=3.13
#linux或者mac m，使用>=3.12
$uv venv -p 3.12
#uv的其它操作，都是对默认的环境：.venv，即使激活了一个环境，也需要"--active"参数
#而pip操作，为了和pip兼容，会安装到激活环境，或者通过"-p",UV_PYTHON=xxx等来设置

#目前还是使用requirements.txt安装依赖更加方便，虽然uv支持了很多的冲突处理，但是
#还不够，还无法快速的解决多个同名包的冲突

$source .venv/bin/activate
$uv pip install requirements.txt
#因为opencv，onnxruntime，torch可能被第三方库指定了不同的版本，如：headless/contrib，cpu/gpu
#所以这里统一先清除，再安装
$uv pip uninstall -r requirements.uninstall.txt
#安装cpu或者cuda一个即可
$uv pip install -r requirements.cpu.txt
$uv pip install -r requirements.cuda.txt
$uv pip install -r requirements.opencv.txt

$./app start

#解析pdf，pdf+ocr（自动）
./app parse a.pdf

#解析pdf，总是使用ocr
./app parse a.pdf --ocr yes

#解析pdf，不使用ocr
./app parse a.pdf --ocr no

#解析图片
./app parse 1.png

#指定使用哪个llm：deepseek,paddle,glm，对于文本，没有每个字符的坐标
./app parse 1.pdf --backend deepseek

#常用配置可以通过命令行参数或者环境变量，或者本地文件，如：
#本地文件，目录为：./conf/settings.py, ./conf/log.py
#常用参数  
$./app parse 1.pdf --set xx.xx.xx=1 
$./app parse 1.pdf --backend deepseek --url http://127.0.0.1:/1111/v1
$./app parse 1.pdf --backend glm --url http://127.0.0.1:1111/v1
$./app parse 1.pdf --backend paddle --url http://127.0.0.1:1111/v1

#解析目录下的所有pdf或者图片，如果没有
$./app parse dir1 

#输出到这个目录
$./app parse dir1 -o dir2 

#表示dir1目录下的所有图片作为一个整体解析，也就是连续的多个页面
$./app parse dir1 --images 


```

## Docker Compose

```bash
#
$cd x2x
#日常操作就3个：build，up，push，其他用户：pull，up
#注意：这里是自动获得日期，如：20260324，也可以手动指定一个
#重新构建所有的镜像
$TAG=$(date +%Y%m%d) docker compose build
#apiserver,deepseek,paddle,glm
$TAG=$(date +%Y%m%d) docker compose build apiserver

#注意切换TAG为指定日前
#推送到hub
$TAG=20260324 docker compose push apiserver deepseek

#其他用户需要先pull
$TAG=20260324 docker compose pull apiserver deepseek

#启动apiserver
#注意：需要指定具体的版本（日期），否则会自动build
$TAG=20260324 docker compose up apiserver
#启动apiserver+deepseek
$TAG=20260324 docker compose up apiserver deepseek

#可以通过".env"修改设置，如果使用其它的文件名，需要--env-file ./.env.my
$cp .env.sample .env
```

## Docker Build

如果想单独操作单个docker的，通过"-t"可以指定名字，也可以使用和docker compose中的一致，
这里就使用简单的名字了，因为主要是本地测试，通过tag也可以方便修改

```bash
#docker>=23 如果是<23，使用docker buildx build 
$docker build --target apiserver -t x2x-apiserver .
#后面这些是可选择的，使用其它方式部署模型也可以，如果是使用docker compose，使用这些会更好
$docker build --target deepseek -t x2x-deepseek  .
$docker build --target paddle -t x2x-paddle  .
$docker build --target glm -t x2x-glm .

$docker build --target llm -t x2x-llm .

```

## Docker Run

```bash
#使用docker run 启动单个服务，可以把"--it"替换为"-d"
#--gpus "device=0"  => 容器仅仅可以看到0这个gpu，也就是nvidia-smi只看到0
#-e NVIDIA_VISIBLE_DEVICES=0 => 在容器限制，仅仅在docker下可用
#-e CUDA_VISIBLE_DEVICES=0   => 在应用层限制，实际使用上两张并没有什么区别，个人建议还是使用这个


#启动apiserver，有内部模型，可以指定使用gpu或者cpu
#默认的配置都是使用127.0.0.1
#方案1，使用host网络，不需要修改任何配置
#--network host
#方案2，创建网络，对于这种，建议使用docker compose完成了

$docker run --gpus all -it --rm -p 9527:9527 x2x-apiserver

#使用cpu
$docker run -it --rm -p 9527:9527 x2x-apiserver

#启动大模型，可以通过环境变量控制gpu的映射
#常用的命令参数，如：控制显存，同时启动多个实体
#--gpu-memory-utilization 0.5 -dp 2
$docker run --gpus all -it --rm -p 4000:4000 x2x-deepseek
$docker run --gpus all -it --rm -p 4001:4001 x2x-paddle
$docker run --gpus all -it --rm -p 4002:4002 x2x-glm
#可以把模型下载在外部，然后启动
$docker run --gpus all -it --rm -p 4003:4003 -v ./hub:/apps/llm/hub x2x-llm vllm serve ...

```

## 启动模型

```bash

#常用环境变量，可以附加在命令前面
$export CUDA_VISIBLE_DEVICES=0

#需要大概20G
$vllm serve ./hub/deepseek-ai/DeepSeek-OCR-2 --served-model-name deepseek-ocr-2 \
--logits-processors vllm.model_executor.models.deepseek_ocr:NGramPerReqLogitsProcessor \
--mm-processor-cache-gb 0 \
--no-enable-prefix-caching \
--port 4000 \
--gpu-memory-utilization 0.8 


#需要10G
$vllm serve ./hub/PaddlePaddle/PaddleOCR-VL \
  --served-model-name paddleocr-vl \
  --trust-remote-code \
  --max-num-batched-tokens 16384 \
  --no-enable-prefix-caching \
  --mm-processor-cache-gb 0 \
  --gpu-memory-utilization 0.5\
  --port 4001

#需要10G
#也可以启动1.5版本，模型名字，端口号等都一样，配置等就都不需要改变
$vllm serve ./hub/PaddlePaddle/PaddleOCR-VL-1.5 \
  --served-model-name paddleocr-vl \
  --trust-remote-code \
  --max-num-batched-tokens 16384 \
  --no-enable-prefix-caching \
  --mm-processor-cache-gb 0 \
  --gpu-memory-utilization 0.5\
  --port 4001

#https://modelscope.cn/models/ZhipuAI/GLM-OCR
#需要手动更新为这个，以后vllm的依赖大于这个就不需要手动了
$uv pip install "transformers>=5.3.0"
$vllm serve ./hub/ZhipuAI/GLM-OCR \
--served-model-name glmocr \
--max-num-batched-tokens 16384 \
--max-model-len 16384 \
--speculative-config '{"method": "mtp", "num_speculative_tokens": 1}' \
--gpu-memory-utilization 0.5 \
--port 4002
```
