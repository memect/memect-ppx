from typing import Any, Final, Sequence

from memect.base.bbox import BBox
from memect.pdf.base import KChar, KColor, KDocument, KFigure, KFormula, KObject, KPage, KRect, KSpan, KTable, KText, KTextline, TableIntent
from memect.pdf.x.xbase import XBlock, XFigure, XFormula, XSection, XTable, XText
from .document import Document
from .model import Paragraph, Section, SectionMargins, TableCell
from .units import pt, twip


"""
页眉和页脚是和节（section）关联的

每一节都可以有自己独立的页眉页脚，从新开始，或者使用不同的style

或者所有的节都适用相同的页眉页脚

一个节可以设置了
纸张大小: 不会继承上一节的，如果没有设置，使用schema的默认
方向（竖直或者横版）：
页边距：不会继承
页眉页脚：如果没有设置，就使用上一节的

节和节之间，可以为
continue，也就是不换页
nextPage：换页
odd/even：偶数或者奇数页开始新的节，主要使用在书籍排版中，因为书籍是双面打印，这样可以确保某些节的第一页总是为奇数还是偶数
          开启这个，是全局的（也就是整个文档，而不是仅仅对于当前节）
          然后在每一个节中，奇数页选择“default”，偶数页选择“even”，所以，如果没有就会出现错误
          所以，如果开启了这个，section就需要设置even header/footer

在word中，添加新的节，默认是链接上一节的页眉页脚，

在一节当中，页眉页脚可以如下设置：
（如果没有设置，就表示使用前一节的设置）
首页不同： 第一页可以设置独立的页眉页脚，或者没有
奇偶页不同：可以设置2套页眉页脚
页码格式
页码开始序号：续前节（和前面的节一起排序）
            或者从新排序
"""

class _XRun:
    def __init__(self):
        super().__init__()
        self.chars:list[KChar]

    def bold(self)->bool:
        pass

    def italic(self)->bool:
        pass

    def underline(self)->bool:
        pass

    def strikeout(self)->bool:
        pass

    def fontsize(self)->float:
        pass

    def color(self)->str:
        pass

class _Styles:
    def __init__(self):
        super().__init__()
        self._table_styles:dict[str,Any]={}
    
    def parse(self,doc:KDocument):
        xtree = doc.tree
        assert xtree is not None

        #使用style，便于文档二次编辑
        #每个表格设置自己的style，便于实现，二次编辑不方便
        #表格的style千变万化，典型的有这几种
        #1.
        #表头使用指定的颜色（可以没有表头）
        #其他行使用交替颜色
        #2.
        #看似为交替，但是又不全是，如：
        #---red---
        #---blue--
        #---red---
        #---red---  
        #---blue
        #---red--

        #现在如下：
        #前面几行颜色相同，理解为表头
        #后面的严格交替，可以作为一个style
        
        for node in xtree.root.flat():
            if node.is_table() and not node.table.is_layout():
                #如果是用来布局的，忽略
                xtable = node.table
                
                for ktable in xtable.tables:
                    cells = ktable.get_row(0)
                    #如果这一行的颜色都是相同的，如果是来自ocr，相近就可以？
                    for cell in cells:
                        color = cell.color or KColor.WHITE
                    pass
                pass

        for page in doc.working_pages:
            for table in page.objects:
                if isinstance(table,KTable):
                    pass
    
    def _parse_table(self,table:KTable):
        #获得表头的颜色
        #是否行交替
        #是否列交替
        #其他复杂的就不考虑了，如：每一行使用不同的颜色，或者某个单元格使用某些颜色
        #如果是来自pdf的，可以根据fill rect来获得？
        #如果是来自图片的，从视觉上获得颜色？
        
        header_style={
            'background':'#ffffff'
        }

        row_style={

        }

        col_style={}

        if table.header is not None:
            #表示为跨页表格的表头
            #1.没有使用颜色
            #2.使用特定的颜色
            #3.使用交替的颜色
            pass
        else:
            #
            pass
        pass

    def _get_table_colors(self,table:KTable):
        for i in range(table.row_num):
            #可能存在跨行的，都仅仅取第一行
            row = table.get_row(i)
            #严格的，应该是获得一个复杂的区域，因为存在跨行
            #使用shapely等来计算这个区域的面积
            pass
    def _get_area_color_by_pdf(self,page:KPage,bbox:BBox):
        if not page.pdf_rects:
            return None
        
        #可能为多个小矩形，使用简单的算法，就是颜色相同的矩形的面积相加，取最大面积
        rects = bbox.get(page.pdf_rects,ratio=0.5)
        if not rects:
            return None
        
        colors:dict[KColor,list[KRect]]={}
        for rect in rects:
            group = colors.setdefault(rect.color,[])
            group.append(rect)
        
        areas:list[tuple[Any,float]]=[]
        for rgba,group in colors.items():
            areas.append((rgba,sum(r.bbox.area for r in group)))
        #排序
        areas.sort(key=lambda item:item[1],reverse=True)
        color,area = areas[0]
        if area/bbox.area>=0.5:
            pass
        
        return None

    def _get_area_color_by_image(self,page:KPage,bbox:BBox):
        #从视觉上获得指定区域的颜色
        pass

    
