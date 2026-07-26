from typing import Any, Sequence, cast

import PIL
import PIL.Image

from memect.base.bbox import BBox, Point
from memect.base.matrix import Matrix
from memect.pdf.base import KPage


def inpaint(pil_image:PIL.Image.Image,*,page:KPage|None=None,text_bboxes:Sequence[BBox]|None=None,bboxes:Sequence[BBox]|None=None,polys:Sequence[Sequence[Any]]|None=None)->PIL.Image.Image:
    import cv2
    import numpy as np
    import numpy.typing as npt

    debug=False

    def ensure(bbox:BBox,size:tuple[float,float])->BBox:
        x0,y0,x1,y1=bbox
        x0=max(0,x0)
        x1=min(size[0],x1)
        y0=max(0,y0)
        y1=min(size[1],y1)
        return bbox.adjust(x0=x0,x1=x1,y0=y0,y1=y1)

    def is_dark_bg(image:cv2.typing.MatLike,*,margin_size:int=2,threshold:float=0.6,min_size:tuple[int,int]=(5,5))->bool:
        """判断是否为深色背景"""
        #如果为深色背景，二值化后，就是白底黑字，所以只需要判断是否为白底
        #中文笔画多占用的空间多，所以仅仅考虑4周的空间如果白色居多的
        h,w = image.shape[:2]
        if h<min_size[1] or w<min_size[0]:
            return False
        image = image.copy()
        left=image[:,0:margin_size].reshape(-1,1)
        right=image[:,-margin_size:0].reshape(-1,1)
        top=image[0:margin_size,:].reshape(-1,1)
        bottom=image[-margin_size:0,:].reshape(-1,1)
        margin_image=np.concatenate((left,right,top,bottom))
        #获得背景像素数（白色）
        n=cv2.countNonZero(margin_image)
        total=margin_image.shape[0]
        if False:
            print('==>',image.shape,margin_image.shape,total,n,n/total)
            cv2.imshow('margin',margin_image)
            cv2.waitKey()
            cv2.destroyAllWindows()
        return n/total>=threshold
    def to_binary(img:cv2.typing.MatLike)->Any:
        gray = cv2.cvtColor(img,cv2.COLOR_BGR2GRAY)
        #黑底白字
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        if is_dark_bg(thresh):
            thresh=cv2.bitwise_not(thresh)
        return thresh
    
    if page is not None:
        #表示坐标是相对页面
        sw = pil_image.width/page.width
        sh = pil_image.height/page.height
        m = Matrix(1,0,0,-1,0,page.height).scale(sw,sh)
    else:
        m=None


    img = np.array(pil_image)
    #img = img[:,:,::-1]
    img = cv2.cvtColor(img,cv2.COLOR_RGB2BGR)
    mask = cast(npt.NDArray[np.uint8],np.zeros(img.shape[:2], dtype=np.uint8))
    
    if text_bboxes:
        for bbox in text_bboxes:
            if m is not None:
                bbox = bbox.transform(m.to_tuple()).small
                bbox = ensure(bbox,pil_image.size)
            x0,y0,x1,y1 = bbox.to_int()
            mask[y0:y1,x0:x1]=255
            #TODO 根据轮廓来填充，效果不是很好，因为轮廓可能细了一些，而且周边的颜色也不一定匹配
            if False:
                x0,y0,x1,y1 = bbox.idata
                thresh = to_binary(img[y0:y1,x0:x1])
                text_mask = np.zeros(img.shape[:2],dtype=np.uint8)
                
                # 创建不同形状的核
                # 矩形核
                kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
                # 椭圆核
                #kernel_ellipse = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
                # 十字核
                #kernel_cross = cv2.getStructuringElement(cv2.MORPH_CROSS, (5, 5))

                dilated = cv2.dilate(thresh, kernel, iterations=1)
                text_mask[y0:y1,x0:x1]=dilated
                cv2.imshow('thresh',thresh)
                cv2.imshow('dilated',dilated)
                cv2.imshow('text mask',text_mask)

                #获得0区域的平均值
                #黑底白字=>白底黑字
                b,g,r=cv2.mean(img[y0:y1,x0:x1],mask=cv2.bitwise_not(dilated))[:3]
                #或者获得出现最多次数的元素？
                bgr=(int(b),int(g),int(r))

                img[text_mask!=0]=bgr
                print('==.bgr',bgr)
                cv2.imshow('imgxx',img)
                cv2.waitKey()
                cv2.destroyAllWindows()

            if False:
                coords = cv2.findNonZero(thresh)
                x, y, w, h = cv2.boundingRect(coords)
                text_mask = np.zeros_like(thresh)
                text_mask[y:y+h,x:x+w]=255
                mask[y0:y1,x0:x1]=text_mask
                #cv2.rectangle(mask,(x0,y0),(x1,y1),255,-1)
                #print('bbox',bbox)
            
    if bboxes:
        for bbox in bboxes:
            #必须为原点为左上角，相对图片
            if m is not None:
                bbox = bbox.transform(m).large
                bbox = ensure(bbox,pil_image.size)
            x0,y0,x1,y1 = bbox.large
            mask[y0:y1,x0:x1]=255
            #cv2.rectangle(mask,(x0,y0),(x1,y1),255,-1)
            #print('bbox',bbox)
            
    
    if polys:
        for points in polys:
            if m is not None:
                #points = list(m.transforms(points))
                points = [ Point(point[0],point[1]).transform(m) for point in points]

            pts = np.array(points, dtype=np.int32)
            cv2.fillPoly(mask, [pts], 255)

    #cv2.INPAINT_TELEA: Fast, based on Fast Marching Method
    #cv2.INPAINT_NS: Slower but sometimes better quality
    #t1 = time.monotonic()
    new_img = cv2.inpaint(img, mask, inpaintRadius=3, flags=cv2.INPAINT_NS)
    #t2 = time.monotonic()
    #print('==>>',t2-t1)
    if debug:
        cv2.imshow('mask',mask)
        cv2.imshow('img',img)
        cv2.imshow('final img',new_img)
        cv2.waitKey()
        cv2.destroyAllWindows()

    new_img = cv2.cvtColor(new_img,cv2.COLOR_BGR2RGB)
    return PIL.Image.fromarray(new_img)

