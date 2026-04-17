# coding=utf-8

import multiprocessing
import os
import re
from collections.abc import Sequence
from typing import Any, Final

from rich.console import Console

# https://docs.python.org/3.12/library/logging.config.html#module-logging.config

os.makedirs('./logs',exist_ok=True)

def access_filter_factory(prefix:str):
    from logging import INFO, LogRecord
    pattern = re.compile(prefix)
    def access_filter(record:LogRecord):
        #print(record.name,record.args,record.msg)
        #('127.0.0.1:52408', 'GET', '/api/parse?task_id=xxx', '1.1', 200)
        #('GET /api/ocr?task_id=xx HTTP/1.1', '200', '-')
        # ('127.0.0.1:57709', 'GET', '/api/parse?task_id=20260312182636-99ad2bc00ca84411a0d5acddad329ddc&custom=false', '1.1', 200)
        args = record.args
        if record.name=='uvicorn.access' and isinstance(args,Sequence) and record.levelno == INFO and len(args)==5 and args[1]=='GET' and args[-1]==200 and isinstance(args[2],str) and pattern.match(args[2]):
            return 0
        return 1
    return access_filter

def get_log_level()->str:
    return os.environ.get('PPX_LOG_LEVEL','INFO').upper()

settings:Final[dict[str,Any]] = {
    'version': 1,
    # 默认为true
    'disable_existing_loggers': False,
    'formatters': {
        'default': {
            'format': '%(asctime)s [%(levelname)-8s][%(process)d:%(processName)s:%(threadName)s][%(name)s][%(filename)s:%(lineno)d][%(funcName)s ] %(message)s',
            'datefmt': '%Y-%m-%d %H:%M:%S'
        },
        'rich':{
            'format': '[%(process)d:%(processName)s:%(threadName)s][%(name)s][%(filename)s:%(lineno)d][%(funcName)s ] %(message)s',
            'datefmt': '%Y-%m-%d %H:%M:%S'
        }
    },
    'filters': {
        'remove_poll':{
            #过滤轮训的日志
            '()': access_filter_factory,
            'prefix':r'/api/parse[?]'
        }
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'default',
            # 'level':'INFO',
            # 'filters':[],
            'stream': 'ext://sys.stdout'
        },
        'rich':{
          'class':'rich.logging.RichHandler',
          'formatter':'rich',
          'filters':['remove_poll'],
          'console': Console(markup=False),
          'omit_repeated_times':False, 
          'show_path':False,
          'rich_tracebacks':False,
          'keywords':None
        },
        'file': {
            #'class': 'logging.handlers.RotatingFileHandler',
            #这个支持多进程下访问同一个文件
            #两种方式是一致的，都是同一个类，如果需要根据时间：ConcurrentTimedRotatingFileHandler
            'class':'concurrent_log_handler.ConcurrentRotatingFileHandler',
            #'class':'logging.handlers.ConcurrentRotatingFileHandler',
            'formatter': 'default',
            #相对程序所在目录
            'filename': './logs/ppx.log',
            # 'level':'ERROR',
            'filters':['remove_poll'],
            'maxBytes': 1024 * 1024,
            'backupCount':10,
            'encoding':'utf-8'
        },

        'access': {
            #'class': 'logging.handlers.RotatingFileHandler',
            'class':'concurrent_log_handler.ConcurrentRotatingFileHandler',
            'formatter': 'default',
            #相对程序所在目录
            'filename': './logs/access.log',
            # 'level':'INFO',
            #过滤掉异步请求的轮训
            'filters':['remove_poll'],
            'maxBytes': 1024 * 1024,
            'backupCount':10,
            'encoding':'utf-8'
        },
        'process':{
            #每个进程一个文件，目的是方便在测试/调试的时候，可以把仅仅查看某个进程的日志
            #'class': 'logging.handlers.RotatingFileHandler',
            #这个支持多进程下访问同一个文件
            'class':'concurrent_log_handler.ConcurrentRotatingFileHandler',
            #'class':'logging.handlers.ConcurrentRotatingFileHandler',
            'formatter': 'default',
            'filename': f'./logs/{multiprocessing.current_process().name}.log',
            #如果子进程没有设置特别的名字，就如下
            #'filename': logs.get_log_dir()/f'process-{os.getpid()}.log',
            # 'level':'ERROR',
            # 'filters':[],
            'maxBytes': 1024 * 1024,
            'backupCount':10,
            'encoding':'utf-8'
        },
    },
    'root': {
        'level': 'INFO',
        'handlers': ['rich','file'],
        # 仅仅过滤当前logger的，不会过滤子logger的，对一个logger而言，通过level+filters控制哪些event可记录
        'filters': []
    },
    'loggers': {
        #例子
        '__sample': {
            'level': 'DEBUG',
            # 默认为True，表示把当前通过的event递交给上一级的handlers
            'propagate': True,
            'filters': [],
            'handlers': []
        },
        'uvicorn': {

        },
        'uvicorn.access': {
            'filters':[
                #如果不需要保留轮训的日志，去掉注释即可
                #'remove_parse_poll'
            ],
            #'propagate':False,
            'handlers':[]
        },
        'hypercorn.access':{
            #'handlers':['access']
        },
        'memect':{
            'level': get_log_level()
        }
    }
}



    
