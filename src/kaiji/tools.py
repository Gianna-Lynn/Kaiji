"""定义工具契约、注册表、参数与结果校验、超时和有界重试。"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from kaiji.models import ToolCall, ToolErrorCode, ToolFailure, ToolResult


class ToolExecutionError(Exception):
    """
    表示工具预期内的执行失败，并显式携带是否可重试。
    继承说明: 继承Exception标准库.
    类变量说明: 包含retryable: bool, 说明是否可以重试.
    """

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """定义工具名称、说明、输入输出模型、处理函数和可靠性策略。  
    类变量说明:  
    - 类型约束: BaseModel:  
        BaseModel 是 Pydantic 库提供的基类。继承它定义的类会自动获得：  
        输入参数和返回结果校验：调用 **.model_validate()** 例如声明a: int，传入 "hello" 会自动报错.
        类型转换：例如传入 a: "123"（字符串），会自动转成 int(123)  
        JSON Schema 导出：调用 **.model_json_schema()** 就能拿到标准 JSON Schema（给 LLM 用的工具描述就靠它）  
    - arguments_model:   
        含义: 工具输入参数的模式类(是一个类对象, 不是类的实例). type[BaseModel]在Python中表示"BaseModel的子类/BaseModel本身"  
        用途: 在执行工具前，用arguments_model来校验和解析 LLM 传来的参数.
    - result_model:   
        含义: 工具输出结果的模式类.  
        用途: handler 执行完后，用result_model去校验返回结果是否符合预期格式.  
    - handler: Callable[[BaseModel], Any]:  
        含义: 工具的实际处理函数.Callable[[BaseModel], Any] 表示："一个可调用对象, 接受 BaseModel 实例, 返回任意类型（Any）"  
        它是一个函数: def some_handler(args: BaseModel) -> 随便什么类型  
    """

    name: str
    description: str
    arguments_model: type[BaseModel] # type[BaseModel]表达"这个字段的值必须是BaseModel的一个子类, 或者BaseModel本身.
    result_model: type[BaseModel]     
    handler: Callable[[BaseModel], Any]
    timeout_seconds: float = 2.0
    max_retries: int = 0

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("tool name cannot be empty")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_retries not in (0, 1):
            raise ValueError("the v0 runtime permits at most one retry")


class ToolRegistry:
    """按唯一名称保存工具定义，并向模型暴露参数 Schema。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        """
        作用: 在工具注册表中写入键值对:(工具名, 工具定义), 并保证不出现重复的工具名.
        如果出现重复工具名, 提出ValueError.  
        参数说明:
        - definition: ToolDefinition 中包含了工具名和工具定义.
        """
        # TODO 1：拒绝重复名称，否则保存工具定义。
        # 名称是唯一查找键；描述文本不参与工具身份判断。
        if definition.name in self._tools:
            raise ValueError("already registered")
        self._tools[definition.name] = definition

    def get(self, name: str) -> ToolDefinition | None:
        """
        作用: 在工具注册表中, 根据工具名, 搜索工具定义并返回.  
        - 参数说明:  
        name: str 工具名字符串  
        - 返回值类型:  
        找到了ToolDefinition, 没找到返回None 
        """
        # TODO 1：未知名称返回 None。此处不抛异常，因为 Executor 负责把它
        # 转换为结构化 UNKNOWN_TOOL 失败。
        return self._tools.get(name) # 字典的get方法默认返回None

    def schemas(self) -> list[dict[str, Any]]:
        """
        作用: 把工具注册表转换为一个列表并返回. 列表中每个元素都是一个字典, 对应于一个工具.
        字典的键值对包括工具名, 工具说明, JSON Schema.
        """
        # TODO 1：暴露工具名称、描述，以及放在 parameters 字段下的
        # Pydantic JSON Schema。
        tool_dict_list : list[dict[str, Any]] =[]
        for tool in self._tools.values(): # tool是一个ToolDefinition类型对象.
            tool_dict = {
                "name" : tool.name,
                "description": tool.description,
                "parameters": tool.arguments_model.model_json_schema()
                # .model_json_schema()回一个纯 dict，包含 properties、required、type 等 JSON Schema 字段。
            }
            tool_dict_list.append(tool_dict)
        return tool_dict_list

