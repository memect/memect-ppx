import importlib.util
import json
import logging
import logging.config
import os
import re
import signal
import threading
import tomllib
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, Final,cast


def load_py(filename: str | Path, *, name: str | None = None) -> ModuleType:
    """载入py文件"""
    filename = Path(filename)
    spec = importlib.util.spec_from_file_location(name or filename.stem, filename)
    if spec is None:
        raise ValueError(f"不能够载入module:{filename}")
    m = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise ValueError(f"不能够载入module:{filename}")
    spec.loader.exec_module(m)
    return m


def load_data(filename: str | Path, *, py_name: str = "settings") -> dict[str, Any]:
    filename = Path(filename)
    suffix: str = filename.suffix
    if suffix == ".json":
        return json.loads(filename.read_text("utf-8"))
    elif suffix == ".yaml":
        import yaml

        return yaml.safe_load(filename.read_text("utf-8"))
    elif suffix == ".toml":
        return tomllib.loads(filename.read_text("utf-8"))
    elif suffix == ".py":
        return getattr(load_py(filename), py_name)
    else:
        raise ValueError(f"不支持的文件格式:{filename}")


def _load_settings(default_file:str|Path,custom_settings:Mapping[str,Any]|None=None,custom_dir:Path=Path('./conf'))->Any:
    """先读取默认配置，然后再合并自定义配置，再合并来自命令行的配置"""
    from .utils import console
    default_file = Path(default_file)
    name = default_file.name.split('.')[0]
    data = load_data(default_file,py_name='settings')
    for custom_file in [custom_dir.joinpath(f'{name}.py'),custom_dir.joinpath(f'{name}.json')]:
        if custom_file.is_file():
            console.log(f'load custom config:{custom_file}')
            _set_values(data,load_data(custom_file,py_name='settings'))
            break

    #环境变量的设置，目前不支持，因为太多容易混乱，通过上面的自定义文件，或者命令行传递就可以解决

    #命令行的设置
    if custom_settings:
        _set_values(data,custom_settings)
    return data


def _set_values(
    data: MutableMapping[str, Any],
    values: Mapping[str, Any] | Sequence[tuple[str, Any]] | None,
    *,
    fn: Callable[[Any, Any], Any] | None = None,
    force: bool = True,
):
    if not values:
        return
    if isinstance(values, Mapping):
        items = values.items()
    else:
        items = values

    for k, v in items:
        if fn is not None:
            _set_value(data, k, fn=fn, force=force)
        else:
            _set_value(data, k, value=v, force=force)


def _set_value(
    data: MutableMapping[str, Any],
    key: str,
    *,
    value: Any = ...,
    fn: Callable[[Any, Any], Any] | None = None,
    force: bool = True,
):
    # 为了支持a."b.c".d => x['a']['b.c']['d']
    # 需要使用>=python3.12
    matchs = re.findall(r'([^".]+|(?P<q>")?[^"]+(?(q)"))[.]?', key)
    names: list[str] = []
    for m in matchs:
        m = m[0]
        if m[0] == '"' and m[-1] == '"':
            m = m[1:-1]
        names.append(m)
    # names = key.split('.')
    obj = data
    for name in names[:-1]:
        v: Any = obj.get(name, None)
        if not isinstance(v, MutableMapping):
            if force:
                v = {}
                obj[name] = v
                obj = v
            else:
                obj = None
                break
        else:
            obj = cast(MutableMapping[str, Any], v)

    if obj is None:
        return

    name = names[-1]
    if force or name in obj:
        if fn is not None:
            obj[name] = fn(obj.get(name, ...), value)
        else:
            obj[name] = value
        # TODO 表示删除？
        if obj[name] is ...:
            del obj[name]


class _KV:
    def parse(self, s: str) -> tuple[str, Any]:
        # a=1 or a="xx"
        m = re.fullmatch(r"(?P<name>.+?)=(?P<value>.*)", s)
        if not m:
            raise ValueError(f"错误的设置:{s}，不符合k=v形式")
        name = m.group("name")
        value: Any = m.group("value")
        name = name.strip()
        if value:
            value = value.strip()
        if value is None:
            # -s a
            value = True
        else:
            # -s a=  => '' 空字符串
            value = self._parse_value(value)
        return (name, value)

    def _parse_value(self, value: str) -> Any:
        if (
            value in ("true", "false", "null")
            or (value[0] == "{" and value[-1] == "}")
            or (value[0] == "[" and value[-1] == "]")
        ):
            # a=true or a=false or a=null or a={} or a=[]
            value = json.loads(value)
        else:
            # 为了简化字符串的输入，如果是a=1 这样的，就认为是int/float，如果是a=xyz，就认为是字符串，当然，如果是a="1"，肯定是字符串
            if value[0] == "'" and value[-1] == "'":
                # 命令行 a=\'x\' => a='x' => x 认为是字符串，虽然不是json格式
                # 命令行 a=\"x\" => a="x" => x 为json字符串
                value = value[1:-1]
            else:
                try:
                    # 命令行a=1 => a=1 => 1
                    # 命令行a=\"x\"x => a="x" => x
                    value = json.loads(value)
                except json.JSONDecodeError:
                    # 认为是字符串，不需要处理，如：
                    # a=xyz => xyz
                    pass
        return value


