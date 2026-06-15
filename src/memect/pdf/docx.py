

import importlib
import importlib.resources
import io
from pathlib import Path

from .base import KDocument, KFigure, KFormula, KTable, KText




from docx.document import Document
from docx.shared import Pt, RGBColor,Cm
from docx.text.paragraph import Paragraph
from docx.oxml import parse_xml, OxmlElement
from docx.oxml.ns import nsdecls, nsmap, qn
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ROW_HEIGHT_RULE,WD_ALIGN_VERTICAL
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING,WD_TAB_ALIGNMENT
from docx.enum.section import WD_ORIENTATION, WD_SECTION_START
from docx.section import Section

class DocxBuilder:
    def __init__(self):
        super().__init__()
    
    def build(self,doc:KDocument)->bytes:
        #TODO 按页解析和安章节树解析不一样
        fp = io.BytesIO()
        
        for page in doc.pages:
            if page.skipped:
                #插入空白页
                pass
            else:
                for obj in page.objects:
                    if isinstance(obj,KText):
                        #可以使用固定的字体大小，或者计算一个大概的
                        
                        pass

                    elif isinstance(obj,KFigure):
                        pass
                    elif isinstance(obj,KFormula):
                        pass
                    elif isinstance(obj,KTable):
                        pass
                    else:
                        pass
                pass
        
        return fp.getvalue()

class Docx:
    def __init__(self):
        super().__init__()
        self.doc = Document()
    
    def add_section(self):
        pass

    def add_footnotes(self):
        pass

    def render(self, out_file: str | Path):
        from docx import Document
        template = importlib.resources.files(
            __package__).joinpath('template.docx').read_bytes()
        #比默认的模版缺少Title style等，但是可以让生成的文档使用word打开没有显示兼容模式
        doc = Document(io.BytesIO(template))
        self._set_styles(doc)
        #默认已经存在一个section，python-docx在添加新的section，会按照xml的要求，自动调整到合适的位置，如：
        #<p><sectPr></sectPr> => 这个影响前面的，如果需要添加一个新的section，就变成
        #<p/><p><sectPr/></p> [新section的内容] <sectPr/> =>新创建的在这里，然后新的内容
        for i, section in enumerate(self.sections):
            # TODO 可能已经存在一个section了
            if i > 0 or len(kdoc.sections) == 0:
                #如果是连续的节，如：一个是不分栏，另外一个是分栏，那么，下一个节不需要分页
                #TODO 如果是因为分栏需要使用新的节，那么页眉页脚还是使用之前的？并不需要重新设置
                #如果是因为header/footer等不同，需要使用分页，那么就需要创建新的header/footer
                page_break=True
                #NEW_PAGE: 分节且开始新的一页
                #NEW_COLUMN: 分节且开始开始新的栏，如：原来是2栏的，新的节也是2栏的，就在原来的最后一栏继续，通常在报纸排版中使用
                #CONTINUOUS: 分节且在同一页继续
                #ODD_PAGE: 分节表示开始新的一页，如果不是奇数页，就添加空白，在书籍打印中使用，因为是双面打印，确保章节的开始为奇数页
                #EVEN_PAGE: 分节表示开始新的一页，确保在偶数页
                kdoc.add_section(WD_SECTION_START.NEW_PAGE if page_break else WD_SECTION_START.CONTINUOUS)

            section.render(kdoc)

        out_file = Path(out_file)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(out_file))
    
    def _set_styles(self,doc:Document):
        style = doc.styles['Normal']
        style.font.name = 'Times New Roman' # 必须先设置font.name
        style.element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')
