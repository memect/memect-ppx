from typing import Any, Iterable

from memect.base import lists
from memect.base.bbox import BBox



class Sorter:
    """从上到下，从左到右排序对象"""
    def __init__(self):
        super().__init__()
    
    @classmethod
    def sort[Any](cls,objects:list[Any]):
        """对原对象排序"""
        if not objects:
            return
        lines = cls()._sort(objects)
        objects.clear()
        for line in lines:
            objects.extend(line)
        
    @classmethod
    def sorted(cls,objects:Iterable[Any])->list[Any]:
        lines = cls()._sort(objects)
        return lists.flat(lines)

    @classmethod
    def get_lines(cls,objects:Iterable[Any])->list[list[Any]]:
        """按行划分"""
        return cls()._sort(objects)

    def _sort(self,objects:Iterable[Any])->list[list[Any]]:
        #从上到下，从左到右排序
        objects = sorted(objects,key=lambda obj:BBox.get_bbox(obj)[3],reverse=True)
        lines:list[list[Any]]=[]
        while objects:
            line = self._parse_line(objects)
            lines.append(line)
        return lines
    
    def _parse_line(self,objects:list[Any])->list[Any]:
        from .base import Group
        def has_above_object(line:Group[Any],obj:Any)->bool:
            b1 = BBox.from_object(obj)
            for obj2 in line:
                #[b2] =>不存在这样的
                #[b1]
                b2 = BBox.get_bbox(obj2)#obj2.bbox
                #if b1.over('x',b2,d=4) and b1.y1<=b2.y0:
                if  b1.over('x',b2,d=4) and b2[1]-b1[3]>=-4 and b1[1]<b2[1]:
                    return True
            return False
        
        def join(i:int,objects:list[Any],line:Group[Any])->bool:
            b1 = line.bbox
            #b2 = objects[i].bbox
            b2 = BBox.get_bbox(objects[i])
            #TODO 如果太小了？
            d=min(b1.height,b2[3]-b2[1])/2
            if b1.over('y',b2,d=d) and not has_above_object(line,objects[i]):
                #[b1][b2] => 在同一行，且没有重叠的，如：
                #[obj1][obj2]
                #[obj3]  => 没有在任何其他对象下
                line.append(objects[i])
                line.invalidate()
                return True
            else:
                return False

        line:Group[Any]=Group()

        #取第一个且删除
        line.append(objects.pop(0))
        line.invalidate()

        i=0
        while i<len(objects):
            obj2 = objects[i]
            b1 = line.bbox
            b2 = BBox.get_bbox(obj2)
            if join(i,objects,line):
                del objects[i]
            elif b2[3]<=b1[1]:
                #这样再判断一下是多执行几次，避免“d”太大的时候，跳过了一些很小的
                #因为已经根据y1排序，如果没有重叠的，就可以跳过了
                break
            else:
                i+=1
        
        line.sort(key=lambda obj:BBox.get_bbox(obj)[0])
        return line