class DocxBuilder:
    def __init__(self):
        super().__init__()

    def build(self, kdoc: KDocument) -> bytes:
        doc = Document(title="", creator="memect")
        #为了让生成的docx可以方便的修改，使用style的方式，而不是每一个段落都设置字体的字体（family+size）等
        doc.set_default_font(font='Calibri',east_asia_font='宋体',size=pt(10.5),color='000000')
        #base_on:继承
        #next：当在word中，输入“回车”，会新建一个paragraph，然后新的paragraph就使用“next”的style
        doc.add_paragraph_style('',name='正文',size=pt(10))
        doc.add_paragraph_style('Heading1',name='标题1',size=pt(10),next_style='Normal')
        doc.add_paragraph_style('Heading2',name='标题2',size=pt(10),next_style='Normal')
        doc.add_paragraph_style('Heading3',name='标题3',size=pt(10),next_style='Normal')

        doc.add_paragraph_style('Header',name='页眉')
        doc.add_paragraph_style('Footer',name='页脚')
        doc.add_paragraph_style('Footnote',name='脚注')

        #全局计算表格的style，有多少个表格的style是一样的
        #表格的style
        #表头固定颜色，行交替
        if False:
            doc.add_table_style('',name='',header_shading='',banded_rows=('',''))
            #表头固定颜色，列交替
            doc.add_table_style('',name='',header_shading='',banded_columns=('',''))

        
        
        
        if kdoc.tree is not None:
            self._render_tree(kdoc, doc)
        else:
            # 按页
            pass

        return doc.to_bytes()

    def _render_tree(self, kdoc: KDocument, doc: Document):
        # 按节输出
        # 首页（没有页眉页脚）

        assert kdoc.tree is not None
        
        first = True
        for ksection in kdoc.tree.get_sections():
            self._render_xsection(doc, ksection)
            if first:
                # 把自动生成的删除
                first = False
                del doc.sections[0]

    def _render_xsection(
        self, doc: Document, xsection: XSection
    ):

        def is_A4(width: float, height: float) -> bool:
            # 595*842
            if 590 <= width <= 600 and 830 <= height <= 850:
                # 纵向
                return True
            elif 830 <= width <= 850 and 590 <= height <= 600:
                # 横向
                return True
            else:
                return False
        

        #下面这些，每一节都必须设置，因为不会继承，如果没有设置，使用schema默认值
        #纸张大小: 不会继承上一节的，如果没有设置，使用schema的默认
        #方向（竖直或者横版）：portrait or landscape(横向)
        #页边距：不会继承
        orientation = 'portrait'
        #在这个section中，所有的页面都一致的，如：都是A4
        kpage = xsection.xobjects[0].pages[0]
        if is_A4(kpage.width,kpage.height):
            #可能有个点误差，使用标准的A4纸大小即可
            if kpage.width<kpage.height:
                #pt(595)*pt(842)
                #直接使用twip更加准确一点
                orientation='portrait'
                page_width=twip(11906)
                page_height=twip(16838)
            else:
                orientation='landscape'
                page_width=twip(16838)
                page_height=twip(11906)
        else:
            if kpage.width<kpage.height:
                orientation='portrait'
            else:
                orientation='landscape'
            page_width = pt(kpage.width)
            page_height = pt(kpage.height)
        #设置4周留空
        # 如果是A4纵向，典型的为
        # top/bottom=72,left/right=90
        # top/bottom=72,left/right=54
        # top/bottom=72,left/right=144
        # top/bottom=36,left/right=36
        # 如果是A4横向，典型的也如上

        #现在使用统一的页边距，即使原文有所不同
        margins = SectionMargins()
        margins.left=pt(90)
        margins.right=pt(90)
        margins.top=pt(72)
        margins.bottom=pt(72)
        section = doc.add_section(start=xsection.start, columns=xsection.col_num,page_width=page_width,page_height=page_height,orientation=orientation,margins=margins)

        #True表示为第一节
        is_first = kpage.number==1
        if kpage.number==1 and not kpage.header.objects and not kpage.footer.objects:
            #首页，没有页眉页脚，或者使用自己的页眉页脚？
            section.first_page_different=True
            #section.first_header
            #section.first_footer
            pass

        #如果需要100%一致，可以每个节都设置自己的header/footer，因为有些文档是复制粘贴过来的，很乱
        #现在不追求，只使用一个，也就是仅仅在第一节设置
        if is_first:
            section.header.add_paragraph('测试页眉',alignment='right')
            section.footer.add_paragraph('测试页脚',alignment='left')
            section.set_page_numbering(format='decimal')
            #可以根据原文推理一个，或者就使用一个固定的
            section.add_page_number(position='footer',template='第{page}页',alignment='center')

        #如果是目录章节
        is_toc=False
        if is_toc:
            #添加一个目录即可，在word中打开后，根据实际自动更新
            #如果根据原文生成目录，意义不大
            #如果还有图文目录
            section.add_toc(title='目录',levels=(1,3))
            section.add_table_of_figures(title='图目录',sequence='Figure')
            section.add_table_of_figures(title='表目录',sequence='Table')
        else:
            for xobj in xsection.xobjects:
                #在同一节当作，有时候也需要分页
                if xsection.is_page_break(xobj):
                    section.add_page_break()
                if isinstance(xobj, XText):
                    self._render_xtext(section, xobj)
                elif isinstance(xobj, XFigure):
                    self._render_xfigure(section, xobj)
                elif isinstance(xobj, XFormula):
                    self._render_xformula(section, xobj)
                elif isinstance(xobj, XTable):
                    self._render_xtable(section, xobj)
                elif isinstance(xobj,XBlock):
                    #??
                    pass

    def _render_xtext(self, sec: Section, xtext: XText):
        assert len(xtext.objects)>0

        #支持跨页/跨栏文本
        #如果使用简单的方式
        
        #字体：可以在每一个p/run中设置，或者全局设置默认的字体

        doc:Final[Document] = sec.document
        mode='simple'
        if mode=='simple':
            #一个段落即可，字体大小/颜色/粗体/斜体/下划线等，都统一
            if xtext.node.is_title() and xtext.no is not None:
                #表示为标题，判断是否有序号，可以添加序号
                #这个仅仅允许1-3级，而且自动设置style=Heading1-3
                sec.add_heading(xtext.text,level=1)
                sec.add_list_item('xxx')
            else:
                fontsize=10
                #首行缩进2个字符
                first_line_indent=pt(2*fontsize)
                p=sec.add_paragraph(first_line_indent=first_line_indent)
                p.add_run(xtext.text,font='',east_asia_font='',size=pt(fontsize),color='')
        else:
            #不需要按pdf原文的换行（因为这个有可能原文是主动换行，也有可能原文没有换行，是渲染的时候换行）
            #这里就不换行了
            #仅仅支持粗体/斜体/下划线/删除线
            #而且，也不打算支持太复杂的粗体/斜体/下划线/删除线混合方式
            #粗体：如果连续的字符为粗体，就设置为粗体，忽略其他的
            #斜体：同上
            #下划线：同上
            #删除线：同上
            #字体颜色：同上
            objs:list[KObject]=[]
            for obj in xtext.texts:
                for tl in obj.lines:
                    #在行之间，如果间距过大，补充空格？
                    objs.extend(tl.split())
            
            #然后为了简化，原来是按行，现在合并为一个，只要为连续，减少run的次数，后续作者修改页方便
            
            #为了避免在整个段落中，字体大小差异过大，这里使用统一的字体即可
            #如果是黑色，就不需要设置字体颜色了
            #如果对象的间距过大，补充空格？
            def fill_spaces(p:Paragraph,index:int,objs:Sequence[KObject]):
                if index==0:
                    return
                obj1 = objs[index-1]
                obj2 = objs[index]
                #如果为同一行，且间距过大
                n=int((obj2.bbox.x1-obj1.bbox.x0)//10)
                if n>0:
                    p.add_run(' '*n,size=fontsize)

            p=sec.add_paragraph()
            for i,obj in enumerate(objs):
                fill_spaces(p,i,objs)
                if isinstance(obj,KSpan):
                    span = obj
                    if not span.color.is_black():
                        #多数情况下都是黑色，就不需要设置了，这样可以统一修改全文style？
                        color=f'{span.color.rgba[0]:02x}{span.color.rgba[1]:02x}{span.color.rgba[2]:02x}'
                    else:
                        color=None
                    p.add_run(span.text,bold=span.bold,italic=span.italic,underline=span.underline,strike=span.strikeout,color=color)
                elif isinstance(obj,KFigure):
                    p.add_picture(doc.create_picture(obj.fullpath,width=pt(obj.bbox.width),height=pt(obj.bbox.height)))
                elif isinstance(obj,KFormula):
                    p.add_picture(doc.create_picture(obj.fullpath,width=pt(obj.bbox.width),height=pt(obj.bbox.height)))
                else:
                    pass
            groups:list[Any]=[]
            
            for group in groups:
                p.add_run('')
            



    def _render_xtable(self, sec: Section, xtable: XTable):

        def render_object(tc:TableCell,obj:KObject):
            if isinstance(obj,KText):
                tc.add_paragraph(obj.text)
            elif isinstance(obj,KFigure):
                tc.add_picture(obj.fullpath)
            elif isinstance(obj,KFormula):
                tc.add_picture(obj.fullpath)
            elif isinstance(obj,KTable):
                render_table(tc,obj)
            else:
                pass

        def render_table(parent:TableCell,ktable:KTable):
            cells:list[TableCell]=[]
            for cell in ktable.cells:
                tc = TableCell(width=pt(cell.bbox.width),row_index=cell.row_index,col_index=cell.col_index,row_span=cell.row_span,col_span=cell.col_span)
                cells.append(tc)
                for obj in cell.objects:
                    render_object(tc,obj)
                    
            
            parent.add_table(rows=ktable.row_num,cols=ktable.col_num,cells=cells,alignment='center')
        

        #获得表格的style
        #独立：表示每个单元格自己设置自己的
        #表头+交替颜色
        #交替颜色：
        
        cells: list[TableCell] = []
        for cell in xtable.cells:
            # 这个为逻辑上的bbox
            # cell.bbox
            if cell.bbox is not None:
                width = pt(cell.bbox.width)
                height = pt(cell.bbox.height)
            else:
                width=pt(50*cell.col_span)
                height=None
            
            tc = TableCell(
                row_index=cell.row_index,
                col_index=cell.col_index,
                row_span=cell.row_span,
                col_span=cell.col_span,
                width=width,
            )
            color = cell.color
            font_color = cell.font_color
            if color is not None:
                tc.set_shading(color.hex())
            cells.append(tc)
            #TODO 后续需要修改为使用xobjects，也是需要合并的，如：文本合并为一个
            for obj in cell.objects:
                render_object(tc,obj)
                    

        # TODO 还需要设置是否表头重复，跨页断行
        table = sec.add_table(rows=xtable.row_num, cols=xtable.col_num, cells=cells,alignment='center')
        if xtable.tables[0].header is not None:
            #表头重复
            table.set_repeat_header_rows(xtable.tables[0].header.row_num)
            #可以设置表头的颜色
            #table.set_header_shading()
        else:
            #不允许
            table.set_repeat_header_rows(0)
        
        #就是总是设置为跨页断行
        table.set_allow_row_break_across_pages(True)

        #设置表格列或者行使用交替颜色，为了简化或者统一修改，使用style的方式更好？
        #table.set_banded_rows('','')
        #table.set_banded_columns('','')



    def _render_xfigure(self, sec: Section, xfigure: XFigure):
        alignment = "center"
        if xfigure.bbox is not None:
            width = xfigure.bbox.width
            height = xfigure.bbox.height
        else:
            width = xfigure.objects[0].bbox.width
            height = xfigure.objects[0].bbox.height

        #TODO 如果图片是浮动的，需要使用anchor的方式，如：相对页面，相对段落，这会非常复杂，目前不支持
        #如果有多个图片并排，需要使用表格的方式，目前不支持，全部居中
        sec.add_picture(
            xfigure.fullpath, width=pt(width), height=pt(height), alignment=alignment
        )

    def _render_xformula(self, sec: Section, xformula: XFormula):
        if xformula.latex:
            # 渲染公式？
            pass
        else:
            pass

        if xformula.bbox is not None:
            width = xformula.bbox.width
            height = xformula.bbox.height
        else:
            width = xformula.objects[0].bbox.width
            height = xformula.objects[0].bbox.height
        
        alignment='center'
        sec.add_picture(xformula.fullpath, width=pt(width), height=pt(height),alignment=alignment)

    def _render_pages(self,doc: Document,kdoc:KDocument):
        for page in kdoc.pages:
            #每个页面都添加一个
            pass
        pass


    