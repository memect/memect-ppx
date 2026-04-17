import math

from typing import Any, Final, Iterable, Literal, MutableSequence, NamedTuple, Sequence, cast, overload

from .matrix import Matrix


type _Axis=Literal['x','y']
type _BBox=Sequence[float]|'BBox'
type _Point=Sequence[float]|'Point'
type _Quad=Sequence[Sequence[float]]|'Quad'

class Point(NamedTuple):
    x: float
    y: float

    def transform(self, m: Sequence[float]|None) -> "Point":
        if m is None or tuple(m) == (1, 0, 0, 1, 0, 0):
            return self
        a, b, c, d, e, f = m
        return Point(
            round(a * self.x + c * self.y + e, 3),
            round(b * self.x + d * self.y + f, 3),
        )

    def distance(self, other:_Point) -> float:
        return math.hypot(self[0] - other[0], self[1] - other[1])

    def translate(self, dx: float, dy: float) -> "Point":
        return Point(self.x + dx, self.y + dy)


class BBox(tuple[float,float,float,float]):
    # 如果允许x0=x1,y0=y1，就存在area=0，也就是inter.area==0
    # 这个时候，iou=0
    # 但是，如果完全或者部分在内部，iou=0就不合理了，比如一条直线，简单的表示就是(x0,y0,x1,y1)，当然有笔宽，有面积
    # 但是在计算中，如水平线，就是(x0,y0,x1,y0)，垂直线就是(x0,y0,x0,y1)
    # 面积为0的
    # 所以，现在不允许x0==x1,y0==y1，如果是线，使用另外的对象

    def __new__(cls, x0: float, y0: float, x1: float, y1: float) -> "BBox":
        if x0 > x1:
            x0, x1 = x1, x0
        if y0 > y1:
            y0, y1 = y1, y0
        return super().__new__(cls, (x0, y0, x1, y1))

    @property
    def x0(self) -> float:
        return self[0]

    @property
    def y0(self) -> float:
        return self[1]

    @property
    def x1(self) -> float:
        return self[2]

    @property
    def y1(self) -> float:
        return self[3]

    @property
    def cx(self)->float:
        return (self[0]+self[2])/2

    @property
    def cy(self)->float:
        return (self[1]+self[3])/2
    
    @property
    def p1(self)->Point:
        return Point(self.x0,self.y0)
    
    @property
    def p2(self)->Point:
        return Point(self.x1,self.y0)
    
    @property
    def p3(self)->Point:
        return Point(self.x1,self.y1)
    
    @property
    def p4(self)->Point:
        return Point(self.x0,self.y1)

    @property
    def width(self) -> float:
        return self[2] - self[0]

    @property
    def height(self) -> float:
        return self[3] - self[1]

    @property
    def area(self) -> float:
        return self.width * self.height
    
    @property
    def area2(self)->float:
        """这个比area要宽容一些，当x0=x1或者y0=y1，会自动增加一个1来计算，即使为一条线或者一个点，都有面积而不会为0"""
        w = self.width
        h = self.height
        if w==0:
            w=1
        if h==0:
            h=1
        return w*h

    @property
    def center(self) -> Point:
        return Point((self[0] + self[2]) / 2, (self[1] + self[3]) / 2)

    @property
    def small(self) -> "BBox":
        """转换为整形，缩小，不建议用这个了，因为缩小就有可能x0=x1,y0=y1"""
        return BBox(int(self.x0), int(self.y0), int(self.x1), int(self.y1))

    @property
    def large(self) -> "BBox":
        """转换为整形，扩大"""
        x0, y0, x1, y1 = self
        return BBox(math.floor(x0), math.floor(y0), math.ceil(x1), math.ceil(y1))

    def to_int(self) -> "BBox":
        """转换为整形，扩大"""
        x0, y0, x1, y1 = self
        return BBox(math.floor(x0), math.floor(y0), math.ceil(x1), math.ceil(y1))

    def split(self, axis: _Axis, n: int | None = None, weights: Sequence[float] | None = None) -> list["BBox"]:
        """沿 axis 方向切分为多个 BBox。
        n 和 weights 只能设置一个：
        - n: 均分为 n 份
        - weights: 按比例切分，如 [1, 2, 1] 表示各占 1/4、2/4、1/4
        """
        assert (n is None) != (weights is None), "n 和 weights 只能设置一个"
        if weights is not None:
            total = sum(weights)
            ratios = [w / total for w in weights]
        else:
            assert n is not None and n >= 1
            ratios = [1 / n] * n

        result: list[BBox] = []
        acc = 0.0
        if axis == 'x':
            length = self.x1 - self.x0
            for r in ratios:
                x0 = self.x0 + acc * length
                x1 = self.x0 + (acc + r) * length
                result.append(BBox(x0, self.y0, x1, self.y1))
                acc += r
        else:
            length = self.y1 - self.y0
            for r in ratios:
                y0 = self.y0 + acc * length
                y1 = self.y0 + (acc + r) * length
                result.append(BBox(self.x0, y0, self.x1, y1))
                acc += r
        return result

    def expand(self,*,dx:float=0,dy:float=0,bound:_BBox|None=None)->"BBox":
        """调整，dx>0表示左右变大，dy>0表示上下变大，负值就是缩小，如果调整后不满足要求，就不调整了
        
        bound: 表示在放大后，不要溢出这个范围
        """
        if dx==0 and dy==0:
            return self
        
        x0, y0, x1, y1 = self
        if dx!=0:
            x0 -= dx
            x1 += dx
            if x0>=x1:
                #如果调整太多导致无效，就不调整
                x0=self.x0
                x1=self.x1

        if dy!=0:
            y0 -= dy
            y1 += dy
            if y0>=y1:
                y0=self.y0
                y1=self.y1

        bbox = BBox(x0, y0, x1, y1)
        if bound is not None:
            #指定了边界，确保调整后还在这个边界内
            bbox = bbox.intersect(bound)
            #原来就必须在bound内，缩小肯定也在内，放大，把溢出的裁剪而已
            assert bbox is not None
        return bbox

    def adjust(self,*,x0:float|None=None,x1:float|None=None,y0:float|None=None,y1:float|None=None,bound:_BBox|None=None) -> "BBox":
        """修改x0,y0,x1,y1的值，如果设置后不满足要求，就不调整了
        bound: 如果指定了，表示原来的bbox在这个范围内，调整后的bbox也限制在这个范围内
        """
        if x0 is None:
            x0=self.x0
        if x1 is None:
            x1 = self.x1
        if y0 is None:
            y0 = self.y0
        if y1 is None:
            y1 = self.y1
        if x0 <= x1 and y0 <= y1:
            bbox=BBox(x0, y0, x1, y1)
            if bound is not None:
                #没有调整之前的bbox就必须在bound，在调整后，确保不要溢出
                bbox = bbox.intersect(bound)
                assert bbox is not None
            return bbox
        else:
            # 就不调整了
            return self
    
    def set(self,*kvs:tuple[int,float])->"BBox":
        """设置新的值，如：set((0,1),(1,2)) 类似 bbox[0]=1,bbox[1]=2，只是返回一个新的"""
        a:list[float]=list(self)
        for k,v in kvs:
            a[k]=v
        return BBox(*a)

    def over(self,axis: _Axis,other:Sequence[float], /, *, d: float | None = None, d0: float = 0, d1: float = 0, min_len: float | None = None, max_len: float | None = None, strict: bool = True) -> bool:
        """判断a和b在x轴或者y轴上是否有重叠，或者说b经过a
        -------a
             --------b
        或者
        ----------a
           ---b
        或者
           ---a
        ----------b
        """
        a=self
        b=other
        if axis == 'x':
            i, j = 0, 2
        else:
            i, j = 1, 3

        if not BBox.check_length(axis, (a, b), min_len=min_len, max_len=max_len):
            return False

        if d is not None:
            d0 = d
            d1 = d

        if strict:
            # 如果是严格的
            k1 = a[j]-a[i]
            k2 = b[j]-b[i]
            d0 = min(d0, k1, k2)
            d1 = min(d1, k1, k2)

        if b[j] - a[i] >= d0 and a[j] - b[i] >= d1:
            # 使用>=0，表示重叠即使0也算over
            return True
        else:
            return False
    
    def align(self,axis:_Axis,other:_BBox,*,d:float=0)->bool:
        if axis=='x':
            x0,x1=0,2
        else:
            x0,x1=1,3
        return abs(self[x0]-other[x0])<=d and abs(self[x1]-other[x1])<=d

    def is_valid(self) -> bool:
        """判断是否有效的，strict=True表示不允许x0==x1,y0==y1"""
        return self.x1 > self.x0 and self.y1 > self.y0

    def is_h(self)->bool:
        """True表示为水平线"""
        #如果为一个点，width=0，height=0
        return self.width>=self.height
    
    def is_v(self)->bool:
        """判断是否为垂直线"""
        return self.height>self.width
    
    def transform(self, m: Sequence[float] | None) -> "BBox":
        if m is None or tuple(m) == (1, 0, 0, 1, 0, 0):
            return self
        pts = [
            Point(self.x0, self.y0).transform(m),
            Point(self.x1, self.y0).transform(m),
            Point(self.x1, self.y1).transform(m),
            Point(self.x0, self.y1).transform(m),
        ]
        return BBox(
            min(p.x for p in pts),
            min(p.y for p in pts),
            max(p.x for p in pts),
            max(p.y for p in pts),
        )

    def intersect(self, other:_BBox) -> "BBox | None":
        x0=max(self.x0, other[0])
        y0=max(self.y0, other[1])
        x1=min(self.x1, other[2])
        y1=min(self.y1, other[3])
        #为了支持线(x0=x1) or (y0=y1)，这里使用>，而不是>=
        if x0>x1 or y0>y1:
            return None
        else:
            return BBox(x0,y0,x1,y1)


    def union(self, other:_BBox) -> "BBox":
        return BBox(
            min(self.x0, other[0]),
            min(self.y0, other[1]),
            max(self.x1, other[2]),
            max(self.y1, other[3]),
        )

    def iou(self, other:_BBox) -> float:
        """"""
        inter = self.intersect(other)
        if inter is None:
            return 0.0
        other_area=BBox.from_list(other).area2
        return inter.area2 / (self.area2 + other_area - inter.area2)
    
    def contains(self, other:_BBox|_Point) -> bool:
        if isinstance(other, Point) or len(other)==2:
            return self.x0 <= other[0] <= self.x1 and self.y0 <= other[1] <= self.y1
        return (
            self.x0 <= other[0]
            and self.y0 <= other[1]
            and self.x1 >= other[2]
            and self.y1 >= other[3]
        )
    
    def get[T](self,objs:Sequence[T],*,ratio:float=1,exclude:Sequence[T]|None=None,strict:bool=True,remove:bool=False)->list[T]:
        """从objs中选择在该区域内的对象
        objs:[]
        ratio: 面积占比，1表示100%，表示对象有多少比例的面积在该区域内，如果只需要部分重叠，可以设置一个合适的比例
        strict: True表示如果有None对象或者None的bbox，抛出异常
        remove:True 表示同时删除，当这个为True，objs为list
        """
        assert ratio<=1
        
        results:list[T]=[]
        #如果为一条线，就不会包含任何东西
        area:Final = self.area
        if area==0:
            return results
        
        items:list[tuple[int,T,_BBox]]=[]
        for i,obj in enumerate(objs):
            if obj is None and strict:
                raise ValueError('')
            
            if exclude and obj in exclude:
                continue
            obj_bbox = BBox.get_bbox(obj,strict=strict)
            if obj_bbox is not None:
                items.append((i,obj,obj_bbox))
        if not items:
            return []
        
        def get_area(b:_BBox)->float:
            w=b[2]-b[0]
            h=b[3]-b[1]
            if w==0:
                w=1
            if h==0:
                h=1
            return w*h
        #y1排序
        items.sort(key=lambda item:item[2][3],reverse=True)
        removed_indexes:list[int]=[]
        for i,obj,obj_bbox in items:
            #不需要再继续
            if obj_bbox[3]<self.y0:
                break
            inter = self.intersect(obj_bbox)
            #使用最小区域，允许蛇吞象:min(obj_bbox.area2,area)
            if inter is not None and get_area(inter)/get_area(obj_bbox)>=ratio:
                results.append(obj)
                removed_indexes.append(i)
        
        if remove and removed_indexes:
            if not isinstance(objs,MutableSequence):
                raise ValueError(f'remove=True，objs必须为list对象，现在为:{type(objs)}')
            removed_indexes.sort()
            n=0
            for i in removed_indexes:
                del objs[i-n]
                n+=1
        return results
            

    def to_quad(self) -> "Quad":
        """转为 Quad（顺时针：左下→右下→右上→左上，PDF 坐标系）"""
        return Quad(
            Point(self.x0, self.y0),
            Point(self.x1, self.y0),
            Point(self.x1, self.y1),
            Point(self.x0, self.y1),
        )
    
    def to_tuple(self)->tuple[float,float,float,float]:
        return (self[0],self[1],self[2],self[3])
    
    def to_list(self)->list[float]:
        return list(self)
        
    def jsonify(self)->Any:
        return (self[0],self[1],self[2],self[3])

    @classmethod
    def from_points(cls, points: Sequence[_Point]) -> "BBox":
        assert len(points)>0
        return cls(
            min(p[0] for p in points),
            min(p[1] for p in points),
            max(p[0] for p in points),
            max(p[1] for p in points),
        )
    
    @classmethod
    def from_list(cls,bbox:Sequence[float],*,matrix:Matrix|None=None)->"BBox":
        if isinstance(bbox,BBox) and matrix is None:
            return bbox
        return cls(*bbox).transform(matrix)
    
    @classmethod
    def from_object(cls,obj:Any)->"BBox":
        bbox = cls.get_bbox(obj)
        if isinstance(bbox,BBox):
            return bbox
        else:
            return cls(*bbox)
    
    @overload
    @classmethod
    def join(cls,bboxes:Sequence[_BBox])->"BBox":
        ...

    @overload
    @classmethod
    def join(cls,bboxes:Sequence[_BBox|None],strict:bool=True)->"BBox|None":
        ...

    
    @classmethod
    def join(cls,bboxes:Sequence[_BBox|None],strict:bool=True)->"BBox|None":
        new_bboxes=[b for b in bboxes if b is not None]
        if not strict:
            if len(new_bboxes)==0:
                return None
        else:
            if len(new_bboxes)!=len(bboxes):
                raise ValueError('存在None的BBox')
            assert len(new_bboxes)>0
        bboxes = new_bboxes
        return cls(
            min(b[0] for b in bboxes),
            min(b[1] for b in bboxes),
            max(b[2] for b in bboxes),
            max(b[3] for b in bboxes),
        )

    @overload
    @classmethod
    def join2(cls,objs:Sequence[Any])->"BBox":
        ...

    @overload
    @classmethod
    def join2(cls,objs:Sequence[Any],*,strict:bool=True)->"BBox":
        ...

    @classmethod
    def join2(cls,objs:Sequence[Any],*,strict:bool=True):
        bboxes:list[_BBox|None]=[]
        for obj in objs:
            bbox = cls.get_bbox(obj,strict=strict)
            bboxes.append(bbox)
        return cls.join(bboxes,strict=strict)


    @staticmethod
    def check_length(axis: _Axis, bboxes: Iterable[_BBox], /, *, min_len: float | None = None, max_len: float | None = None) -> bool:
        """检查宽（x轴）或者高（y轴）是否满足要求"""
        if axis == 'x':
            i, j = 0, 2
        else:
            i, j = 1, 3

        for bbox in bboxes:
            if not BBox.check_range(bbox[j]-bbox[i], min_value=min_len, max_value=max_len):
                return False
        return True
    
    @staticmethod
    def check_range(value: float, min_value: float | None = None, max_value: float | None = None) -> bool:
        if min_value is not None and max_value is not None:
            if min_value > max_value:
                raise ValueError(
                    f'min_value={min_value},max_value={max_value}')
            return min_value <= value <= max_value
        elif min_value is not None:
            return value >= min_value
        elif max_value is not None:
            return value <= max_value
        else:
            return True
    
    @overload
    @staticmethod
    def get_bbox(obj:Any)->_BBox:
        ...

    @overload
    @staticmethod
    def get_bbox(obj:Any,strict:bool=True)->_BBox|None:
        ...
    
    @staticmethod
    def get_bbox(obj:Any,strict:bool=True)->_BBox|None:
        if isinstance(obj,BBox):
            b = obj
        elif isinstance(obj,Sequence):
            #这个就已经包括了BBox，但是分开判断还是更加明确一点
            b=cast(_BBox,obj)
        elif isinstance(obj,dict):
            b=cast(_BBox|None,obj['bbox'])
        else:
            b=cast(_BBox|None,obj.bbox) # type: ignore
        
        if strict and b is None:
            raise ValueError(f'存在为None的bbox,obj={obj}')
        return b


