import logging

from typing import Any, Final, Mapping


from memect.base.debug import XDebugger
from memect.base.utils import MyBaseModel
from memect.pdf.base import KDocument, TreeBackend
from .xbase import XTree
from .xgroup import XGroupParser
from .xtable import XTableParser
from .xtext import XTextParser


class LLMParserArgs(MyBaseModel):
    # or anthropic
    provider: str = "openai"
    model: str
    base_url: str
    api_key: str
    prompt: str
    temperature: float = 0
    max_tokens: int = 1024 * 4
    client: Mapping[str, Any] | None = None
    """可以配置特定的参数，参考:OpenAI/Anthropic"""
    extras: Mapping[str, Any] | None = None
    """可以配置特定的参数，在调用的时候"""


class DefaultParserArgs(MyBaseModel):
    extra_patterns: list[str] = []
    exclude_texts: list[str] = []
    chapter_indices: list[int] | None = None
    llm: LLMParserArgs | None = None
    """可选LLM，对规则候选做二次语义判断"""


class XTreeParserArgs(MyBaseModel):
    llm: Mapping[str, Any] | None = None
    default: Mapping[str, Any] | None = None
    toc:Mapping[str,Any]|None=None
    outline:Mapping[str,Any]|None=None
    
    text:Mapping[str,Any]|None=None
    table:Mapping[str,Any]|None=None
    group:Mapping[str,Any]|None=None

class XParser:
    def __init__(self):
        super().__init__()
    
    def parse(self,xtree:XTree):
        pass
class XTreeParser:
    _logger = logging.getLogger(f"{__module__}.{__qualname__}")
    _debugger = XDebugger(f"{__module__}.{__qualname__}")

    def __init__(self, args: Mapping[str, Any] | XTreeParserArgs):
        super().__init__()
        self._args: Final = XTreeParserArgs.create(args)
        self._text_parser = XTextParser()
        self._table_parser = XTableParser()
        self._group_parser = XGroupParser()
        #self._parsers: Final[dict[TreeBackend, XParser]] = {}
        #self._lock: Final = threading.RLock()

    def parse(self, doc: KDocument):
        debugger: Final = self._debugger.bind()
        xtree = XTree(doc)
        doc.tree=xtree
        # 跨页/跨栏文本合并
        self._text_parser.parse(xtree)
        # 跨页/跨栏表格合并
        self._table_parser.parse(xtree)
        # 引用等的处理，也就是把某些局部内容先分成一个组，不需要再细分
        self._group_parser.parse(xtree)

        #轻量级对象，每次生成？
        if doc.params.tree.backend==TreeBackend.DEFAULT:
            from .xtree_default import Parser
            Parser(self._args.default).parse(xtree)
        elif doc.params.tree.backend == TreeBackend.LLM:
            from .xtree_llm import Parser
            Parser(self._args.llm).parse(xtree)
        elif doc.params.tree.backend==TreeBackend.OUTLINE:
            from .xtree_outline import Parser
            Parser(self._args.outline).parse(xtree)
        elif doc.params.tree.backend==TreeBackend.TOC:
            from .xtree_default import Parser
            Parser(self._args.toc).parse(xtree)
        else:
            pass

        xtree.root.setup_ids()
        
        if debugger.allow("save"):
            doc.write("debug/xtree.txt", xtree.root.stringify())


        

