
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Final, Sequence

from memect.pdf.base import KDocument, KPage, VObject
from memect.pdf.model import ModelExecutor


class Parser:
    def __init__(self):
        super().__init__()
        self._table_det: Final = ModelExecutor.get("table_det")
        self._table_det_key: Final = "cache/default/table_det"
    
    def parse(self,doc:KDocument,*,max_workers:int=0):
        pass

    def parse_page(self,page:KPage):
        pass

    def parse_table(self,page:KPage,vobj:VObject):
        pass

    def _do(
        self, fn: Callable[[KPage], None], pages: Sequence[KPage], max_workers: int = 0
    ):
        if max_workers == 0:
            for page in pages:
                fn(page)
        else:
            # 在free-threaded后才真正使用多核心
            with ThreadPoolExecutor(
                max_workers, thread_name_prefix=fn.__name__
            ) as executor:
                for _ in executor.map(fn, pages):
                    pass