# coding=utf-8
import json
import os
from pathlib import Path
from typing import Annotated, Any

import typer

from .base.config import get_settings
from .pdf.base import Backend,OCRMode, ParseMode, TableMode

app = typer.Typer()


def _parse_pages(pages: str | None) -> list[int]:
    if not pages:
        return []

    result: set[int] = set()
    for part in pages.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            result.update(range(int(a), int(b) + 1))
        else:
            result.add(int(part))
    return sorted(result)


def _set_device(cpu:bool,cuda:str|None|None=None,cann:str|None=None):
    if cpu:
        #强制使用cpu，即使当前支持gpu
        os.environ['PPX__FORCE_CPU']='true'
    
    if cuda:
        #指定使用哪个设备
        os.environ['CUDA_VISIBLE_DEVICES']=cuda
    
    if cann:
        # 设置CANN设备（华为昇腾）
        # 根据具体的深度学习框架设置
        os.environ['ASCEND_DEVICE_ID'] = cann
        # 或者对于某些框架：
        # os.environ['NPU_VISIBLE_DEVICES'] = cann

@app.command()
def start(
    host: Annotated[str | None, typer.Option(help="监听地址")] = None,
    port: Annotated[int | None, typer.Option(help="监听端口")] = None,
    cpu: Annotated[bool,typer.Option(help='强制使用cpu，即使当前有gpu')]=False,
    cuda:Annotated[str|None,typer.Option(help='指定使用哪些gpu，等同于CUDA_VISIBLE_DEVICES的设置')]=None,
    kvs: Annotated[
        list[str] | None, typer.Option("--set", help='如：--set server.host="0.0.0.0"')
    ] = None,
    log_kvs: Annotated[
        list[str] | None,
        typer.Option("--set-log", help='如：--set-log root.level="debug"'),
    ] = None,
) -> None:
    """启动服务"""
    from .app import App
    from .base.config import parse_kvs, setup
    _set_device(cpu,cuda=cuda)
    custom_settings = parse_kvs(kvs)
    custom_log_settings = parse_kvs(log_kvs)
    if host is not None:
        custom_settings["server.host"] = host
    if port is not None:
        custom_settings["server.port"] = port
    #自动配置日志
    setup(settings=custom_settings, log_settings=custom_log_settings)
    App.run()