class ToolExecutor:
    """在工具权限边界内完成调用准备、执行、结果校验和有界重试。"""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def execute(self, call: ToolCall) -> ToolResult:
        prepared = self._prepare_call(call)
        if isinstance(prepared, ToolResult):
            return prepared

        definition, arguments = prepared
        return self._execute_with_retry(call, definition, arguments)

    def _prepare_call(
        self, call: ToolCall
    ) -> ToolResult | tuple[ToolDefinition, BaseModel]:
        """查找工具并校验参数，但不执行处理函数。"""
        # TODO 2：
        # 1. 通过 Registry 查找 call.name。
        # 2. 工具不存在时返回 UNKNOWN_TOOL，attempts=0。
        # 3. 使用 definition.arguments_model 校验 call.arguments。
        # 4. 捕获 ValidationError，返回 INVALID_ARGUMENTS，attempts=0。
        #
        # 失败时返回 ToolResult，成功时返回 (definition, arguments)，可以让
        # execute() 的控制流保持显式，而不是用异常代替正常分支。
        
        tool_definition = self._registry.get(call.name)
        if tool_definition is None:
            return self._failure(
                call=call,
                code=ToolErrorCode.UNKNOWN_TOOL,
                message="工具未知",
                retryable=False,  # 找不到call.name对应的工具, 注册表里没有这个名字, 原样重试仍找不到.
                attempts=0, # 工具未能成功被运行哪怕一次, 所以attempt取0.
            )
        try:
            arguments = tool_definition.arguments_model.model_validate(call.arguments)
            # 这里的arguments是一个BaseModel类型.
        except ValidationError as exc:
            return self._failure(
                call=call,
                code=ToolErrorCode.INVALID_ARGUMENTS,
                message=str(exc),
                retryable=False,  # 参数内容已经错了, 原样重试仍会校验失败。
                attempts=0, # 工具未能成功被运行哪怕一次, 所以attempt取0.
            )
        else:
            return tool_definition, arguments

    def _execute_with_retry(
        self,
        call: ToolCall,
        definition: ToolDefinition,
        arguments: BaseModel,
    ) -> ToolResult:
        """执行已准备的调用，校验输出，并应用有界重试策略。"""
        # TODO 3：
        # - max_tries 等于 max_retries + 1。
        # - 在尝试循环内部调用 _invoke_with_timeout。
        # - 返回成功前用 definition.result_model 校验原始输出。
        # - TIMEOUT 和 retryable=True 的 ToolExecutionError 可以重试。
        # - ValidationError 与未预期的 Exception 不可重试。
        # - 遇到不可重试失败或耗尽次数时立即停止。
        
        max_tries = definition.max_retries + 1 # 最多允许重试次数是max_retries, 最多允许尝试次数是max_tries
        for cnt in range(max_tries):
            # 这里cnt程序上从0开始计数, 但是语义上从1开始计数.
            try:
                raw_result = self._invoke_with_timeout(
                    definition=definition, 
                    arguments=arguments,
                )
                # 这里的raw_result是由handler生成的原始结果, 属于Any类型. 
                # 根据调用工具的不同, raw_result可能形如 {"final_price": 90} , {"display_name": "..."}  , 
                # 需要交给工具输出结果的模式类result_model来判定raw_result是不是合法.
                # 如果合法, validated_result是BaseModel类的一个实例, final_price是这个类的一个类成员, 可以用validated_result.final_price来访问, 读出来是90.
                # 但是validated_result本身并不是字典, 不允许这样访问: validated_result["final_price"]  ,会报TypeError
                validated_result = definition.result_model.model_validate(raw_result)

                # 调用工具成功时, 尝试次数等于循环标记cnt + 1, 并结束循环.
                return ToolResult(
                    call_id=call.call_id, 
                    tool_name=definition.name,
                    ok=True,
                    error=None,
                    attempts=cnt + 1,
                    output=validated_result.model_dump(mode='json'), 
                    # 注意要用output传出结果,不能只传出状态就了事. 
                    # 结果是一个字典. BaseModel.model_dump()会把BaseModel转为字典[str, Any]
                    # BaseModel.model_dump()默认保持Python对象的精度, 会把数字保存为Decimal.
                    # 但是本实验要求把结果用JSON序列化后, 存到output交给LLM读取, 因此需要把数字保存为字符串.
                    # 可以给BaseModel.model_dump()添加一个参数mode='json'.
                    # 这样做会把Decimal变成字符串, 普通int和float是JSON数字.
                )
            except FutureTimeoutError as e:
                # 工具调用等待时间耗尽: 可重试, 若尝试次数耗尽, 就返回
                if cnt == max_tries - 1:
                    return self._failure(
                        call=call,
                        code=ToolErrorCode.TIMEOUT,
                        message= f"[{type(e).__name__}] {str(e)},工具调用等待时间耗尽",
                        retryable=True, # retryable表示的是FutureTimeoutError是否允许重试. 超时这种错误允许重试.
                        attempts=cnt + 1,
                    )
            except ToolExecutionError as e:
                # 工具调用失败: 取决于e.retryable, 查看是否允许重试. 另外, 若尝试次数耗尽, 就返回
                if not e.retryable or cnt == max_tries - 1:
                    return self._failure(
                        call=call,
                        code=ToolErrorCode.EXECUTION_FAILED,
                        message= f"[{type(e).__name__}] {str(e)}, 工具调用失败",
                        retryable=e.retryable,    
                        attempts=cnt + 1
                        # retryable表示的是这一种错误是否允许重试. 
                        # 工具调用失败这种错误可能存在多种情况: 
                        # 服务限流, e.retryable == True, 等一会儿再试可能调用成功.
                        # 用户非法, e.retryable == False, 再试一百次, 用户依旧非法, 不可能调用成功.
                        # 因此不能再这里给retryable赋值. 工具自己抛出的ToolExecutionError是自定义类, 
                        # 已经带有.retryable标记, 直接拿来输入错误处理函数_failure即可. 
                        # ToolExecutionError.retryable是这一类错误本身的属性.
                    )
            except ValidationError as e:
                # handler 返回了格式不对的数据, 不允许重试.
                return self._failure(
                    call=call,
                    code=ToolErrorCode.INVALID_RESULT,
                    message=f"[{type(e).__name__}] {str(e)}, 工具返回的数据格式错误",
                    retryable=False, # retryable表示的是ValidationError是否允许重试. 这种错误属于数据格式错误, 不允许重试.
                    attempts=cnt + 1,
                )
            except Exception as e:
                # 其他任何没有预料到的错误, 不允许重试.
                return self._failure(
                    call=call,
                    code=ToolErrorCode.EXECUTION_FAILED,
                    message= f"[{type(e).__name__}] {str(e)}, 工具调用出现了其他失败类型",
                    # retryable=e.retryable, # retryable是ToolExecutionError的自定义字段,而不是Exception拥有的字段, 这么写会抛Attribute Error
                    retryable=False, # 未预期的异常,不允许重试.
                    attempts=cnt + 1,
                )
        #endfor
        raise AssertionError("尝试调用工具的循环必须返回结果") # 显式标记理论上不可到达的结尾.这一断言说明循环中一定会返回
    
    # "@staticmethod"声明此方法是静态方法,不需要访问self, 也不需要"self"参数.
    # 具体来说, 此方法是一个纯工具函数, 不需要ToolExecutor这个实例的任何属性.
    # 添加这一声明之后, Python不会再把self当做参数传入函数内部.
    # 普通方法含有self, 用法是: 实例.方法() 或者 self.方法() Python会自动传入self.
    # 静态方法不含self, 用法是: 实例.方法() 或者 self.方法() 或者 类.方法(), 但Python一定不会传入self参数.
    @staticmethod 
    def _invoke_with_timeout(
        definition: ToolDefinition, arguments: BaseModel
    ) -> Any:
        """
        把一个工具函数丢到单独线程里运行，最多等待指定时间，然后把工具的原始返回值交还给上层校验。
        参数说明:  
        - arguments: BaseModel 是某个Pydantic输入模型的实例, 可能带有具体的数值.  
        返回值说明:  
        返回值是Any, 因为注册表中可能存在完全不同的工具, 例如:  
        折扣工具 -> {"final_price": 90}  
        搜索工具 -> {"matches": [...]}  
        用户工具 -> {"display_name": "..."}  
        handler会返回的结构无法事先确定, 因此被声明为Callable[[BaseModel], Any], 表示它: 是一个可调用对象, 接收Pydantic参数对象, 返回任意原始数据.  
        返回的原始数据raw_result, 需要经过 BaseModel类提供的model_validate(raw_result)方法进行校验, 通过校验后才能成为成功的工具结果.
        """
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=definition.name)
        # ThreadPoolExecutor是标准库提供的线程池。max_workers=1表示这个线程池最多同时运行一个任务.这里每次调用都会建立一个临时线程池，只用来运行当前工具。
        # thread_name_prefix=definition.name 会让工作线程带上工具名称，方便以后排查日志
        
        future = pool.submit(definition.handler, arguments)
        # pool.submit 等价于要求线程池执行：definition.handler(arguments)
        # submit()不会直接返回工具结果，而是立刻返回一个 Future。
        # Future 可以理解成“未来才会完成的结果盒子”：Future[Any].
        # 因为 handler 返回 Any，所以这里推导出的类型是 Future[Any]。可能处于正在执行/ 已成功，内部保存返回值/ 已失败，内部保存异常；/ 已取消 这些状态.

        try:
            return future.result(timeout=definition.timeout_seconds)
            # 超时表示Runtime停止了等待, 但是已经开始执行的handler可能还在后台继续运行.比如扣款, 删除文件等敏感操作.
            # 对于这些工具不能因为外层超时就直接安全重试, 否则可能出现第一次调用未结束的时候第二次调用就启动.
            # TODO: 增加幂等键/工具自身超时/可取消机制.

        
            # 等待线程中的handler完成.
            # 可能出现三种结果：
            # 工具按时成功： return {"final_price": 90}
            # future.result() 就返回这个字典。
            # 工具自身抛出异常：raise ToolExecutionError("服务暂时不可用", retryable=True)
            # future.result() 会在当前线程重新抛出这个异常。
            # 等待时间耗尽：FutureTimeoutError
            # 这个异常应当由外层 _execute_with_retry() 捕获，并转换为：
            # ToolErrorCode.TIMEOUT
            # retryable=True

        finally:
            pool.shutdown(wait=False, cancel_futures=True)
            # 无论工具成功/超时/抛出异常,线程池都需要清理，因此使用 finally。
            # 参数含义是：
            # wait=False：当前代码不等待工作线程结束。
            # cancel_futures=True：取消尚未开始的任务。

    @staticmethod
    def _failure(
        call: ToolCall,
        code: ToolErrorCode,
        message: str,
        *,
        retryable: bool,
        attempts: int,
    ) -> ToolResult:
        return ToolResult(
            call_id=call.call_id,
            tool_name=call.name,
            ok=False,
            attempts=attempts,
            error=ToolFailure(code=code, message=message, retryable=retryable),
        )
