from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Sequence

from PIL import ImageFont

from memect.pdf.base import (
    KBlock,
    KChar,
    KColor,
    KDocument,
    KFigure,
    KFont,
    KFormula,
    KLine,
    KObject,
    KPage,
    KRect,
    KSpan,
    KTable,
    KText,
)

from pptx.presentation import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.slide import Slide
from pptx.text.text import TextFrame
from pptx.util import Pt

import pptx_ea_font


@dataclass(frozen=True)
class _TextRun:
    text: str
    font: KFont
    color: KColor
    bold: bool
    italic: bool
    underline: bool
    size: float


@dataclass(frozen=True)
class _TextLine:
    runs: tuple[_TextRun, ...]
    advance: float | None = None


class PPTXBuilder:
    def __init__(self):
        super().__init__()
        self._measure_font_cache: dict[tuple[str, int, int], ImageFont.FreeTypeFont] = {}
    
    def build(self,doc:KDocument)->bytes:
        from pptx import Presentation as Factory
        prs = Factory()
        # pptx的slide必须使用同一的size，不能够使用不同的
        # 典型的就是A4纸，这里可以使用Inches，或者
        # Pt(566) Pt(842) = 8.27*72  11.69*72

        

        #正常的文档页面大小都是一样的，可能有些例外，如：横版了
        #如：两个页面，500*800，800*500 => 800*800
        #
        width = max(p.width for p in doc.pages)
        height = max(p.height for p in doc.pages)
        prs.slide_width = Pt(width)
        prs.slide_height = Pt(height)
        for page in doc.pages:
            self._build_page(prs, page)
        
        fp = BytesIO()
        prs.save(fp)
        return fp.getvalue()

    def _build_page(self, prs: Presentation, page: KPage):
        # 空白页面
        # 使用Pt单位，刚好和pdf的一致，都是1inch=72pt，就不需要使用inch单位了
        slide = prs.slides.add_slide(prs.slide_layouts[6])

        #可以先渲染了其他，获得对应的背景颜色？
        #因为表格使用了自定义的style，所以就需要把表格区域的line，rect去掉

        
        #self._render_block(slide,adjust_other())
        
        if page.header:
            #self._render_block(slide,page.header.body)
            pass
        
        if page.footnotes:
            for footnote in page.footnotes:
                #self._render_block(slide,footnote.body)
                pass
        if page.footer:
            #self._render_block(slide,page.footer.body)
            pass
        
        self._render_objects(slide,page.objects)

    def _render_objects(self,slide:Slide,objects:Sequence[KObject]):
        for obj in objects:
            if isinstance(obj, KText):
                self._render_text(slide,obj)
            elif isinstance(obj, KFigure):
                self._render_figure(slide,obj)
            elif isinstance(obj, KFormula):
                self._render_formula(slide,obj)
            elif isinstance(obj, KTable):
                self._render_table(slide,obj)
            elif isinstance(obj,KRect):
                self._render_rect(slide,obj)
            elif isinstance(obj,KLine):
                self._render_line(slide,obj)
            elif isinstance(obj,KBlock):
                #实际上不存在这个
                self._render_objects(slide,obj.objects)
            else:
                pass

    def _render_rect(self,slide:Slide,rect:KRect):
        x0,y0,x1,y1 = rect.bbox.transform(rect.page.to_lt())
        shape = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE,
            Pt(x0),Pt(y0),
            Pt(x1-x0),Pt(y1-y0)
        )
        shape.fill.solid()
        shape.fill.fore_color.rgb = self._to_color(rect.color)
        shape.line.fill.background()
    
    def _render_line(self,slide:Slide,line:KLine):
        x0,y0,x1,y1 = line.bbox.transform(line.page.to_lt())
        shape = slide.shapes.add_shape(
            MSO_SHAPE.LINE_INVERSE,
            Pt(x0),Pt(y0),
            Pt(x1-x0),Pt(y1-y0)
        )
        shape.line.color.rgb = self._to_color(line.color)
        shape.line.width = Pt(line.width)

    def _render_text(self, slide: Slide, text: KText,*,text_frame:TextFrame|None=None):
        x0,y0,x1,y1 = text.bbox.transform(text.page.to_lt())
        if text_frame is None:
            #如果没有，创建一个
            shape = slide.shapes.add_textbox(
                Pt(x0), Pt(y0),
                Pt(max(1, x1-x0)), Pt(max(1, y1-y0))
            )
            text_frame = shape.text_frame

        lines = self._collect_text_lines(text)
        self._prepare_text_frame(text_frame)
        text_frame.clear()
        self._prepare_text_frame(text_frame)
        if not lines:
            return

        scale = self._fit_text_scale(lines, max(1, x1-x0), max(1, y1-y0))
        for i,line in enumerate(lines):
            p = text_frame.paragraphs[0] if i==0 else text_frame.add_paragraph()
            p.space_before = Pt(0)
            p.space_after = Pt(0)
            p.alignment = PP_ALIGN.LEFT

            _, line_height = self._measure_line(line, scale)
            p.line_spacing = Pt(max(1, line_height))
            if i < len(lines)-1 and line.advance is not None:
                p.space_after = Pt(max(0, line.advance-line_height))

            for run_spec in line.runs:
                if not run_spec.text:
                    continue
                run = p.add_run()
                run.text = run_spec.text
                self._set_run_style(run, run_spec, scale)

    def _prepare_text_frame(self,text_frame:TextFrame):
        # 已有 text_frame 可能来自 table cell；统一清掉边距，避免版面和 PDF bbox 偏离。
        text_frame.margin_bottom=Pt(0)
        text_frame.margin_top=Pt(0)
        text_frame.margin_left=Pt(0)
        text_frame.margin_right=Pt(0)
        text_frame.vertical_anchor = MSO_ANCHOR.TOP
        text_frame.word_wrap = False
        text_frame.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE

    def _collect_text_lines(self,text:KText)->tuple[_TextLine,...]:
        if not text.lines:
            raw_lines = text.text.splitlines() or ([text.text] if text.text else [])
            if not raw_lines:
                return tuple()
            size = max(1, text.bbox.height / len(raw_lines) * 0.8)
            advance = text.bbox.height / len(raw_lines)
            return tuple(
                _TextLine(
                    (_TextRun(line,KFont.SANS_SERIF,KColor.BLACK,False,False,False,size),),
                    advance=advance if i < len(raw_lines)-1 else None,
                )
                for i,line in enumerate(raw_lines)
            )

        rendered_bboxes = [line.bbox.transform(line.page.to_lt()) for line in text.lines]
        result:list[_TextLine] = []
        for i,line in enumerate(text.lines):
            runs:list[_TextRun] = []
            for obj in line.split():
                if isinstance(obj,KSpan):
                    runs.append(self._span_to_run(obj))
                elif isinstance(obj,KChar):
                    runs.append(self._char_to_run(obj))
                else:
                    # 行内公式/图片暂时不塞入文本框；它们的独立坐标渲染后续单独处理。
                    pass

            if not runs:
                continue
            advance:float|None = None
            if i < len(rendered_bboxes)-1:
                next_y = rendered_bboxes[i+1].y0
                y = rendered_bboxes[i].y0
                if next_y > y:
                    advance = next_y-y
            result.append(_TextLine(tuple(runs),advance=advance))
        return tuple(result)

    def _span_to_run(self,span:KSpan)->_TextRun:
        text = ''.join(self._char_text(char) for char in span.chars)
        return _TextRun(
            text=text,
            font=span.font,
            color=span.color,
            bold=span.bold,
            italic=span.italic,
            underline=span.underline,
            size=max(1, span.bbox.height),
        )

    def _char_to_run(self,char:KChar)->_TextRun:
        return _TextRun(
            text=self._char_text(char),
            font=char.font,
            color=char.color,
            bold=char.bold,
            italic=char.italic,
            underline=char.underline,
            size=max(1, char.bbox.height),
        )

    def _char_text(self,char:KChar)->str:
        if char.font.wingdings:
            wingdings_text = getattr(char,'wingdings_text',None) or getattr(char,'wingding_text',None)
            if wingdings_text:
                return wingdings_text
        return char.text

    def _fit_text_scale(self,lines:Sequence[_TextLine],width:float,height:float)->float:
        if not lines:
            return 1

        def fits(scale:float)->bool:
            measured_width,measured_height = self._measure_block(lines,scale)
            return measured_width <= width+0.5 and measured_height <= height+0.5

        low = 0.05
        high = 1.0
        if fits(high):
            while high < 4:
                candidate = high * 1.25
                if not fits(candidate):
                    break
                low = high
                high = candidate
            else:
                return high
        elif not fits(low):
            return low

        for _ in range(16):
            mid = (low+high)/2
            if fits(mid):
                low = mid
            else:
                high = mid
        return low

    def _measure_block(self,lines:Sequence[_TextLine],scale:float)->tuple[float,float]:
        max_width = 0.0
        height = 0.0
        for i,line in enumerate(lines):
            line_width,line_height = self._measure_line(line,scale)
            max_width = max(max_width,line_width)
            if i < len(lines)-1:
                if line.advance is not None:
                    height += max(line_height,line.advance)
                else:
                    height += line_height*1.15
            else:
                height += line_height
        return max_width,height

    def _measure_line(self,line:_TextLine,scale:float)->tuple[float,float]:
        width = 0.0
        height = 0.0
        for run in line.runs:
            run_width,run_height = self._measure_run(run,scale)
            width += run_width
            height = max(height,run_height)
        return width,max(1,height)

    def _measure_run(self,run:_TextRun,scale:float)->tuple[float,float]:
        size = max(1,run.size*scale)
        if not run.text:
            return 0,size

        measure_scale = 4
        font = self._get_measure_font(run.font,run.bold,int(round(size*measure_scale)))
        x0,y0,x1,y1 = font.getbbox(run.text)
        ascent,descent = font.getmetrics()
        width = (x1-x0)/measure_scale
        height = max(y1-y0,ascent+descent)/measure_scale
        return width,max(size,height)

    def _get_measure_font(self,font:KFont,bold:bool,size:int)->ImageFont.FreeTypeFont:
        path,index = self._get_measure_font_path(font,bold)
        key = (str(path),index,size)
        if key not in self._measure_font_cache:
            self._measure_font_cache[key] = ImageFont.truetype(str(path),size=size,index=index)
        return self._measure_font_cache[key]

    def _get_measure_font_path(self,font:KFont,bold:bool)->tuple[Path,int]:
        from memect.pdf.fonts import get_font_dir
        font_dir = get_font_dir()
        if font.wingdings:
            if font.name == 'wingdings2':
                return font_dir/'wingdings2.ttf',0
            if font.name == 'wingdings3':
                return font_dir/'wingdings3.ttf',0
            return font_dir/'wingdings.ttf',0
        if font.serif:
            return font_dir/f'serif/SourceHanSerif-{"Bold" if bold else "Regular"}.ttc',2
        return font_dir/f'sans-serif/SourceHanSans-{"Bold" if bold else "Regular"}.ttc',2

    def _set_run_style(self,run,run_spec:_TextRun,scale:float):
        font_name = self._get_font(run_spec.font)
        font = run.font
        font.size = Pt(max(1,run_spec.size*scale))
        font.bold = run_spec.bold
        font.italic = run_spec.italic
        font.underline = run_spec.underline
        font.color.rgb = self._to_color(run_spec.color)
        pptx_ea_font.set_font(run,font_name)

    def _get_font(self,font:KFont|KText|None=None)->str:
        """返回 PPT 中使用的字体名，中文字体会由 pptx_ea_font 写入 eastAsia。"""
        if isinstance(font,KText):
            font = font.chars[0].font if font.chars else KFont.SANS_SERIF
        elif font is None:
            font = KFont.SANS_SERIF

        if font.wingdings:
            if font.name == 'wingdings2':
                return 'Wingdings 2'
            if font.name == 'wingdings3':
                return 'Wingdings 3'
            return 'Wingdings'
        if font.serif:
            return 'Source Han Serif SC'
        if font.monospace and font.name != 'ocr':
            return 'Consolas'
        return 'Source Han Sans SC'
    def _render_figure(self, slide: Slide, figure: KFigure):
        x0,y0,x1,y1 = figure.bbox.transform(figure.page.to_lt())
        slide.shapes.add_picture(
            str(figure.fullpath),
            Pt(x0), Pt(y0),
            Pt(x1-x0), Pt(y1-y0)
        )

    def _render_table(self, slide: Slide, table: KTable):
        x0,y0,x1,y1 = table.bbox.transform(table.page.to_lt())
        ptable = slide.shapes.add_table(
            rows=table.row_num, cols=table.col_num,
            left=Pt(x0), top=Pt(y0),
            width=Pt(x1-x0), height=Pt(y1-y0)
        ).table
        
        def get_x_axis()->list[float]:
            axis:list[float] = [None]*table.col_num
            for cell in table.cells:
                i = cell.col_index
                # j = cell.col_index+cell.col_span
                if axis[i] is None:
                    axis[i] = cell.bbox[0]
            axis.append(table.bbox[2])
            return axis

        #设置列的宽度，行的高度无法设置？
        x_axis = get_x_axis()
        for i in range(table.col_num):
            ptable.columns[i].width=Pt(x_axis[i+1]-x_axis[i])
        
        for cell in table.cells:
            pcell = ptable.cell(cell.row_index,cell.col_index)
            #透明
            #pcell.fill.background()
            #对于跨行跨栏的，只需要合并对角就可以
            if cell.col_span>1 or cell.row_span>1:
                pcell.merge(ptable.cell(cell.row_index+cell.row_span-1,cell.col_index+cell.col_span-1))

                    
            #cell.objects如何渲染到pcell
            for obj in cell.objects:
                if isinstance(obj,KText):
                    #文本还是在这里？
                    #remove_all_padding(pcell)
                    self._render_text(slide,obj,text_frame=pcell.text_frame)
                    #TODO 可以根据文本行判断，是否左对齐还是右对齐，还是居中对齐
                    for p in pcell.text_frame.paragraphs:
                        p.alignment=PP_ALIGN.CENTER
                    pcell.text_frame.vertical_anchor=MSO_ANCHOR.MIDDLE
                    
                elif isinstance(obj,KFigure):
                    self._render_figure(slide,obj)
                elif isinstance(obj,KFormula):
                    self._render_formula(slide,obj)
                else:
                    pass
                
    def _render_formula(self, slide: Slide, formula: KFormula):
        #可以根据latex渲染为图片，再插入，或者直接使用截图
        #使用渲染的图片可以更加清晰
        x0,y0,x1,y1 = formula.bbox.transform(formula.page.to_lt())
        # TODO 是否使用相对路径即可？
        slide.shapes.add_picture(
            str(formula.fullpath),
            Pt(x0), Pt(y0),      
            Pt(x1-x0), Pt(y1-y0)
        )

    def _to_color(self,color:KColor)->RGBColor:
        return RGBColor(color.rgba[0],color.rgba[1],color.rgba[2])



