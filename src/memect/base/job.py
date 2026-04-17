import logging
import threading
import weakref
from concurrent.futures import (
    FIRST_COMPLETED,
    FIRST_EXCEPTION,
    Executor,
    Future,
    ThreadPoolExecutor,
    as_completed,
    wait,
)
from enum import StrEnum, auto
from types import TracebackType
from typing import Any, Callable, Final, Self, Sequence

from pydantic import BaseModel


class JobPolicy(StrEnum):
    fifo=auto()
    """按顺序执行"""
    balance=auto()
    """均衡调度执行"""

class JobError(Exception):
    pass

type ProgressListener[T]=Callable[[Sequence[T],Sequence[T]],None]
class Job[T1,T2]:
    """Job包含多个小任务，如：多个页面需要解析"""
    def __init__(self,items:Sequence[T1]):
        super().__init__()
        self.items:Final=items
        self._succeeded_items:Final[list[T1]]=[]
        self._cancelled_event:Final=threading.Event()
        self._future:Future[list[T2]]|None=None
        self._lock:Final=threading.RLock()
        self._listeners:Final[list[ProgressListener[T1]]]=[]
    
    def cancelled(self)->bool:
        """这个是给scheduler使用的，判断是否被外部取消了"""
        return self._cancelled_event.is_set()

    def cancel(self):
        """执行取消，和future.cancel的有点不同，future.cancel已经运行无法取消，这里因为是分批执行，尽可能取消"""
        #这个已经执行，无法取消
        self.future.cancel()
        self._cancelled_event.set()
    
    @property
    def future(self)->Future[list[T2]]:
        assert self._future is not None
        return self._future
    
    @future.setter
    def future(self,future:Future[list[T2]]):
        """设置feture，scheduler使用"""
        assert self._future is None
        #future.add_done_callback(self._on_done_callback)
        self._future=future
    

    
    def wait(self,timeout:float|None=None,*,cancel:bool=True)->list[T2]:
        """
        等待结果，如果设置了cancel=True，在有异常或者超时等，会自动取消，所以，第二次再调用result，返回的结果可能不一致
        """
        if self.cancelled():
            raise JobError('Job被取消')
        try:
            return self.future.result(timeout)
        finally:
            if cancel:
                self.cancel()

    def on_complete(self,item:T1):
        """成功完成了"""

        items:Sequence[T1]=[]
        succeeded_items:Sequence[T1]=[]
        listeners:Sequence[ProgressListener[T1]]=[]
        with self._lock:
            self._succeeded_items.append(item)
            if len(self._listeners)>0:
                items=tuple(items)
                succeeded_items=tuple(self._succeeded_items)
                listeners=tuple(self._listeners)
        
        #避免lock
        for listener in listeners:
            listener(items,succeeded_items)
        
    
    def add_progress_listener(self,listener:ProgressListener[T1]):
        with self._lock:
            self._listeners.append(listener)
        pass
    def remove_progress_listener(self,listener:ProgressListener[T1]):
        with self._lock:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

class SchedulerArgs(BaseModel):
    max_task_size:int=10
    policy:JobPolicy=JobPolicy.fifo
    auto_close:bool=True

