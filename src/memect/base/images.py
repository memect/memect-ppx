
from __future__ import annotations

import base64
import io
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    import cv2.typing

import PIL
import PIL.Image
import PIL.ImageOps
from PIL import ExifTags

from .bbox import BBox, Quad


def size(file:str|Path|bytes)->tuple[int,int]:
    """获得图片的大小，轻量级操作"""
    if isinstance(file,bytes):
        fp=io.BytesIO(file)
    else:
        fp=file
    with PIL.Image.open(fp) as img:
        #轻量级操作
        return img.size


def copy(file:str|Path|bytes,dest:str|Path,*,exif:bool=True,rotation:int=0):
    image:PIL.Image.Image|None=None
    if rotation%360!=0:
        #如果需要处理旋转，肯定需要生成新的图片了
        image = open(file,exif=exif,rotation=rotation,mode=None)
    elif exif:
        #如果只是处理exif，不一定需要生成新的图片
        if isinstance(file,bytes):
            fp=io.BytesIO(file)
        else:
            fp=file
        image = PIL.Image.open(fp)
        image.load()
        image_exif = image.getexif()
        orientation = image_exif.get(ExifTags.Base.Orientation, 1)
        if orientation in (2,3,4,5,6,7,8):
            PIL.ImageOps.exif_transpose(image,in_place=True)
        else:
            #不需要处理exif
            image=None
    else:
        #不需要做任何处理
        image=None

    if image:
        image.save(dest)
    else:
        if isinstance(file,bytes):
            Path(dest).write_bytes(file)
        else:
            shutil.copyfile(file,dest)

def apply_exif(file:str|Path|bytes,dest:str|Path):
    """根据图片的exif信息进行了旋转处理等，然后保存到新的位置"""
    if isinstance(file,bytes):
        fp=io.BytesIO(file)
    else:
        fp=file
    image = PIL.Image.open(fp)
    image.load()
    image_exif = image.getexif()
    orientation = image_exif.get(ExifTags.Base.Orientation, 1)
    if orientation in (2,3,4,5,6,7,8):
        #表示有，需要处理，返回新的图片保存
        image=PIL.ImageOps.exif_transpose(image)
        image.save(dest)
    else:
        #表示没有，使用原图即可，避免不一致
        if isinstance(file,bytes):
            Path(dest).write_bytes(file)
        else:
            shutil.copyfile(file,dest)

def open(file:str|Path|bytes,*,exif:bool=False,rotation:int=0,mode:str|None='RGB')->PIL.Image.Image:
    """
    exif: True表示需要处理exif的旋转
    rotation: 顺时针旋转多少度
    """
    if isinstance(file,bytes):
        fp=io.BytesIO(file)
    else:
        fp=file
    image = PIL.Image.open(fp)
    if exif:
        #不需要生成新的图片，因为本身就是新打开的
        PIL.ImageOps.exif_transpose(image,in_place=True)
    if rotation!=0:
        image = image.rotate(-rotation,expand=True)
    #总是转化为RGB
    if mode and image.mode!=mode:
        if mode=='RGB':
            bg = PIL.Image.new("RGB", image.size, (255, 255, 255))
            bg.paste(image, mask=image.getchannel("A") if "A" in image.mode else None)
            image = bg
        else:
            #直接使用这个，透明的变成黑色
            image=image.convert(mode)
    return image

def open_cv2(file:str|Path|bytes,rotation:int=0)->'cv2.typing.MatLike':
    import cv2
    import numpy as np
    if isinstance(file,bytes):
        buf = np.frombuffer(file, dtype=np.uint8)
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)   # BGR uint8
    else:
        img = cv2.imread(str(file))

    if img is None:
        #或者使用其他异常
        raise ValueError(f'文件不是图片:{file}')
    
    rotation=rotation%360
    if rotation!=0:
        if rotation==90:
            img = cv2.rotate(img,cv2.ROTATE_90_CLOCKWISE)
        elif rotation==180:
            img = cv2.rotate(img,cv2.ROTATE_180)
        elif rotation==270:
            img = cv2.rotate(img,cv2.ROTATE_90_COUNTERCLOCKWISE)
        else:
            raise ValueError(f'不支持的rotation={rotation}')
    return img

def resize(image:PIL.Image.Image,*,size:tuple[int,int]|None=None,max_size:tuple[int,int]|None=None,copy:bool=False)->PIL.Image.Image:
    """可以指定size或者max_size，获得一个返回的图片大小
    size:
    max_size:
    copy:True表示即使不需要resize，也返回一个新的
    """
    def get_alg(src:tuple[int,int],dest:tuple[int,int]):
        if src[0]>dest[0] or src[1]>dest[1]:
            #缩小
            #缩小，LANZCOS质量最好，但是慢一点，20ms，如果是1000页，20秒，5000页，100秒
            #BILINEAR 快一点，7ms，但是都是毫秒级别，1000页，7秒，35秒
            #可以根据当前doc的页数进行选择？😄
            return PIL.Image.Resampling.LANCZOS
        else:
            #放大
            return PIL.Image.Resampling.BICUBIC
    
    original_image = image
    if size is not None:
        #如果指定了明确的大小
        image = image.resize(size,get_alg(image.size,size))
    elif max_size is not None:
        w, h = image.size
        if w > max_size[0] or h > max_size[1]:
            sw = max_size[0]/w
            sh = max_size[1]/h
            scale = min(sw,sh)
            dest_size = (int(w * scale), int(h * scale))
            image = image.resize(dest_size,get_alg(image.size,dest_size)) # type: ignore
    else:
        pass
    
    #如果图片没有改变
    if image is original_image and copy:
        image = original_image.copy()
    return image

