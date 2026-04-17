#!python/bin/python
import importlib
import os
from pathlib import Path
import threading
from typing import Final


def get_nvidia_lib_paths() -> list[str]:
    # CUDA 相关包
    nvidia_modules = [
        "nvidia.cuda_runtime",
        "nvidia.cudnn",
        "nvidia.cublas",
        "nvidia.cufft",
        "nvidia.curand",
        "nvidia.cuda_nvrtc",
    ]
    paths:list[Path]=[]
    for n in nvidia_modules:
        try:
            m=importlib.import_module(n)
            if m.__file__:
                #xxx/xx/__init__.py
                #xxx/xx/lib
                paths.append(Path(m.__file__).parent.joinpath('lib'))
            else:
                #namespace
                #xxx/xx
                #xxx/xx/lib
                paths.append(Path(m.__path__[0]).joinpath('lib'))
        except ImportError:
            pass

    for n in ['tensorrt_libs']:
        try:
            m=importlib.import_module(n)
            if m.__file__:
                #xxx/__init__.py
                #xxx/xx.so
                paths.append(Path(m.__file__).parent)
        except ImportError:
            pass

    return [str(p.resolve()) for p in paths if p.is_dir()]


_lock:Final=threading.Lock()
_done=False
def set_to_env():
    """设置nvidia的库路径到LD_LIBRARY_PATH"""
    global _done
    with _lock:
        if not _done:
            _done=True
            old_path = os.environ.get('LD_LIBRARY_PATH')
            path:str=os.pathsep.join(get_nvidia_lib_paths())
            if old_path:
                path=f'{path}{os.path.pathsep}{old_path}'
            os.environ['LD_LIBRARY_PATH']=path
            #print(f'LD_LIBRARY_PATH={path}')

if __name__ == "__main__":
    paths = get_nvidia_lib_paths()
    print(os.pathsep.join(paths))