class Scheduler[T1,T2]:
    _logger=logging.getLogger(f'{__module__}.{__qualname__}')
    def __init__(self,name:str,handler:Callable[[T1],T2],*,max_task_size:int=10,policy:JobPolicy=JobPolicy.fifo,auto_close:bool=True):
        super().__init__()
        self.name:Final=name
        self._max_task_size:Final = max_task_size
        """最多同时执行多少个任务"""
        self._policy:Final=policy
        self._handler:Final=handler
        self._lock:Final= threading.RLock()
        self._task_executor:Final = ThreadPoolExecutor(self._max_task_size,thread_name_prefix=f'{name}_task')
        """执行job的task"""

        #execute2需要的变量
        self._running_tasks: Final[list[Future[Any]]] = []
        self._jobs: Final[list[Job[T1,T2]]] = []
        self._job_executor:Final = ThreadPoolExecutor(self._max_task_size,thread_name_prefix=f'{name}_job')
        """执行job"""

        if auto_close:
            self._finalizer=weakref.finalize(self,self._close,name,id(self),self._task_executor,self._job_executor)
        else:
            self._finalizer=None
    

    
    @property
    def max_task_size(self)->int:
        return self._max_task_size
    
    
    def submit(self,items:Sequence[T1])->Job[T1,T2]:
        job=Job[T1,T2](items)
        if not items:
            job.future=Future()
            job.future.set_result([])
        else:
            if self._policy==JobPolicy.fifo:
                job.future=self._job_executor.submit(self._execute_fifo,job)
            else:
                job.future=self._job_executor.submit(self._execute_balance,job)
    
        return job


    def _execute_fifo(self,job:Job[T1,T2])->list[T2]:
        """按提交的顺序执行，如：job1有1000页，job2有10页，那么，需要等job1完成了，才轮到job2"""
        tasks: list[Future[Any]] = []
        try:
            with self._lock:
                for item in job.items:
                    task = self._task_executor.submit(self._handle,job,item)
                    tasks.append(task)
            return self._gather_tasks(job,tasks)
        finally:
            #不管出现什么异常或者正确返回，都把等待中的取消，避免浪费时间执行
            for task in tasks:
                task.cancel()

    def _execute_balance(self,job:Job[T1,T2])->list[T2]:
        """公平的调度，分批调度，按当前job的数量，进行公平分配，对于任务数少的job，可以尽快完成"""
        tasks:list[Future[Any]]=[]
        running_tasks: list[Future[Any]] = []
        items=list(job.items)

        def on_done(task:Future[Any]):
            with self._lock:
                #如果在close中清除了，就需要判断是否存在，目前没有清除
                self._running_tasks.remove(task)
                #不需要在这里执行，因为在chec_tasks()中处理了
                #running_tasks.remove(future)

        def submit_tasks():
            """提交任务"""
            with self._lock:
                # 如果已经满了
                job_size = len(self._jobs)
                if len(self._running_tasks) >= self._max_task_size:
                    return

                #如：当前有3个job，max_task_size=10，每个job可以执行4个task，也就是4+4+2
                task_size = (
                    self._max_task_size + job_size-1
                ) // job_size
                if len(running_tasks) >= task_size:
                    return

                # 现在可以添加多少个，采用的策略是如果有空位，就先填满某个client，其他client就再等一会
                # 当然这个是随机的，哪个client（线程）被调度到就是这个先填满
                k = min(
                    task_size - len(running_tasks),
                    self._max_task_size - len(self._running_tasks),
                )
                for item in items[0:k]:
                    task = self._task_executor.submit(self._handle,job,item)
                    self._running_tasks.append(task)
                    running_tasks.append(task)
                    tasks.append(task)
                    task.add_done_callback(on_done)

                del items[0:k]

        def check_tasks():
            """检查当前正在执行的futures的状态"""
            # 如果当前客户的任务有一个失败
            i=0
            while i<len(running_tasks):
                task = running_tasks[i]
                if task.done():
                    #如果有异常或者被取消，上抛异常，这里就不记录日志了，因为中invoke中会记录
                    #或者，每一个都先记录一次异常？
                    del running_tasks[i]
                    try:
                        task.result()
                    except:
                        raise
                else:
                    i+=1
        
        def wait_tasks():
            """等待有空位执行任务"""
            # self._running_tasks会被复制一个，所以支持多线程
            done, pending = wait(
                self._running_tasks, timeout=None, return_when=FIRST_COMPLETED
            )

        def run_once():
            submit_tasks()
            wait_tasks()
            check_tasks()
        
        with self._lock:
            self._jobs.append(job)
        try:
            try:
                while items and not job.cancelled():
                    run_once()
                return self._gather_tasks(job,tasks)
            finally:
                # 在所有的页面都在处理中了，就可以递减了，因为后续不再占用工位了
                with self._lock:
                    self._jobs.remove(job)
        finally:
            for task in tasks:
                task.cancel()

        
        

    def _gather_tasks(self,job:Job[T1,T2],tasks:Sequence[Future[T2]],method:int=1)->list[T2]:
        #按完成顺序返回，因为分成了很多个小任务，所以每个任务的执行时间都应该不长，如：10秒内
        #如果时间长的，可以再严格一点

        
        def cancel_tasks():
            for task in tasks:
                task.cancel()
        def wait_tasks(timeout:float|None=None)->bool:
            pending_tasks=[task for task in tasks if not task.done()]
            if not pending_tasks:
                return False
            try:
                for task in as_completed(pending_tasks,timeout):
                    if job.cancelled():
                        return False
                    try:
                        task.result()
                    except Exception:
                        #表示有异常，包括CancelledError
                        self._logger.exception('任务执行出现异常')
                        return False
            except TimeoutError:
                pass
            #继续等待
            return True
        
        if job.cancelled():
            cancel_tasks()
            raise JobError('Job被取消')
        
        if method==1:
            #无论任务执行多长时间，都可以1秒取消
            while tasks:
                if job.cancelled():
                    break
                if not wait_tasks(1):
                    #等1秒
                    break
        elif method==2:
            #有5个正在执行，且每一个都耗时10秒，然后被取消，也需要等待10秒
            #如果时间不同，如：一个1秒，一个2秒等，那么，等1秒的完成，会调度新的任务，就可以判断job.cancelled()，结束wait
            wait_tasks()
        else:
            #如果手动job.cancel()，可能等待的时间会长一点，如：
            #有5个正在执行，且耗时10秒，然后被取消，正在执行的无法取消，10秒后，调度新的执行，然后可以获得job.cancelled()，抛出异常，然后结束wait
            done,pending = wait(tasks,return_when=FIRST_EXCEPTION)
        
        
        cancel_tasks()

        results:list[T2]=[]
        exceptions:list[Exception]=[]
        for task in tasks:
            #因为前面执行了取消，确保所有的都完成了（成功/失败/取消）
            if task.done():
                try:
                    results.append(task.result())
                except Exception as e:
                    #CancelledError+other
                    exceptions.append(e)
            else:
                #正在运行的，无法取消，但是现在也不要结果了
                try:
                    #可能为Job被取消了，不需要等待结果
                    #可能为有Task执行失败，Job自动取消，也不需要等待结果
                    #任务还在运行，无法真正取消，只是丢弃结果
                    raise JobError('Job被取消或者有任务失败，丢弃正在运行的任务的结果')
                except JobError as e:
                    exceptions.append(e)
                #或者在前面的task.result(0.0001)，对于正在运行的，等待0.0001秒然后抛出异常
                pass
        
        if len(exceptions)>0:
            raise ExceptionGroup('执行失败',exceptions)
        else:
            return results
        
    def _gather_tasks2(self,tasks:Sequence[Future[T2]])->list[T2]:
        results:list[T2]=[]
        exceptions:list[Exception]=[]
        for task in tasks:
            #因为前面执行了取消，确保所有的都完成了（成功/失败/取消）
            if task.done():
                try:
                    results.append(task.result())
                except Exception as e:
                    #CancelledError+other
                    exceptions.append(e)
            else:
                #正在运行的，无法取消，但是现在也不要结果了
                try:
                    #可能为Job被取消了，不需要等待结果
                    #可能为有Task执行失败，Job自动取消，也不需要等待结果
                    #任务还在运行，无法真正取消，只是丢弃结果
                    raise JobError('Job被取消或者有任务失败，丢弃正在运行的任务的结果')
                except JobError as e:
                    exceptions.append(e)
                #或者在前面的task.result(0.0001)，对于正在运行的，等待0.0001秒然后抛出异常
                pass
        
        if len(exceptions)>0:
            raise ExceptionGroup('执行失败',exceptions)
        else:
            return results
    
    def _handle(self,job:Job[T1,T2],item:T1)->T2:
        if job.cancelled():
            #因为可能马上被调度执行
            raise JobError('Job被取消了')
        try:
            result=self._handler(item)
            job.on_complete(item)
            return result
        except:
            job.cancel()
            raise 

    def __enter__(self)->Self:
        return self
    
    def __exit__(self,et:type[BaseException]|None,ev:BaseException|None,tb:TracebackType|None):
        self.close()

    def __del__(self):
        #self._logger.info('gc name=%s,object_id=%s',self.name,id(self))
        pass

    def close(self):
        #多次调用不影响
        if self._finalizer is not None and self._finalizer.alive:
            self._finalizer()

    @classmethod
    def _close(cls,name:str,object_id:int,*executors:Executor):
        cls._logger.debug('close name=%s,object_id=%s',name,object_id)
        for executor in executors:
            executor.shutdown(cancel_futures=True,wait=True)

