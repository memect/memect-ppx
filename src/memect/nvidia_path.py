#!python/bin/python
import importlib
import os
import sys
import threading
from pathlib import Path
from typing import Any, Final


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
    # Linux/macOS DLL 目录在 lib/，Windows 在 bin/（部分包还会有 lib/x64/）
    if sys.platform == "win32":
        subdirs = ("bin", "lib/x64", "lib")
    else:
        subdirs = ("lib",)

    paths: list[Path] = []
    for n in nvidia_modules:
        try:
            m = importlib.import_module(n)
            if m.__file__:
                # xxx/xx/__init__.py -> xxx/xx
                base = Path(m.__file__).parent
            else:
                # namespace package: xxx/xx
                base = Path(m.__path__[0])
            for sub in subdirs:
                paths.append(base.joinpath(sub))
        except ImportError:
            pass

    for n in ['tensorrt_libs']:
        try:
            m = importlib.import_module(n)
            if m.__file__:
                # xxx/__init__.py -> xxx/  (DLL/so 直接放在包根下)
                paths.append(Path(m.__file__).parent)
        except ImportError:
            pass

    return [str(p.resolve()) for p in paths if p.is_dir()]


_lock: Final = threading.Lock()
_done = False
# Windows os.add_dll_directory 返回的 cookie 必须保活，否则目录会从搜索路径中移除
_dll_cookies: list[Any] = []


def set_to_env():
    """让 NVIDIA 库目录可被加载器找到。

    Linux: 写入 LD_LIBRARY_PATH
    macOS: 写入 DYLD_LIBRARY_PATH
    Windows: 调用 os.add_dll_directory（Python 3.8+ 不再读 PATH 找 DLL）
    """
    global _done
    with _lock:
        if _done:
            return
        _done = True
        lib_paths = get_nvidia_lib_paths()
        if not lib_paths:
            return

        if sys.platform == "win32":
            for p in lib_paths:
                _dll_cookies.append(os.add_dll_directory(p))
            # 同时追加到 PATH：部分依赖通过 ctypes / 子进程查找时仍依赖 PATH
            old_path = os.environ.get("PATH", "")
            os.environ["PATH"] = os.pathsep.join(lib_paths + ([old_path] if old_path else []))
            return

        env_var = "DYLD_LIBRARY_PATH" if sys.platform == "darwin" else "LD_LIBRARY_PATH"
        old = os.environ.get(env_var)
        new = os.pathsep.join(lib_paths + ([old] if old else []))
        os.environ[env_var] = new


if __name__ == "__main__":
    paths = get_nvidia_lib_paths()
    print(os.pathsep.join(paths))