class Quad(tuple[Point,Point,Point,Point]):
    """
    四边形，四个顶点按逆时针（逆时针，PDF 坐标系 y 轴向上）：
        p4──p3
        |    |
        p1──p2
    
    如果转换为坐标原点为左上角，顺序就是
        p1──p2
        |    |
        p4──p3
    """

    def __new__(cls, p1: Point, p2: Point, p3: Point, p4: Point) -> "Quad":
        pts = [p1, p2, p3, p4]
        cx = sum(p.x for p in pts) / 4
        cy = sum(p.y for p in pts) / 4
        #pts.sort(key=lambda p: math.atan2(cy - p.y, p.x - cx))
        pts.sort(key=lambda p: math.atan2(p.y - cy, p.x - cx))
        return super().__new__(cls, pts)

    @property
    def p1(self) -> Point:
        """左下"""
        return self[0]

    @property
    def p2(self) -> Point:
        """右下"""
        return self[1]

    @property
    def p3(self) -> Point:
        """右上"""
        return self[2]

    @property
    def p4(self) -> Point:
        """左上"""
        return self[3]

    # ── 基本属性 ──────────────────────────────────────────────

    @property
    def points(self) -> tuple[Point, Point, Point, Point]:
        return (self[0], self[1], self[2], self[3])

    @property
    def center(self) -> Point:
        return Point(
            sum(p.x for p in self.points) / 4,
            sum(p.y for p in self.points) / 4,
        )

    @property
    def area(self) -> float:
        """Shoelace 公式"""
        pts = self.points
        n = 4
        return (
            abs(
                sum(
                    pts[i].x * pts[(i + 1) % n].y - pts[(i + 1) % n].x * pts[i].y
                    for i in range(n)
                )
            )
            / 2
        )

    @property
    def bbox(self) -> BBox:
        """最小外接矩形"""
        return BBox.from_points(list(self.points))

    @property
    def is_rect(self) -> bool:
        """是否为轴对齐矩形"""
        return (
            self.p1.x == self.p4.x
            and self.p2.x == self.p3.x
            and self.p1.y == self.p2.y
            and self.p3.y == self.p4.y
        )

    @property
    def width(self) -> float:
        """上边宽度（p1→p2）"""
        return self.p1.distance(self.p2)

    @property
    def height(self) -> float:
        """左边高度（p1→p4）"""
        return self.p1.distance(self.p4)

    @property
    def angle(self) -> float:
        """长边相对于水平方向的旋转角度（度），范围 [-180, 180]"""
        if self.width >= self.height:
            dx = self.p2.x - self.p1.x
            dy = self.p2.y - self.p1.y
        else:
            dx = self.p4.x - self.p1.x
            dy = self.p4.y - self.p1.y
        return math.degrees(math.atan2(dy, dx))

    # ── 几何操作 ──────────────────────────────────────────────

    def transform(self, m: Sequence[float] | None) -> "Quad":
        """PDF 仿射变换 [a b c d e f]"""
        if m is None or tuple(m) == (1, 0, 0, 1, 0, 0):
            return self
        return Quad(*(p.transform(m) for p in self.points))

    def split(self, axis: _Axis, n: int | None = None, weights: Sequence[float] | None = None) -> list["Quad"]:
        """沿指定方向切分为多个 Quad。
        axis='x' 时沿 p1→p2 方向切分；axis='y' 时沿 p1→p4 方向切分。
        n 和 weights 只能设置一个：
        - n: 均分为 n 份
        - weights: 按比例切分，如 [1, 2, 1] 表示各占 1/4、2/4、1/4
        """
        assert (n is None) != (weights is None), "n 和 weights 只能设置一个"
        if weights is not None:
            total = sum(weights)
            ratios = [w / total for w in weights]
        else:
            assert n is not None and n >= 1
            ratios = [1 / n] * n

        def lerp(a: Point, b: Point, t: float) -> Point:
            return Point(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t)

        result: list[Quad] = []
        acc = 0.0
        if axis == 'x':
            for r in ratios:
                t0, t1 = acc, acc + r
                result.append(Quad(
                    lerp(self.p1, self.p2, t0),
                    lerp(self.p1, self.p2, t1),
                    lerp(self.p4, self.p3, t1),
                    lerp(self.p4, self.p3, t0),
                ))
                acc += r
        else:
            for r in ratios:
                t0, t1 = acc, acc + r
                result.append(Quad(
                    lerp(self.p1, self.p4, t0),
                    lerp(self.p2, self.p3, t0),
                    lerp(self.p2, self.p3, t1),
                    lerp(self.p1, self.p4, t1),
                ))
                acc += r
        return result

    def translate(self, dx: float, dy: float) -> "Quad":
        return Quad(*(p.translate(dx, dy) for p in self.points))

    def scale(self, sx: float, sy: float|None = None) -> "Quad":
        sy = sy if sy is not None else sx
        cx, cy = self.center
        return Quad(
            *(Point(cx + (p.x - cx) * sx, cy + (p.y - cy) * sy) for p in self.points)
        )

    def rotate(self, angle_deg: float, origin: Point|None = None) -> "Quad":
        """绕指定点旋转（默认绕中心）"""
        ox, oy = origin or self.center
        θ = math.radians(angle_deg)
        cos_t, sin_t = math.cos(θ), math.sin(θ)

        def rot(p: Point) -> Point:
            dx, dy = p.x - ox, p.y - oy
            return Point(
                round(ox + dx * cos_t - dy * sin_t, 3),
                round(oy + dx * sin_t + dy * cos_t, 3),
            )

        return Quad(*(rot(p) for p in self.points))

    # ── 格式转换 ──────────────────────────────────────────────

    def to_list(self) -> list[list[float]]:
        return [[p.x, p.y] for p in self.points]

    def to_flat(self) -> tuple[float, ...]:
        return tuple(v for p in self.points for v in p)

    def to_bbox(self) -> BBox:
        return self.bbox
    
    # ── 工厂方法 ──────────────────────────────────────────────


    @classmethod
    def from_list(cls, pts: Sequence[Sequence[float]],*,matrix:Sequence[float]|None=None) -> "Quad":
        if matrix is None or tuple(matrix)==Matrix.identity:
            return cls(*(Point(p[0], p[1]) for p in pts))
        else:
            return cls(*(Point(p[0], p[1]).transform(matrix) for p in pts))

    @classmethod
    def from_flat(cls, coords: Sequence[float],*,matrix:Sequence[float]|None=None) -> "Quad":
        assert len(coords)==8
        it = iter(coords)
        if matrix is None or tuple(matrix)==Matrix.identity:
            return cls(*(Point(x, y) for x, y in zip(it, it)))
        else:
            return cls(*(Point(x, y).transform(matrix) for x, y in zip(it, it)))
    
    @classmethod
    def join(cls, quads: Sequence['Quad']) -> 'Quad':
        """合并多个quad为一个包围所有quad的最小外接矩形quad"""
        assert len(quads)>0
        points = [p for q in quads for p in q.points]
        bbox = BBox.from_points(points)
        return bbox.to_quad()
    
    @staticmethod
    def get_quad(obj:Any)->_Quad|None:
        if isinstance(obj,Quad):
            b = obj
        elif isinstance(obj,Sequence):
            #这个就已经包括了BBox，但是分开判断还是更加明确一点
            b=cast(_Quad,obj)
        elif isinstance(obj,dict):
            b=cast(_Quad|None,obj['quad'])
        else:
            b=cast(_Quad|None,obj.quad) # type: ignore
        return b

if __name__ == "__main__":
    # 从 BBox 创建
    bbox = BBox(10, 20, 110, 80)
    quad = bbox.to_quad()
    print(f"quad:    {quad}")
    print(f"center:  {quad.center}")
    print(f"area:    {quad.area}")
    print(f"bbox:    {quad.bbox}")
    print(f"is_rect: {quad.is_rect}")

    # PDF 仿射变换（平移 +5, +10）
    m = (1, 0, 0, 1, 5, 10)
    print(f"\ntransform {m}:")
    print(f"  {quad.transform(m)}")

    # 旋转 45°
    print(f"\nrotate 45°:")
    print(f"  {quad.rotate(45)}")

    # 缩放
    print(f"\nscale 0.5x:")
    print(f"  {quad.scale(0.5)}")

    # 格式转换
    print(f"\nto_flat: {quad.to_flat()}")
    print(f"to_list: {quad.to_list()}")

    # NamedTuple 特性
    p1, p2, p3, p4 = quad
    print(f"\n解包: {p1}, {p2}")
    print(f"isinstance tuple: {isinstance(quad, tuple)}")