def parse_kvs(items: Sequence[str] | None) -> dict[str, Any]:
    """
    解析：['a.b=1','a.c=2'] => {'a.b':1,'a.c':2,'a."xy".c'}
    """
    if not items:
        return {}
    data: dict[str, Any] = {}
    kv = _KV()
    for item in items:
        k, v = kv.parse(item)
        data[k] = v
    return data


_state: Final[dict[str, Any]] = {
    "done": False,
    "custom_settings": None,
    "custom_log_settings": None,
    "env_prefix": None,
    "settings": {},
}
_lock:Final=threading.RLock()


def setup(
    settings: Mapping[str, Any] | None = None,
    log_settings: Mapping[str, Any] | None = None,
    conf_dir:str|Path=Path('./conf'),
    use_log:bool=True
):
    """设置初始化，如果已经初始化，不再执行，所以如果需要应用一些自定义的设置，必须先执行"""
    from .utils import console

    with _lock:
        if _state["done"]:
            return
        
        conf_dir = Path(conf_dir).resolve()

        #console.log("config setup")
        console.rule("start setup config")
        console.log(f"pid={os.getpid()}")
        console.log(f"cwd={os.path.abspath('.')}")
        console.log(f'use_log={use_log}')
        #console.log(f"env_prefix={env_prefix}")
        console.log(f"custom_settings={settings}")
        console.log(f"custom_log_settings={log_settings}")
        console.log(f'custom_dir={conf_dir}')

        # 然后获得环境变量，然后设置？
        _state["done"] = True
        #TODO
        import memect.conf
        default_conf_dir = Path(memect.conf.__file__).parent
        _state["settings"] = _load_settings(default_conf_dir/"settings.default.py", custom_settings=settings,custom_dir=conf_dir)
        # 设置日志
        if use_log:
            log_cfg = _load_settings(default_conf_dir/ "log.default.py", custom_settings=log_settings,custom_dir=conf_dir)
            logging.config.dictConfig(log_cfg)
        
        console.rule('end setup config')



def get_settings(name: str | None = None) -> Mapping[str, Any]:
    """获得设置，如果还没有载入设置，自动载入"""
    setup()
    if not _state["done"]:
        raise RuntimeError("还没有执行过setup()")
    if not name:
        return _state["settings"]
    return _state["settings"].get(name) or {}


class _F:
    def __init__[**P](self, fn: Callable[P, Any], *args: P.args, **kwargs: P.kwargs):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def __call__(self):
        return self._fn(*self._args, **self._kwargs)


class MPInit:
    """在多进程的时候，执行这个初始化，应用相同的配置"""

    def __init__(self, use_log: bool = True):
        super().__init__()
        #确保先初始化
        setup()
        global _state
        #self._env_prefix = _state["env_prefix"]
        self._custom_settings = _state["custom_settings"]
        self._custom_log_settings = _state["custom_log_settings"]
        self._use_log = use_log
        self._fn: Callable[[], Any] | None = None

    def set_fn[**P](self, fn: Callable[P, Any], *args: P.args, **kwargs: P.kwargs):
        """设置额外需要执行的操作，注意：需要支持序列化"""
        self._fn = _F(fn, *args, **kwargs)

    def __call__(self):
        # 在多进程下，也启用日志，可能会很慢
        setup(
            self._custom_settings,
            self._custom_log_settings,
            use_log=self._use_log
        )
        # 忽略ctrl+c，等待主进程关闭释放
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        # 主进程获得，子进程不会获得，除非直接kill子进程
        # signal.signal(signal.SIGTERM, lambda s,f: print(f"子进程 {os.getpid()} 收到 SIGTERM"))
        if self._fn:
            self._fn()


def usage():
    setup({}, {})
    get_settings()