def to_url(image:PIL.Image.Image,*,size:tuple[int,int]|None=None,max_size:tuple[int,int]|None,format:str|None=None,file:str|Path|bytes|None=None)->tuple[PIL.Image.Image,str]:
    """
    转换为base64的url格式，
    image:
    size: 新的大小
    max_size: 不超过
    format: 新的格式
    file: 原图的文件（image），目的是如果不需要做任何改变，直接使用原图的，可以直接读取文件内容即可
    """
    original_image = image
    image = resize(image,size=size,max_size=max_size)
    if not format:
        format = image.format or 'png'
    format = format.lower()
    mime=f'image/{format}'        
    if file and image is original_image and (original_image.format or 'png').lower()==format:
        #如果就是原图，且不需要转换格式
        if isinstance(file,bytes):
            data=file
        else:
            data = Path(file).read_bytes()
    else:
        buf = io.BytesIO()
        image.save(buf, format=format)
        data = buf.getvalue()
    data = base64.b64encode(data).decode()
    return (image,f'data:{mime};base64,{data}')

def rotate_cv2(img:cv2.typing.MatLike, angle:int):
    """正值表示顺时针"""
    if angle == 0:
        return img
    elif angle == 90:
        return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    elif angle == 180:
        return cv2.rotate(img, cv2.ROTATE_180)
    elif angle == 270:
        return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    else:
        raise ValueError(f'不支持的度数:{angle}')

def cv2_to_pil(image:cv2.typing.MatLike,mode:str='rgb')->PIL.Image.Image:
    """cv2的图片总是为bgr/bgra，转换为rgb/rgba"""
    if image.shape[2] == 4:
        return PIL.Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGRA2RGBA))
    else:
        return PIL.Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))

def pil_to_cv2(image:PIL.Image.Image)->cv2.typing.MatLike:
    """PIL图片转换为cv2格式(BGR)"""
    import numpy as np
    if image.mode == 'RGBA':
        return cv2.cvtColor(np.array(image), cv2.COLOR_RGBA2BGRA)
    elif image.mode == 'RGB':
        return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    else:
        return np.array(image.convert('RGB'))[:, :, ::-1]
def make_image(
    src: PIL.Image.Image,
    quads: Sequence[Sequence[float]|Quad]|None=None,
    bboxes: Sequence[Sequence[float]|BBox]|None=None
) -> PIL.Image.Image:
    import cv2
    import numpy as np
    src_rgb = src.convert("RGB")
    src_np = np.array(src_rgb)
    # 白色RGB
    out_np = np.ones_like(src_np) * 255
    mask = np.zeros(src_np.shape[:2], dtype=np.uint8)
    if quads:
        for quad in quads:
            pts = np.array(quad).astype(np.int32)
            cv2.fillPoly(mask, [pts], 255)
            
    if bboxes:
        for bbox in bboxes:
            x1,y1,x2,y2=bbox
            cv2.rectangle(mask, (int(x1), int(y1)), (int(x2), int(y2)), 255, thickness=-1)
            out_np[mask == 255] = src_np[mask == 255]
    out_np[mask == 255] = src_np[mask == 255]
    return PIL.Image.fromarray(out_np, mode="RGB")

def hmerge(
    *images: str | Path | PIL.Image.Image,
    file: str | Path | None = None,
    gap: int = 0,
    bg_color: tuple[int, int, int] = (255, 255, 255),
) -> PIL.Image.Image:
    """
    水平合并多张图片。

    Args:
        *images: 图片路径或 PIL.Image.Image 对象，支持混合传入。
        file: 输出路径，为 None 时不保存。
        gap: 图片间距（像素），默认 0。
        bg_color: 背景色 RGB，默认白色 (255, 255, 255)。

    Returns:
        合并后的 PIL.Image.Image 对象。

    Raises:
        ValueError: images 为空时。
    """
    if not images:
        raise ValueError("至少需要一张图片")

    # 统一转换为 PIL.Image.Image
    imgs: list[PIL.Image.Image] = [
        img if isinstance(img, PIL.Image.Image) else PIL.Image.open(img)
        for img in images
    ]

    total_width = sum(img.width for img in imgs) + gap * (len(imgs) - 1)
    max_height = max(img.height for img in imgs)

    canvas = PIL.Image.new("RGB", (total_width, max_height), bg_color)

    x = 0
    for img in imgs:
        y = (max_height - img.height) // 2   # 垂直居中
        canvas.paste(img, (x, y))
        x += img.width + gap

    if file is not None:
        file=Path(file)
        file.parent.mkdir(parents=True,exist_ok=True)
        canvas.save(file)

    return canvas