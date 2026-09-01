"""通过 OpenAI-compatible Chat Completions 调用单工具回合模型。"""

from __future__ import annotations

from time import perf_counter
from typing import Any, Literal
import json
import os

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator, ValidationError

from kaiji.models import (
    Message,
    ModelAction,
    ModelCallMetadata,
    ModelCallResult,
    ModelUsage,
    ProviderErrorCode,
    ProviderFailure,
)
from kaiji.tools import ToolRegistry, ToolCall


class OpenAICompatibleConfig(BaseModel):
    """保存 Provider 配置；API Key 只通过环境变量名间接引用。"""

    base_url: str = Field(min_length=1)
    model: str = Field(min_length=1)
    api_key_env: str = Field(min_length=1)
    timeout_seconds: float = Field(default=30.0, gt=0)
    temperature: float = Field(default=0.0, ge=0)
    system_prompt: str | None = None
    thinking: Literal["disabled"] | None = None
    max_tokens: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def normalize_base_url(self) -> OpenAICompatibleConfig:
        """拒绝明显错误的 URL，同时保留兼容服务的自定义路径前缀。"""

        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("base_url 必须使用 http 或 https")
        self.base_url = self.base_url.rstrip("/")
        return self

    @property
    def endpoint(self) -> str:
        """返回 Chat Completions 完整端点。"""

        return f"{self.base_url}/chat/completions"


class _FunctionCall(BaseModel):
    """校验兼容响应中的函数名称与 JSON 参数字符串。"""

    # 只消费声明字段；兼容服务新增的非语义扩展字段可安全忽略。
    model_config = ConfigDict(extra="ignore", strict=True)

    name: str = Field(min_length=1)
    arguments: str


class _ResponseToolCall(BaseModel):
    """校验单个 OpenAI-compatible 工具调用。"""

    model_config = ConfigDict(extra="ignore", strict=True)

    id: str = Field(min_length=1)
    type: str
    function: _FunctionCall


class _ResponseMessage(BaseModel):
    """校验响应消息中最终文本或工具调用的载荷。"""

    model_config = ConfigDict(extra="ignore", strict=True)

    role: str
    content: str | None = None
    tool_calls: list[_ResponseToolCall] | None = None


class _Choice(BaseModel):
    """校验 Chat Completions 的单个候选。"""

    model_config = ConfigDict(extra="ignore", strict=True)

    message: _ResponseMessage
    finish_reason: str | None = None


class _Usage(BaseModel):
    """读取 OpenAI-compatible Token 字段。"""

    model_config = ConfigDict(extra="ignore", strict=True)

    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class _ChatResponse(BaseModel):
    """只校验 Kaiji 决策所需的最小响应结构。"""

    model_config = ConfigDict(extra="ignore", strict=True)

    id: str | None = Field(default=None, min_length=1)
    model: str | None = Field(default=None, min_length=1)
    choices: list[_Choice]
    usage: _Usage | None = None