def inpaint2(pil_image:PIL.Image.Image,*,page:KPage|None=None,bboxes:Sequence[BBox]|None=None)->PIL.Image.Image:
    """使用字符串的背景颜色填充"""

    def get_color_with_mask(image:Any, mask:Any)->tuple[int,int,int]:
        """处理彩色图像（BGR格式）"""
        # 应用掩码获取有效像素
        # 首先创建三维掩码
        mask_3d = np.stack([mask] * 3, axis=2) if len(image.shape) == 3 else mask
        
        # 获取掩码区域内的像素
        masked_pixels = image[mask_3d > 0]
        
        if masked_pixels.size == 0:
            return (255,255,255)
        
        # 重塑为像素列表
        if len(masked_pixels.shape) == 1:
            masked_pixels = masked_pixels.reshape(-1, 3)
        
        # 方法1：将BGR像素转换为整数编码（更高效）
        pixels_int = (masked_pixels[:, 0].astype(np.uint32) << 16) | \
                    (masked_pixels[:, 1].astype(np.uint32) << 8) | \
                    masked_pixels[:, 2].astype(np.uint32)
        
        # 统计频率
        unique_values, counts = np.unique(pixels_int, return_counts=True)
        
        # 找到最大值
        max_idx = np.argmax(counts)
        most_frequent_int = unique_values[max_idx]
        frequency = counts[max_idx]
        
        # 解码BGR值
        b = (most_frequent_int >> 16) & 0xFF
        g = (most_frequent_int >> 8) & 0xFF
        r = most_frequent_int & 0xFF
        most_frequent_pixel = (int(b), int(g), int(r))
        
        # 获取前5个最频繁像素
        top_indices = np.argsort(counts)[-5:][::-1]
        top_n = []
        for idx in top_indices:
            pixel_int = unique_values[idx]
            b = (pixel_int >> 16) & 0xFF
            g = (pixel_int >> 8) & 0xFF
            r = pixel_int & 0xFF
            top_n.append(((int(b), int(g), int(r)), int(counts[idx])))
        
        #return most_frequent_pixel, int(frequency), top_n
        return most_frequent_pixel
    
    def get_bg_color(image:Any,bbox:BBox):
        """获得指定区域周边的颜色"""

        #目前主要是表格，可能bbox靠近了左右或者上下的线或者矩形，倒置获得了错误的颜色
        h,w=image.shape[:2]
        bg_bbox = bbox.adjust(dx=4,dy=0).large.ensure((0,0,w,h))
        mask=np.zeros(image.shape[:2],dtype=np.uint8)
        x0,y0,x1,y1=bg_bbox.idata
        mask[y0:y1,x0:x1]=255
        x0,y0,x1,y1=bbox.idata
        mask[y0:y1,x0:x1]=0
        #不使用平均值，而是使用最多出现的值
        #b,g,r = cv2.mean(image,mask=mask)[:3]
        b,g,r = get_color_with_mask(image,mask)
        
        #cv2.imshow('x',mask)
        #cv2.waitKey()
        #cv2.destroyAllWindows()
        return (int(r),int(g),int(b))
    
    cv2_image = cv2.cvtColor(np.array(pil_image),cv2.COLOR_RGB2BGR) 
    image = pil_image.copy()
    draw = PIL.ImageDraw.Draw(image)
    #parser = FontParser()
    if page is not None and bboxes:
        sw = image.width/page.width
        sh = image.height/page.height
        m = M(1,0,0,-1,0,page.height).scale(sw,sh).to_tuple()
        new_bboxes:list[BBox]=[]
        for bbox in bboxes:
            new_bboxes.append(bbox.transform(m).trunc.ensure((0,0,image.width,image.height)))
        bboxes = new_bboxes
    
    if bboxes:
        for bbox in bboxes:
            #使用周边的背景颜色
            #color,bg_color = parser.get_colors(image.crop(bbox.data))
            bg_color = get_bg_color(cv2_image,bbox)
            draw.rectangle(bbox.data,fill=bg_color)
    return image
