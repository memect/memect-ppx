
import importlib
import importlib.resources
import logging
import os
from typing import Any, Final, Mapping

from memect.base.bbox import BBox
from memect.pdf.base import KDocument, KPage
from memect.pdf.model import ModelManager


class FeatureParser:
    _logger = logging.getLogger(f'{__module__}.{__qualname__}')

    def __init__(self,features:Mapping[str,Any]):
        super().__init__()
        self._features:dict[str,type]={}
        self._load(features)
    

    def _load(self,features:Mapping[str,Any]):
        for ns,v in features.items():
            package:str|None=v.get('package')
            class_name:str|None = v.get('class')
            if ns=='default':
                prefix=''
            else:
                prefix=f'{ns}.'
            if package:
                for file in importlib.resources.files(package).iterdir():
                    if file.is_file():
                        name,ext = os.path.splitext(file.name)
                        if name[0]!='_' and ext.lower() in ('.py',):
                            m=importlib.import_module(f'.{name}',package)
                            self._features[f'{prefix}{name}']=getattr(m,'Feature')
            elif class_name:
                i=class_name.rfind('.')
                m=importlib.import_module(class_name[0:i])
                self._features[f'{ns}']=getattr(m,class_name[i+1:])
            else:
                pass
        
        self._logger.info('load features=%s',self._features)

    def parse(self,doc:KDocument):
        features = doc.params.features
        if not features:
            return
        
        feature_objs:dict[str,Any]={}
        for name in features:
            feature_class = self._features.get(name)
            if feature_class is None:
                self._logger.warning('不存在的feature=%s',name)
                continue

            if name not in feature_objs:
                feature_objs[name]=feature_class()
        
        for name,feature in feature_objs.items():
            self._logger.info('执行feature=%s',name)
            feature.parse(doc)
    



            