class OpenAICompatibleProvider:
    """发送完整工具历史，并把 HTTP/协议失败转换为稳定模型。"""

    def __init__(
        self,
        config: OpenAICompatibleConfig,
        registry: ToolRegistry,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._config = config
        self._registry = registry
        self._transport = transport

    def decide(self, messages: list[Message]) -> ModelCallResult:
        """执行一次非流式调用；本方法不在内部重试，避免隐藏调用预算。"""
        # TODO 13d（L1）：完成一次显式、无隐藏重试的 HTTP 调用。API Key 只能
        # 从 config 指定的环境变量读取；timeout/transport/429/5xx/其他 4xx、
        # 非 JSON 与协议错误必须映射为稳定 ProviderFailure；成功时保存模型、
        # 请求 ID、延迟和 Token。错误结果不得包含鉴权头或响应正文。

        # 开始计时
        start_time = perf_counter()
        # 读取环境变量中的API Key
        api_key = os.environ.get(self._config.api_key_env)
        # 没有Key, 返回不可重试失败
        if not api_key:                 # if not 可以同时覆盖 None 和空字符串 ""
            return self._failure(
                code=ProviderErrorCode.HTTP_ERROR,
                message="缺少API Key",
                retryable=False,
                started=start_time,
            )
        # 创建 httpx.Client
        with httpx.Client(
            base_url= self._config.base_url,
            transport=self._transport,
            timeout=self._config.timeout_seconds,
            headers={"Authorization":f"Bearer {api_key}","Content-Type":"application/json"}, # Authorization: Bearer <key> 是 OpenAI-compatible 服务的标准鉴权头.
        )as client:
            request_id = None
            # 发送一次POST
            try:
                search_resp = client.post(
                    url="/chat/completions",
                    json=self._request_payload(messages),
                )
                request_id = self._request_id(search_resp)
                search_resp.raise_for_status()   # 把HTTP层错误翻译成Python异常. 
            # 处理timeout和transport error报错.
            except httpx.TimeoutException as exc:
                return self._failure(
                    code=ProviderErrorCode.TIMEOUT,
                    message="超时",
                    retryable=True,
                    started=start_time,
                    status_code=None,   # httpx.TimeoutException 通常没有 response
                    request_id=None,
                )
            except httpx.TransportError as exc:
                return self._failure(
                    code=ProviderErrorCode.TRANSPORT_ERROR,
                    message="传输错误",
                    retryable=True,
                    started=start_time,
                    status_code=None,   # httpx.TransportError 通常没有 response
                    request_id=request_id,
                )
            except httpx.HTTPStatusError as exc:    # httpx.HTTPStatusError 通常具有 response 
                status_code = exc.response.status_code
                if status_code == 429:
                    return self._failure(
                        code=ProviderErrorCode.RATE_LIMITED,
                        message= f"HTTP {status_code}",
                        retryable= True,
                        started=start_time,
                        status_code=status_code,
                        request_id=request_id,
                    )
                elif status_code >= 500:
                    return self._failure(
                        code=ProviderErrorCode.SERVER_ERROR,
                        message= f"HTTP {status_code}",
                        retryable= True,
                        started=start_time,
                        status_code=status_code,
                        request_id=request_id,
                    )
                else:
                    return self._failure(
                        code=ProviderErrorCode.HTTP_ERROR,
                        message= f"HTTP {status_code}",
                        retryable= False,
                        started=start_time,
                        status_code=status_code,
                        request_id=request_id,
                    )
            # 检查HTTP状态码
            # search_resp.status_code
            # 读取JSON, 校验 _ChatResponse
            try:
                chat_response = _ChatResponse.model_validate(search_resp.json())
                request_id = self._request_id(search_resp) or chat_response.id # 优先响应头, 没有响应头, 那么就回退到响应体id
            except ValidationError: # ValidationError 属于 ValueError 体系, 必须先放前面接住.
                return self._failure(
                    code=ProviderErrorCode.INVALID_RESPONSE,
                    message="chat response is invalid",
                    retryable= False,
                    started=start_time,
                    request_id=request_id,
                )

            except ValueError:
                return self._failure(
                    code=ProviderErrorCode.INVALID_JSON,
                    message="search response is not a valid JSON",
                    retryable=False,
                    started=start_time,
                    request_id=request_id,
                )

            # 把响应解析为: 最终回答, 或者工具调用.
            try:
                model_action =  self._parse_action(chat_response)
            except ValueError:
                return self._failure(
                    code=ProviderErrorCode.INVALID_RESPONSE,
                    message="chat response is invalid",
                    retryable= False,
                    started=start_time,
                    request_id=request_id,
                )
            # 计算latency
            latency = self._elapsed_ms(start_time)
            # 构造成功 ModelCallResult
            model_usage = ModelUsage()
            if chat_response.usage:
                model_usage = ModelUsage(
                    input_tokens=chat_response.usage.prompt_tokens,
                    output_tokens=chat_response.usage.completion_tokens,
                    total_tokens=chat_response.usage.total_tokens,
                )

            response_model = chat_response.model
            if not response_model:
                response_model = self._config.model # 如果响应中不存在model, 那么回退到配置model.
            return ModelCallResult(
                
                action=model_action,
                failure=None,
                metadata=ModelCallMetadata(
                    model=response_model,
                    latency_ms=latency,
                    request_id=request_id,
                    usage=model_usage
                )

        )

    def _request_payload(self, messages: list[Message]) -> dict[str, Any]:
        """构造不含 API Key 的请求 JSON。"""
        # TODO 13a（L1）：转换 System Prompt、完整历史与 Registry Schema，并
        # 固定非流式、自动工具选择和温度。thinking/max_tokens 只在配置非空时
        # 出现；任何凭据都不得进入 payload。

        # Message 的定义:
        # role: Literal["user", "assistant", "tool"]
        # content: str
        # tool_call: ToolCall | None = None
        # tool_result: ToolResult | None = None
        payload_messages_list = []

        # 如果存在system prompt, 那么首先在payload_messages_list当中加入system_prompt, 
        # 然后才开始逐条添加messages消息.
        if self._config.system_prompt is not None: 
            payload_messages_list.append(
                {
                    "role": "system",
                    "content":self._config.system_prompt,
                }
            )
        # 历史消息先交给已有的单条消息转换逻辑；13a 不需要提前实现 13b 的严格配对校验。
        # for message in messages:
        #     payload_messages_list.append(
        #         self._convert_message(message)
        #     )
        # 替换为13b的方法.
        payload_messages_list.extend(self._convert_messages(messages))  # 使用extend, 把每个元素逐个追加到末尾
        tool_schemas_list = self._registry.schemas()

        payload_tools_list = [
            {"type": "function", "function": tool_schema}
            for tool_schema in tool_schemas_list
        ]
        
        openai_compatible_dict = {
            "model":self._config.model,
            "messages":payload_messages_list,
            "tools":payload_tools_list,
            "tool_choice":"auto",
            "temperature":self._config.temperature,
            "stream":False,
        }
        # thinking 和 max_tokens 是“配置非空才加入”，不是永远放进去。
        if self._config.thinking:
            openai_compatible_dict["thinking"] = {"type":self._config.thinking} # 测试契约要求:payload["thinking"] == {"type": "disabled"}
        if self._config.max_tokens:
            openai_compatible_dict["max_tokens"] = self._config.max_tokens

        return openai_compatible_dict
        """
        这次请求 JSON 的外壳大致是：
        {
            "model": "配置中的模型名",
            "messages": [
                {"role": "system", "content": "可选的系统提示"},
                {"role": "user", "content": "用户问题"}
            ],
            "tools": [
                {
                "type": "function",
                "function": {
                    "name": "工具名",
                    "description": "工具说明",
                    "parameters": {}
                }
                }
            ],
            "tool_choice": "auto",
            "temperature": 0.0,
            "stream": false
        }
        """

    def _convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """转换完整历史并验证工具调用与结果的严格顺序。"""
        # TODO 13b（L1）：
        # 模型工具调用协议要求:
        # assistant.tool_call(call_id, tool_name) 后面紧跟着
        # tool.tool_result(call_id, tool_name), 其中call_id和tool_name完全一致.
        # 模型工具调用协议拒绝:
        # 孤立: tool 单独出现
        # 缺失: assistant 调用后没有 tool result
        # 错配: call_id 不一致, 或者工具名不一致.
        # 混入: assistant消息和tool消息之间, 混入其他角色(用户, 或者其他tool)的消息.
        # 重复: 同一个call_id被反复使用
        list_dict = []
        pending_tool_call = None
        # pending_tool_call 形如:
        #         {
        #             "id": "call-1",
        #             "type": "function",
        #             "function": {
        #                 "name": "calculate_discount",
        #                 "arguments": "{\"percent\":10,\"price\":100}",
        #             },
        #         }
        seen_call_ids = set()
        for msg in messages:

            # msg 和 转换后的字典 形如:
            # region
            # """
            # 1. user消息
            # Message(role="user", content="帮我计算折扣")
            # 转换后:
            # {
            #     "role": "user",
            #     "content": "帮我计算折扣",
            # }
            
            # 2.普通 assistant 消息
            # Message(role="assistant", content="我来帮你计算")
            # 转换后:
            # {
            #     "role": "assistant",
            #     "content": "我来帮你计算",
            # }
    
            # 3.带工具调用的 assistant 消息
            # Message(
            #     role="assistant",
            #     content="我来计算一下",
            #     tool_call=ToolCall(
            #         call_id="call-1",
            #         name="calculate_discount",
            #         arguments={"price": 100, "percent": 10},
            #     ),
            # )     
            # 转换后:
            # {
            #     "role": "assistant",
            #     "content": "我来计算一下",   ## 或者 "content": None
            #     "tool_calls": [
            #         {
            #             "id": "call-1",
            #             "type": "function",
            #             "function": {
            #                 "name": "calculate_discount",
            #                 "arguments": "{\"percent\":10,\"price\":100}",
            #             },
            #         }
            #     ],
            # }
    
            # 4.tool结果消息
            # Message(
            #     role="tool",
            #     content='{"final_price":90}',
            #     tool_result=ToolResult(
            #         call_id="call-1",
            #         tool_name="calculate_discount",
            #         ok=True,
            #         attempts=1,
            #         output={"final_price": 90},
            #     ),
            # )
            # 转换后:
            # {
            #     "role": "tool",
            #     "tool_call_id": "call-1",
            #     "content": "{\"final_price\":90}",
            # }
    
            # 5.tool 结果必须使用与 assistant 调用相同的 tool_call_id
            # assistant.tool_calls[0]["id"] == tool.tool_call_id
            #                 "call-1"         ==    "call-1"
            # """
            # endregion

            if pending_tool_call:
                if msg.role in ("assistant", "user"):
                    role = msg.role
                    raise ValueError(f"当前已经有pending_tool_call, 但是遇到了{role}")
                if msg.role == "tool":
                    # 4.tool结果消息
                    # Message(
                    #     role="tool",
                    #     content='{"final_price":90}',
                    #     tool_result=ToolResult(
                    #         call_id="call-1",
                    #         tool_name="calculate_discount",
                    #         ok=True,
                    #         attempts=1,
                    #         output={"final_price": 90},
                    #     ),
                    # )

                    #     pending_tool_call 形如 
                    #     ToolCall(
                    #         call_id="call-1",
                    #         name="calculate_discount",
                    #         arguments={"price": 100, "percent": 10},
                    #     ),

                    # 本消息是tool发送的tool结果消息, 不是assistant发送的tool调用消息.
                    if msg.tool_result is not None:
                        if msg.tool_result.call_id != pending_tool_call.call_id:
                            raise ValueError("错配: 带tool_call的assistant消息中的call_id, 和tool_result消息中的call_id 不一致")
                        if msg.tool_result.tool_name != pending_tool_call.name:
                            raise ValueError("错配: 带tool_call的assistant消息中的call_id, 和tool_result消息中的call_id 不一致")
                        list_dict.append(self._convert_message(msg))
                        pending_tool_call = None
                    elif msg.tool_call is not None:
                        raise ValueError("tool发送的消息中出现了本应由assistant发送的tool调用消息")
                    else:
                        raise ValueError("msg.role == 'tool', 但是msg.tool_result和msg.tool_call均为空")
            else:
                # 没有pending_tool_call            
                if msg.role == "assistant":
                    if msg.tool_call is not None:
                        pending_tool_call = msg.tool_call   
                        #     msg.tool_call 形如 
                        #     ToolCall(
                        #         call_id="call-1",
                        #         name="calculate_discount",
                        #         arguments={"price": 100, "percent": 10},
                        #     ),

                        if msg.tool_call.call_id in seen_call_ids:
                            raise ValueError("复用: 同一个call_id被反复使用")
                        seen_call_ids.add(msg.tool_call.call_id)
                        list_dict.append(self._convert_message(msg))
                    else:
                        list_dict.append(self._convert_message(msg))

                if msg.role == "tool":
                    raise ValueError("孤立tool: 没有pending_tool_call, 却得到了tool的消息.")
                if msg.role == "user":
                    list_dict.append(self._convert_message(msg))

        if pending_tool_call is not None:
            raise ValueError("带有tool_call的assistant, 缺失对应的tool_result")
        return list_dict


    @staticmethod
    def _convert_message(message: Message) -> dict[str, Any]:
        """无损转换 Kaiji 消息，并使用同一 ``tool_call_id`` 配对结果。"""
        # TODO 13b（L1）：分别转换 user、普通 assistant、带单个 tool_call 的
        # assistant 与 tool 消息。参数必须是稳定 JSON 字符串，tool 结果必须
        # 使用与 assistant 调用相同的 tool_call_id。完整历史中工具调用后必须
        # 紧跟同 ID、同工具名的唯一 result；孤立/缺失/错配/插入其他角色和
        # 复用 ID 均在发送前拒绝。

        # Message 的定义:
        # role: Literal["user", "assistant", "tool"]
        # content: str
        # tool_call: ToolCall | None = None
        # tool_result: ToolResult | None = None
        msg_dict = dict()
        msg_dict["role"] = message.role
        if message.role == "user":
            msg_dict["content"] = message.content

        elif message.role == "assistant":
            if message.tool_call is not None:   # assistant带有工具调用.
                msg_dict["content"] = message.content or None   # content 不要无条件丢弃，应保留非空的 assistant 文本
                # OpenAI-compatible 协议将工具调用定义为列表；
                # 当前 Kaiji 只允许单个工具调用，因此这里构造长度为 1 的列表。
                msg_dict["tool_calls"] = [
                    {
                        "id":message.tool_call.call_id,
                        "type":"function",
                        "function":{
                            "name":message.tool_call.name,
                            "arguments":json.dumps(
                                message.tool_call.arguments, 
                                sort_keys=True,         # 对key进行排序确保稳定性
                                separators=(",",":"),   # 使用紧凑分隔符
                                allow_nan=False,            
                                ensure_ascii=False,
                            ),
                        }
                    }
                ]
            else:                               # assistant没有工具调用
                msg_dict["content"] = message.content
                
        else: # message.role == "tool"
            result = message.tool_result    # 由于Message本身的定义, role为"tool"时, tool_result一定存在.
            assert result is not None
            msg_dict["content"] = message.content
            msg_dict["tool_call_id"] = result.call_id

        return msg_dict
        # region
        """
        1. user消息
        Message(role="user", content="帮我计算折扣")
        转换后:
        {
            "role": "user",
            "content": "帮我计算折扣",
        }
        
        2.普通 assistant 消息
        Message(role="assistant", content="我来帮你计算")
        转换后:
        {
            "role": "assistant",
            "content": "我来帮你计算",
        }

        3.带工具调用的 assistant 消息
        Message(
            role="assistant",
            content="我来计算一下",
            tool_call=ToolCall(
                call_id="call-1",
                name="calculate_discount",
                arguments={"price": 100, "percent": 10},
            ),
        )     
        转换后:
        {
            "role": "assistant",
            "content": "我来计算一下",   ## 或者 "content": None
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "calculate_discount",
                        "arguments": "{\"percent\":10,\"price\":100}",
                    },
                }
            ],
        }

        4.tool结果消息
        Message(
            role="tool",
            content='{"final_price":90}',
            tool_result=ToolResult(
                call_id="call-1",
                tool_name="calculate_discount",
                ok=True,
                attempts=1,
                output={"final_price": 90},
            ),
        )
        转换后:
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "content": "{\"final_price\":90}",
        }

        5.tool 结果必须使用与 assistant 调用相同的 tool_call_id
        assistant.tool_calls[0]["id"] == tool.tool_call_id
                     "call-1"         ==    "call-1"
        """
        # endregion

    @staticmethod
    def _parse_action(response: _ChatResponse) -> ModelAction:
        """把唯一候选解析为最终回答或至多一个工具调用。"""
        # TODO 13c（L1）：只接受一个 assistant choice；工具路径至多一个
        # function call，arguments 必须解析为有限 JSON 对象且 finish_reason 配对；
        # 无工具路径必须有非空白文本并正常 stop。未消费的兼容扩展字段应被忽略；
        # 保留 assistant_content。

        # _ChatResponse
        # └── choices: list[_Choice]
        #     └── message: _ResponseMessage
        #         ├── role
        #         ├── content
        #         └── tool_calls: list[_ResponseToolCall] | None
        # 其中, _Choice形如:
        # _Choice
        #     ├── message: _ResponseMessage
        #     └──finish_reason: str | None = None
        # 其中, _ResponseToolCall 形如:
        # _ResponseToolCall
        # ├── id
        # ├── type
        # └── function (_FunctionCall)
        #     ├── name
        #     └── arguments: str  # 这是一个JSON字符串, 不是字典.
        # 其中, _ResponseMessage 形如:
        # _ResponseMessage
        #     ├── role: str
        #     ├── content: str | None = None
        #     └── tool_calls: list[_ResponseToolCall] | None = None

        if len(response.choices) != 1:
            raise ValueError("只接受一个 assistant choice")
        choice = response.choices[0]
        message = choice.message
        tool_calls = message.tool_calls or []
        has_tool_calls = bool(tool_calls)
        finish_reason = choice.finish_reason
        
        if choice.message.role != "assistant":
            raise ValueError("最终回答和工具调用的role应当是assistant")

        if has_tool_calls:
            if finish_reason not in {"tool_calls", None}:
                raise ValueError("包含tool_call时, finish_reason 只能是 tool_calls 或者 None.")
        else:
            if finish_reason not in {"stop", None}:
                raise ValueError("不包含tool_call时, finish_reason 只能是 stop 或者 None.")

        # 路径1 最终回答
        # region
        # 其中, choices列表形如:
        # {
        # "choices": [
        #     {
        #     "message": {
        #         "role": "assistant",
        #         "content": "折后价格是 90 元"
        #     },
        #     "finish_reason": "stop"
        #     }
        # ]
        # }
        # 情况1输出: 
        # ModelAction(
        #     kind="final_answer",
        #     final_answer="折后价格是 90 元",
        # )
        # endregion
        if not has_tool_calls:

            final_answer = choice.message.content
            if final_answer is None:
                raise ValueError("最终回答choice.message.content不存在.")
            final_answer = final_answer.strip()
            if not final_answer:
                raise ValueError("最终回答choice.message.content必须至少包含一个非空白字符.")
            return ModelAction(
                kind="final_answer",
                final_answer=final_answer,
            )

        # 路径2 工具调用
        # region
        # 其中, choices列表形如:
        # {
        # "choices":[
        #     {
        #         "message":{
        #             "role":
        #             "content":
        #             "tool_calls":[
        #                 {
        #                     "id":
        #                     "type":
        #                     "function":{
        #                         "name":
        #                         "arguments":
        #                     }
        #                 }
        #             ]
        #         }

        #     }
        # ]
        # }
        # 输出:
        # ModelAction(
        #     kind="tool_call",
        #     tool_call=ToolCall(
        #         call_id=
        #         name=
        #         arguments=
        #     )
        #     final_answer=
        #     assistant_content=
        # )
        # endregion
        else:
            if len(tool_calls) > 1:
                raise ValueError("只允许一个工具调用")
            if tool_calls[0].type != "function":
                raise ValueError("工具类型必须是function")   
            return ModelAction(
                kind = "tool_call",
                tool_call=ToolCall(
                    call_id=tool_calls[0].id,
                    name=tool_calls[0].function.name,
                    arguments=json.loads(tool_calls[0].function.arguments),
                ),
                assistant_content=choice.message.content or ""
            )
        
    def _failure(
        self,
        code: ProviderErrorCode,
        message: str,
        *,
        retryable: bool,
        started: float,
        status_code: int | None = None,
        request_id: str | None = None,
    ) -> ModelCallResult:
        """构造不包含请求正文、响应正文或鉴权头的失败结果。"""

        return ModelCallResult(
            failure=ProviderFailure(
                code=code,
                message=message,
                retryable=retryable,
                status_code=status_code,
            ),
            metadata=ModelCallMetadata(
                model=self._config.model,
                latency_ms=self._elapsed_ms(started),
                request_id=request_id,
            ),
        )

    @staticmethod
    def _request_id(response: httpx.Response) -> str | None:
        """读取常见请求标识响应头，不读取可能含敏感内容的正文。"""

        return response.headers.get("x-request-id") or response.headers.get(
            "request-id"
        )

    @staticmethod
    def _elapsed_ms(started: float) -> float:
        """返回非负毫秒延迟。"""

        return max(0.0, (perf_counter() - started) * 1000)