@app.command()
def parse(
    file: Annotated[Path, typer.Argument(help="PDF 文件、图片文件或图片目录")],
    out_dir: Annotated[
        Path | None, typer.Option("-o", "--out-dir", help="输出目录")
    ] = None,

    as_doc:Annotated[bool,typer.Option(help='当file为目录且这个为true，表示为一个文档连续的页面，如：1.png,2.png,3.png')]=False,

    max_workers:Annotated[int,typer.Option("-w","--workers",help='如果指定的file目录，可以设置同时执行多少个，0表示不使用多进程执行')]=0,

    pages: Annotated[str | None, typer.Option(help="页码范围，如 1-3,5")] = None,
    backend:Annotated[Backend|None,typer.Option()]=None,

    deepseek:Annotated[str|None,typer.Option(help="")]=None,
    paddle:Annotated[str|None,typer.Option(help="")]=None,
    glm:Annotated[str|None,typer.Option(help="")]=None,

    mode:Annotated[ParseMode|None,typer.Option(help="")]=None,

    ocr:Annotated[OCRMode|None,typer.Option(help="")]=None,
    table:Annotated[TableMode|None,typer.Option(help="")]=None,

    #remove_watermark:Annotated[bool|None,typer.Option(help='设置是否需要清除水印')]=None,
    
    #all:Annotated[bool,typer.Option()]=None,
    docx:Annotated[bool|None,typer.Option(help="")]=None,
    pptx:Annotated[bool|None,typer.Option(help="")]=None,
    md:Annotated[bool|None,typer.Option(help="")]=None,
    doc_json:Annotated[bool|None,typer.Option('--json',help="")]=None,

    cpu: Annotated[bool,typer.Option(help='强制使用cpu，即使当前有gpu')]=False,
    cuda:Annotated[str|None,typer.Option(help='指定使用哪些gpu，等同于CUDA_VISIBLE_DEVICES的设置')]=None,

    #如果修改个别参数，通过--set --set-log 会简便
    #如果修改多个参数，通过./conf/settings.py,./conf/log.py
    kvs: Annotated[
        list[str] | None, typer.Option("--set", help='如：--set server.host="0.0.0.0"')
    ] = None,
    log_kvs: Annotated[
        list[str] | None,
        typer.Option("--set-log", help='如：--set-log root.level="debug"'),
    ] = None,
    conf:Annotated[Path,typer.Option(help='自定义的配置目录')]=Path('./conf'),
    dev: Annotated[bool|None, typer.Option(help="开发模式，保存中间结果和使用缓存结果，如果两次之间参数改变过大，建议删除缓存")] = None,
    debug:Annotated[bool,typer.Option('-x','--debug',help='输出调试信息和调试图片等')]=False,
    params_text: Annotated[
        str | None, typer.Option("--params", help="解析参数，JSON 字符串")
    ] = None,
    params_file: Annotated[
        Path | None, typer.Option(help="解析参数文件，JSON 文件")
    ] = None,
    dry:Annotated[bool,typer.Option(help='表示仅仅测试设置参数等，不执行')]=False
) -> None:
    """解析 PDF 文件"""
    from .base.config import setup,parse_kvs
    from .base.debug import XDebugger
    from .base.utils import kill_child_processes
    from .pdf.base import KDocumentFactory,ParseParams
    from .pdf.parser import Parser
    from .base.utils import console

    def set_custom_values(settings:dict[str,Any],text:str|None,prefix:str):
        if not text:
            return
        data = json.loads(text)
        for k,v in data.items():
            settings[f"{prefix}.{k}"]=v
    
    custom_settings:dict[str,Any]={}
    log_custom_settings:dict[str,Any]={}
    if kvs:
        custom_settings.update(parse_kvs(kvs))
    if log_kvs:
        log_custom_settings.update(parse_kvs(log_kvs))
    
    #常用的设置，更加简便
    set_custom_values(custom_settings,deepseek,'pdf_parser.deepseek.model')
    set_custom_values(custom_settings,paddle,'pdf_parser.paddle.model')
    set_custom_values(custom_settings,glm,'pdf_parser.glm.model')

    _set_device(cpu,cuda=cuda)

    setup(settings=custom_settings,conf_dir=conf)

    if debug:
        XDebugger.setup()
    
    params = ParseParams.create(params_file or params_text)
    if dev is not None:
        params.dev = dev
    if backend is not None:
        params.backend = backend
    
    if mode is not None:
        params.mode = mode
        
    if pages:
        params.pagenos = _parse_pages(pages)
    
    #if remove_watermark is not None:
        #params.remove_watermark=remove_watermark
    
    if ocr is not None:
        params.ocr = ocr
    if table is not None:
        params.table = table

    if pptx is not None:
        params.pptx = pptx
    
    if docx is not None:
        params.docx = docx
    
    if md is not None:
        params.markdown=md
    if doc_json is not None:
        params.doc_json=doc_json
    


    #表示为多个文件，需要并行吗？可能需要比较多的内存
    def get_docs(dir_:Path):
        if dir_.is_file():
            yield KDocumentFactory(dir_,params,out_dir)
        elif dir_.is_dir() and as_doc:
            #表示为一个文档连续的页面，如：1.png,2.png,3.png
            yield KDocumentFactory(dir_,params,out_dir)
        else:
            for file in dir_.iterdir():
                if file.is_file() and file.name[0]!='.' and file.suffix.lower() in ('.pdf','.png','.jpg','.jpeg','.webp','.bmp'):
                    file_out_dir=None
                    if out_dir is not None:
                        #表示输出到这个目录，为了统一，同样添加".out"
                        file_out_dir = out_dir.joinpath(file.name+'.out')
                    yield KDocumentFactory(file,params,file_out_dir)
                else:
                    pass
    #考虑到文件数不会太多，为了获得总数，使用list
    if dry:
        docs = list(get_docs(file))
        console.print(params)
        console.log(f'共需要解析:{len(docs)}')

    else:
        try:
            Parser.batch(list(get_docs(file)),max_workers=max_workers)
        finally:
            pass
            #if max_workers>0:
                #kill_child_processes(os.getpid(),timeout=5)




@app.command()
def pdf2image(
    file: Annotated[Path, typer.Argument(help="PDF文件")],
    out_dir: Annotated[
        Path | None, typer.Option("-o", "--out-dir", help="输出目录")
    ] = None,
    pages: Annotated[str | None, typer.Option(help="页码范围，如 1-3,5")] = None,
    scale: Annotated[int | None, typer.Option(help="设置scale")] = None,
    max_size: Annotated[
        str | None, typer.Option(help="设置最大宽度和高度，如：2000，或者2000,10000")
    ] = None,
    chunk_size: Annotated[
        int | None, typer.Option(help="设置每批的大小，如：10")
    ] = None,
    use_job: Annotated[
        bool, typer.Option(help="设置使用job的方式执行，主要是测试作用")
    ] = False,
    dev: Annotated[bool, typer.Option(help="开发模式，跳过pdf2image")] = False,
):
    
    pass


@app.command()
def test(
    dir:Annotated[Path,typer.Argument(help='测试该目录下的pdf或者图片')],
    url:Annotated[str|None,typer.Option(help='设置请求的url，默认使用当前环境的')]=None,
    max_workers:Annotated[int,typer.Option(help='同时执行多少个')]=5
):
    """测试api"""
    from memect.base.config import setup
    from memect.base.test import Tester
    setup()
    tester = Tester(url=url,max_workers=max_workers)
    tester.run(dir)


@app.command(help='提前下载好需要的模型，方便docker制作')
def download():
    #ocr
    #layout

    pass

def main() -> None:
    from .nvidia_path import set_to_env
    set_to_env()
    app()


if __name__ == "__main__":
    main()
elif __name__ == "__mp_main__":
    print("mpx")
