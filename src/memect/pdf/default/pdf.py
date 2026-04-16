from enum import StrEnum, auto
from typing import Any, Final, Mapping


from memect.base.utils import MyBaseModel
from memect.pdf.base import (
    KDocument,
)


class PdfProvider(StrEnum):
    PDF_OXIDE = auto()
    PYMUPDF = auto()


class PdfParserArgs(MyBaseModel):
    # or pymupdf or pdfminer??
    provider: PdfProvider = PdfProvider.PYMUPDF
    params: Mapping[str, Any] | None = None


class PdfParser:
    def __init__(self, args: PdfParserArgs | Mapping[str, Any] | None = None):
        super().__init__()
        self._args = PdfParserArgs.create(args)
        self._provider: Final = self._args.provider

    def parse(self, doc: KDocument):
        # 使用pdf的解析，工作量在于是否需要和视觉保存一致，现在使用第三方的库，就需要考虑下面几个问题是否处理了
        # 1. 支持了clip path吗？在clip path外的内容不可见
        # 2. 一个cid对应多个字符如何处理，可能是连体字，也可能是返回形似但是codepoint不同的，需要归一化
        # 3. cid没有对应的unicode如何处理？

        # 上面这些情况可能仅仅在个别古老的pdf中（都是因为pdf制作工具bug太多导致），随着时间的推移，这些问题都不需要解决了
        # 如：在项目使用中，某个pdf的某个表格多提取了一个字符，这个字符被clip，或者不可见，就不应该存在。
        # doc.pdf_provider = self._provider
        if self._provider == PdfProvider.PYMUPDF:
            from .pdf_pymupdf import Parser
            Parser().parse(doc)
        elif self._provider == PdfProvider.PDF_OXIDE:
            from .pdf_pdfoxide import Parser
            Parser().parse(doc)
        else:
            raise ValueError(f"不支持的provider:{self._provider}")


