"""定义 Azusa 语义接口、离线 Mock 和当前 Rust 服务的兼容适配器. """

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any, Protocol
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field, StrictInt, StrictStr, field_validator


def _non_blank(value: object, field_name: str) -> str:
    """拒绝空值和纯空白字符串, 同时不改写合法字符串. """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 必须是非空且非纯空白字符串")
    return value


class SearchHit(BaseModel):
    """表示一条由 Azusa 返回并可作为证据使用的检索命中. """

    chunk_id: StrictStr
    text: StrictStr
    score: int | float | None = None
    source: str | None = None
    page: StrictInt | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    _valid_strings = field_validator("chunk_id", "text")(
        lambda value, info: _non_blank(value, info.field_name)
    )
    # 等价于 @field_validator("chunk_id", "text") 装饰一个 def:
    #   @field_validator("chunk_id", "text")
    #   def _valid_strings(value, info):
    #       return _non_blank(value, info.field_name)
    # 原理: field_validator("chunk_id", "text") 先返回一个装饰器, 再立刻作用于 lambda
    # (函数体只有一行, 所以用匿名函数内联); 签名不带 cls 也可以, pydantic v2
    # 按函数签名决定传参. 名字以 _ 开头是私有约定, 不会被当成模型字段.

    @field_validator("score", mode="before")
    @classmethod
    def score_is_finite_number(cls, value: object) -> object:
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
            raise ValueError("score 必须是有限数值")
        return value


class SearchResponse(BaseModel):
    """封装 Azusa 检索命中及实际使用的检索模式. """

    hits: list[SearchHit]
    mode: StrictStr

    _valid_mode = field_validator("mode")(
        lambda value, info: _non_blank(value, info.field_name)
    )
    # 等价于 @field_validator("mode") 装饰一个 def:
    #   @field_validator("mode")
    #   def _valid_mode(value, info):
    #       return _non_blank(value, info.field_name)
    # 原理: field_validator("mode") 先返回一个装饰器, 再立刻作用于 lambda
    # (函数体只有一行, 所以用匿名函数内联); 签名不带 cls 也可以, pydantic v2
    # 按函数签名决定传参. 名字以 _ 开头是私有约定, 不会被当成模型字段.


class ChunkResponse(BaseModel):
    """表示按稳定 chunk_id 取回的完整证据正文与元数据. """

    chunk_id: StrictStr
    text: StrictStr
    source: str | None = None
    page: StrictInt | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    _valid_strings = field_validator("chunk_id", "text")(
        lambda value, info: _non_blank(value, info.field_name)
    )
    # 等价于 @field_validator("chunk_id", "text") 装饰一个 def:
    #   @field_validator("chunk_id", "text")
    #   def _valid_strings(value, info):
    #       return _non_blank(value, info.field_name)
    # 原理: field_validator("chunk_id", "text") 先返回一个装饰器, 再立刻作用于 lambda
    # (函数体只有一行, 所以用匿名函数内联); 签名不带 cls 也可以, pydantic v2
    # 按函数签名决定传参. 名字以 _ 开头是私有约定, 不会被当成模型字段.


class AzusaAdapterError(Exception):
    """表示适配器边界内的失败, 并保留是否适合重试. """

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable

    @property
    def retryable(self) -> bool:
        """返回严格布尔类型的重试标记. """

        return self._retryable

    @retryable.setter
    def retryable(self, value: bool) -> None:
        """只允许真正的 bool, 避免错误真值进入工作流状态. """

        if type(value) is not bool:
            raise TypeError("retryable 必须是真正的 bool")
        self._retryable = value


class AzusaAdapter(Protocol):
    """规定 Kaiji 依赖的两个稳定 Azusa 语义调用. """

    def search_azusa(self, query: str, k: int, mode: str) -> SearchResponse: ...

    def get_chunk_by_id(self, chunk_id: str) -> ChunkResponse: ...


class _LegacySearchItem(BaseModel):
    """校验旧版 ``/search`` 中一条检索结果的必要字段. """

    id: StrictInt | StrictStr
    score: int | float | None = None

    @field_validator("id")
    @classmethod
    def id_is_valid(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            raise ValueError("id 不得为空或纯空白")
        return value

    @field_validator("score", mode="before")
    @classmethod
    def score_is_finite_number(cls, value: object) -> object:
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
            raise ValueError("score 必须是有限数值")
        return value


class _LegacySearchResponse(BaseModel):
    """校验旧版 ``/search`` 的响应外壳. """

    results: list[_LegacySearchItem]


class _LegacyRecordResponse(BaseModel):
    """校验旧版 ``/record/{id}`` 中 Kaiji 依赖的元数据. """

    id: StrictInt | StrictStr
    metadata: dict[str, Any]

    @field_validator("id")
    @classmethod
    def id_is_valid(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            raise ValueError("id 不得为空或纯空白")
        return value


class AzusaHttpAdapter:
    """调用 Kaiji 期望的稳定 Azusa 语义 HTTP 契约. 

    对应的薄网关刻意位于 Kaiji 仓库之外, 并暴露
    POST /search_azusa 与 GET /chunks/{chunk_id}. 
    """

    def __init__(
        self,
        base_url: str,  # 网关根地址, 例如 "http://127.0.0.1:38117"
        *,
        timeout_seconds: float = 3.0,  # 单次 HTTP 操作的等待上限(秒), 不是整个 handler 的总期限
        transport: httpx.BaseTransport | None = None,  # 注入自定义传输层; 测试时传入 MockTransport, 否则使用默认 HTTP
    ) -> None:
        self._client = httpx.Client(  # 同步 HTTP 客户端, 内部维护连接池, 复用 TCP 连接
            base_url=base_url.rstrip("/"),  # 去掉末尾斜杠, 避免后续拼接路径时出现 "//" 双斜杠
            timeout=timeout_seconds,
            transport=transport,  # 为 None 时 httpx 使用默认的真实 HTTP 传输
        )

    def search_azusa(self, query: str, k: int, mode: str) -> SearchResponse:
        """通过语义网关搜索证据, 并把边界失败映射为重试策略. 

        TODO 7a 的输入必须原样组成 ``query/k/mode`` JSON; 成功返回值必须由
        ``SearchResponse`` 校验. Adapter 只分类并抛出 ``AzusaAdapterError``, 
        不在这里循环重试：timeout、network、HTTP 429/5xx、非法 JSON 和响应 Schema
        不匹配标记为可重试; HTTP 3xx 和除 429 外的其他 4xx 标记为不可重试. 
        """
        # TODO 7a：实现 POST /search_azusa、响应校验和上述完整错误分类. 
        try:
            response = self._client.post(
                url="/search_azusa", 
                json={
                    "query":query,
                    "k":k,
                    "mode":mode
                }
            )
            # httpx 不会因 4xx/5xx 自动抛异常(它认为"拿到响应就是成功"), 必须手动
            # 调用 raise_for_status 把非 2xx 变成 HTTPStatusError, 交给下方 except 分类.
            # 若省略此行: 503 的 HTML 响应体会直接进 model_validate, JSON 解析失败抛
            # ValueError, 被误判为"响应格式不对, retryable=True".
            response.raise_for_status()
            return SearchResponse.model_validate(response.json())
        except httpx.TimeoutException as exc:  # 网络超时. TimeoutException是RequestError的子类, 必须在RequestError的前面. 
            raise AzusaAdapterError(f"Azusa search timed out:{str(exc)}", retryable=True) from exc # retryable=True:可能是网络波动, 再试一次可能成功. from exc可以保持调用链, 允许报错的时候看到消息异常.
        except httpx.HTTPStatusError as exc:   # 服务器收到, 但报告请求本身有问题(4xx)
            status_code = exc.response.status_code
            retryable = status_code == 429 or status_code >= 500
            raise AzusaAdapterError(f"Azusa search returned HTTP {status_code}", 
                                    retryable=retryable, ) from exc   # 429 和 5xx 可重试; 3xx 与除 429 外的其他 4xx 不可重试. 因为请求的chunk_id不存在, 无论尝试多少次, 都是404
        except httpx.RequestError as exc:   # 请求根本没发出去, 或者发了一半断了(网线被拔了 / DNS 炸了 / 连接被拒). retryable=True: 换个时间发送, 可能成功.
            raise AzusaAdapterError(f"Azusa search returned RequestError: {str(exc)}", retryable= True) from exc
        # HTTP收到了200 OK, 但是JSON解析失败, 或者字段类型不匹配:
        except ValueError as exc:           # 不是合法的JSON
            raise AzusaAdapterError(f"Azusa search returned ValueError: {str(exc)}", retryable= True) from exc
        except TypeError as exc:            # 字段类型不匹配
            raise AzusaAdapterError(f"Azusa search returned TypeError: {str(exc)}", retryable= True) from exc

    def get_chunk_by_id(self, chunk_id: str) -> ChunkResponse:
        """按稳定 ``chunk_id`` 读取完整证据, 并防止证据错配. 

        TODO 7b 必须把 ``chunk_id`` 当作单个 URL 路径段编码, 再校验
        ``ChunkResponse``; 响应中的 ``chunk_id`` 还必须与请求值完全一致. 
        错误分类和“不在 Adapter 内重试”的边界与 TODO 7a 相同. 
        """

        # TODO 7b：实现 GET /chunks/{encoded_chunk_id}、响应校验和错误分类. 

        try:
            encoded_id = quote(chunk_id, safe="") # quote('abc def') -> 'abc%20def'
            response = self._client.get(f"/chunks/{encoded_id}") # 把 chunk_id 当作单个 URL 路径段编码. safe="" 的意思是所有特殊字符都编码, 包括 / . 
                                                              # 因为 chunk_id 是整个路径段(形如"folder/chunk 1"), 里面的 / 不能当路径分隔符. 在
                                                              # 经过quote编码后, "/chunks/folder/chunk 1"被编码成f"/chunks/folder%2Fchunk%201", 
                                                              # 服务器明确知道chunks/ 后面整个 folder%2Fchunk%201 就是一个路径段, 解码后得到 folder/chunk 1
            response.raise_for_status()
            chunk = ChunkResponse.model_validate(response.json())
            # TODO: 这样写raise可能会吞掉AzusaAdapterError吗?
            if chunk.chunk_id != chunk_id:
                raise AzusaAdapterError(
                    f"Azusa chunk response ID {chunk.chunk_id!r} does not match requested {chunk_id!r}",
                    retryable=True, 
                )
            return chunk
        
        except httpx.TimeoutException as exc:
            raise AzusaAdapterError(f"Azusa search timed out:{str(exc)}", retryable=True) from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            retryable = status_code == 429 or status_code >= 500
            raise AzusaAdapterError(f"Azusa search returned HTTP {status_code}", 
                                    retryable=retryable) from exc
        except httpx.RequestError as exc:
            raise AzusaAdapterError(f"Azusa search returned RequestError: {str(exc)}", retryable= True) from exc
        except ValueError as exc:
            raise AzusaAdapterError(f"Azusa search returned ValueError: {str(exc)}", retryable= True) from exc
        except TypeError as exc:
            raise AzusaAdapterError(f"Azusa search returned TypeError: {str(exc)}", retryable= True) from exc

    def close(self) -> None:
        """关闭底层 HTTP 客户端连接池. """

        self._client.close()


class LegacyAzusaVectorHttpAdapter:
    """兼容 Azusa 当前 Rust 演示 HTTP API 的适配器. 

    当前 Azusa 的 POST /search 接收向量, 因此必须注入 query encoder, 而且只表达
    HNSW 模式. 记录元数据必须包含 chunk_id; Rust 服务不保存 chunk 正文, 所以还要
    注入本地 text resolver. 
    只表达HNSW模式的原因: 旧版Rust服务的POST /search 请求体当中
    只包括vector, k, ef_search, 并不包含mode字段.
    """

    def __init__(
        self,
        base_url: str,
        *,
        query_encoder: Callable[[str], list[float]],  # 此乃构造参数, 不是由adapter自己实现或者import的. 谁创建adapter, 谁就把函数传进来.
        text_resolver: Callable[[str], str],          # 此乃构造参数, 不是由adapter自己实现或者import的. 谁创建adapter, 谁就把函数传进来.
        timeout_seconds: float = 3.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._encoder = query_encoder
        self._text_resolver = text_resolver
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            transport=transport,
        )

    def _resolve_text(self, chunk_id: str) -> str:
        """
        TODO 7c
        
        背景:
        resolve: 解析; resolver: 解析器.
        Azusa的旧Rust服务一共只有两个端点: POST /search, 返回record ID和分数; 
        GET /record/{id}, 返回record ID和metadata字典.
        注意 metadata 分两层看待:
        - 契约层: 代码里是 dict[str, Any], 即不保证任何固定字段, 只有这一条是契约;
          adapter 契约上只依赖 chunk_id 非空、非纯空白.
        - 实现层: 服务端当前实际放 chunk_id, source, page 等字段, 但这是行为, 不是保证.
        这两个端点都不会返回正文. 因此需要 _resolve_text这个函数寻找正文.

        函数功能:
        _resolve_text接收一个chunk_id字符串, 返回这个chunk的正文.
        它负责调用resolver, 校验resolver的输出, 统一包装resolver错误.

        具体说明:
        resolver是调用方在构造 adapter 时通过构造函数注入的 Python callable(依赖注入).
        resolver在本进程内执行, 从本地文件/数据库中读取chunk正文.
        _resolve_text只校验和使用resolver, 不实现resolver. 约定resolver 返回非空、非纯空白字符串.
        如果resolver失败, 表明本地数据有问题, 再重试HTTP也会失败.
        因此resolver抛出的异常或非法返回值需要统一包装成 retryable = False 的``AzusaAdapterError``, 
        不能把它误判为远端瞬时故障. 
        """
        try:
            text = self._text_resolver(chunk_id)
        except Exception as exc:
            raise AzusaAdapterError(
                f"Text resolver raised error: {str(exc)}",
                retryable=False,
            ) from exc
        if not isinstance(text, str) or not text.strip(): # resolver 可能返回 None、返回 7、返回 ["abc"], 所以需要检查类型
            raise AzusaAdapterError(
                f"Text resolver must return non-void and not-empty str",
                retryable=False,
            )
        return text

    def _encode_query(self, query: str) -> list[float]:
        """
        TODO 7c

        背景:
        encode: 编码; encoder: 编码器.
        Azusa的旧Rust服务的POST /search 只接收向量, 不接收文本query.
        它是纯HNSW向量索引, 请求体只有 vector/k/ef_search.
        因此需要 _encode_query 这个函数把自然语言 query 变成向量.

        函数功能:
        _encode_query接收一个query字符串, 返回编码后的向量(list[float]).
        它负责调用encoder, 校验encoder的输出, 统一包装encoder错误.

        具体说明:
        encoder是调用方在构造 adapter 时通过构造函数注入的 Python callable(依赖注入).
        encoder在本进程内执行, 通常是embedding模型.
        _encode_query只校验和使用encoder, 不实现encoder.
        约定encoder返回非空 ``list[float]``, 且元素必须是有限数值(int/float);
        bool、NaN、Inf和其他错误类型都要拒绝——注意 isinstance(True, int) 为 True,
        bool 是 int 的子类, 不能算作数值.
        如果encoder失败, 表明本地数据/模型有问题, 再重试HTTP也会失败.
        因此encoder抛出的异常或非法返回值需要统一包装成 retryable = False 的``AzusaAdapterError``,
        不能把它误判为远端瞬时故障.
        该方法只做编码和质检, 不得触发 HTTP、resolver 或 record 转换.

        返回值举例:
        合法: [1, 2.0] , 最后统一成 [1.0, 2.0]
        不合法: 
            []
            [True]
            [float("nan")]
            [float("inf")]
            ["1"]
            (1, 2)
            None
        """
        try:
            encoded_list = self._encoder(query) # 调用注入的encoder
        except Exception as exc:
            raise AzusaAdapterError(
                f"Query encoder raised error: {str(exc)}",
                retryable=False,
            ) from exc

        # 质检: 返回值必须是非空 list、每个元素是有限数值(int/float)
        # 1.必须是list
        if not isinstance(encoded_list, list):
            raise AzusaAdapterError(
                f"encoded_list is not a list",
                retryable=False,
            )

        # 2.必须非空
        if not encoded_list:
            raise AzusaAdapterError(
                f"encoded_list is empty",
                retryable=False,
            )

        # 3.不得存在元素: (是bool) 或 (不是 int或float) 或 (是 nan或者inf)
        # isinstance(True, int) 和 isinstance(False, int) 都会返回True, 因为bool是int的子类.
        # nan和inf应当使用math.isfinite方法识别并拒绝.
        for value in encoded_list:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise AzusaAdapterError(
                    f"Value in encoded_list is invalid. Should not be bool, not be nan or inf. Should be int or float.",
                    retryable=False,
                )
        return [float(value) for value in encoded_list]

    def search_azusa(self, query: str, k: int, mode: str) -> SearchResponse:
        """
        TODO 7c

        背景:
        Azusa的旧Rust服务一共只有两个端点: POST /search, 只接收向量,
        返回record ID和分数; GET /record/{id}, 返回record ID和metadata字典.
        服务端不保存正文, 也不能处理文本 query, 所以完整的语义检索要靠拼装:
        encoder 把文本变向量, resolver 从本地补正文. 本函数就是拼装全流程的
        入口, 把旧服务的"半残"输出恢复成 Kaiji 的 SearchResponse.

        函数功能:
        search_azusa接收query/k/mode, 返回SearchResponse.
        它负责检查mode、经_encode_query调用encoder、分两阶段调用HTTP
        (search + 每条命中的record)、经_resolve_text调用resolver、
        校验每一步的返回、统一包装所有错误.

        输入形如:
        query="What is HNSW?"
        k=2
        mode="hnsw"

        输出形如:
        SearchResponse(
            hits=[
                SearchHit(
                    chunk_id="chunk-a",
                    score=0.91,
                    text="正文",
                    source="文档名",
                    page=3,
                    metadata={
                        "...": "...",
                        "record_id": 7,
                    },
                )
            ],
            mode="hnsw",
        )

        工作流形如:
        用户查询
        1. 检查mode只能是hnsw
        2. encoder把query编码成向量
        3. POST /search, 返回record_id和score
        4. 对每条命中 GET /record/{id}, 取metadata
        5. 从metadata中找chunk_id
        6. resolver(chunk_id)从本地取正文
        7. 组装成Kaiji的SearchResponse

        具体说明:
        mode只接受 ``hnsw`` (旧服务只表达HNSW检索), 其他值一律包装成
        retryable=False的``AzusaAdapterError``.
        encoder/resolver是调用方通过构造函数注入的callable(依赖注入),
        必须通过``self._encode_query(query)``和``self._resolve_text(chunk_id)``
        调用, 不得绕过helper复制它们的校验逻辑.
        encoder必须返回非空有限数值列表(布尔值不算数值); record ID必须与
        命中ID一致; ``metadata.chunk_id``必须是非空、非纯空白字符串;
        命中的完整metadata要保留并追加原始``record_id``, 同时映射
        ``score/source/page``; 路径中的record ID必须编码.
        encoder/resolver的异常或非法返回不可重试(本地确定性问题, 重试无用);
        远端HTTP两阶段沿用TODO 7a的错误分类: timeout/network/HTTP 429/5xx
        标记为可重试, 其他4xx标记为不可重试.
        并发、缓存和连接池调优不在练习范围.
        """
        # 第1步: 检查mode
        if mode != "hnsw":
            raise AzusaAdapterError("mode must be hnsw", retryable=False)
        # 第2步: 对query进行编码后, 得到向量
        encoded_query = self._encode_query(query)
        try:
            # 第3步: 调用旧接口 POST /search, 得到多个 {id, score}.
            search_resp = self._client.post(   # search_resp 的类型是 httpx.Response(HTTP 响应对象), 通过 .json() 转换为 dict
                "/search",
                json={
                    "vector": encoded_query,
                    "k": k,
                    "ef_search": 16,
                }
            )

            # 关于search_resp.raise_for_status():
            # httpx 不会因 4xx/5xx 自动抛异常(它认为"拿到响应就是成功"), 必须手动
            # 调用 raise_for_status 把非 2xx 变成 HTTPStatusError, 交给下方 except 分类.
            # 若省略此行: 503 的 HTML 响应体会直接进 model_validate, JSON 解析失败抛
            # ValueError, 被误判为"响应格式不对, retryable=True".
            search_resp.raise_for_status()

            search_results = _LegacySearchResponse.model_validate(search_resp.json())
            # search_results 是 _LegacySearchResponse 实例, 有 .results 属性
            # search_results.results 形如:
            # [ _LegacySearchItem(id=7, score=0.9), _LegacySearchItem(id="abc", score=0.83) ]
            # search_resp.json() 返回 dict, 形如:
            # {"results": 
            #   [
            #       {"id": 7, "score": 0.9}, 
            #       {"id": "abc", "score": 0.83}
            #   ]
            # } 

            hits: list[SearchHit] = []
            # 第4步: 对每条结果, 调用 GET /record/{encoded_id}, 得到metadata.
            # 全有或全无: 任一条 /record 失败, 整个 search_azusa 返回失败(异常直接传播, 不会返回半截结果).
            # 本循环会出现 4 个 id, 先记住角色表:
            #   search_result.id      POST /search 命中项 id (int 或 str)
            #   record.id             GET /record/{id} 返回的 id, 必须与 search_result.id 完全一致 (身份核对)
            #   metadata.chunk_id     record.metadata 中的字段, Kaiji 稳定证据标识, 第5步取正文用
            #   metadata["record_id"] record.metadata 中的保留字段, 第7步写入, 值 = search_result.id
            for search_result in search_results.results:
                encoded_id = quote(str(search_result.id), safe="")  # safe="" 表示连 / 也编码; 否则 / 会被误认为路径分隔符。
                record_resp = self._client.get(f"/record/{encoded_id}")
                record_resp.raise_for_status()
                record = _LegacyRecordResponse.model_validate(record_resp.json())
                # 数据结构说明: _LegacyRecordResponse 是 Pydantic 模型, 不是 dict; 字段: id: StrictInt | StrictStr, metadata: dict[str, Any]
                # 校验完毕后, record 是一个 _LegacyRecordResponse 实例, 形如:
                # record = {
                #     "id": 7,
                #     "metadata": {
                #         "chunk_id": "chunk-a",
                #         "source": "paper.pdf",
                #         "page": 3,
                #         "author": "..."
                #     }
                # }


                # 下面三项校验的失败都是远端响应自相矛盾(不是调用者参数错误),
                # 测试契约要求统一视为无效响应, retryable=True.

                # 校验1: /search 和 /record 的结果是不是正确匹配?
                #   search_result.id  ← POST /search 命中的 id (可能是 int 或 str)
                #   record.id         ← GET /record/{id} 返回的 id (可能是 int 或 str)
                # 取回的 record 必须与命中的 id 类型和值都完全一致 (7 与 "7" 不匹配),
                # 否则说明两次响应自相矛盾, 是远端数据问题.
                if not (type(search_result.id) is type(record.id) and search_result.id == record.id):
                    raise AzusaAdapterError("search_result.id (from POST /search) does not match record.id (from GET /record/)", retryable=True) 

                # 校验2: metadata.chunk_id 是否存在?
                metadata = record.metadata
                raw_chunk_id = metadata.get("chunk_id")
                if raw_chunk_id is None:
                    raise AzusaAdapterError("metadata.chunk_id does not exist",retryable=True)

                # 校验3: chunk_id 是否确实是非空字符串？
                if not (isinstance(raw_chunk_id, str) and raw_chunk_id.strip()):
                    raise AzusaAdapterError("metadata.chunk_id should be a non-blank string", retryable=True)

                # 校验4: metadata 是否已经占用了保留字段 record_id?
                # 保留字段 = Kaiji 预留给自己的键名(第7步要写入), 远端禁止占用;
                # 若被占用, 写入时会静默覆盖远端数据, 故视为无效响应直接拒绝.
                if "record_id" in metadata:
                    raise AzusaAdapterError("metadata occupied the 'record_id' key", retryable=True)

                # 第5步: 从metadata中找chunk_id
                chunk_id = metadata["chunk_id"]

                # 第6步: resolver(chunk_id)从本地取正文
                text = self._resolve_text(chunk_id) # 正文字符串.

                # 第7步: 组装成Kaiji的SearchResponse
                hit_metadata = dict(record.metadata)
                hit_metadata["record_id"] = search_result.id # 这个字段的语义是“原始搜索命中的 record id”, 这里不是record.id
                hit = SearchHit(
                    chunk_id= chunk_id,
                    text=text,
                    score=search_result.score,
                    source=hit_metadata.get("source"),  # 远端 metadata 可以不包含source和page这两个字段, 使用["source"] ["page"] 可能会触发 KeyError. 应当使用get方法, 之后交给SearchHit校验实际类型.
                    page=hit_metadata.get("page"),
                    metadata=hit_metadata,
                )
                hits.append(hit)

                # 转换后：
                # SearchHit{
                #     "chunk_id": "chunk-a",
                #     "score": 0.91,
                #     "metadata": {
                #         "chunk_id": "chunk-a",
                #         "source": "paper.pdf",
                #         "page": 3,
                #         "author": "...",
                #         "record_id": 7
                #     }
                # }

        except httpx.TimeoutException as exc:
            raise AzusaAdapterError(f"Azusa search timed out:{str(exc)}", retryable=True) from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            retryable = status_code == 429 or status_code >= 500
            raise AzusaAdapterError(f"Azusa search returned HTTP {status_code}", retryable=retryable) from exc
        except httpx.RequestError as exc:
            raise AzusaAdapterError(f"Azusa search returned RequestError: {str(exc)}", retryable= True) from exc
        except ValueError as exc:
            raise AzusaAdapterError(f"Azusa search returned ValueError: {str(exc)}", retryable= True) from exc
        except TypeError as exc:
            raise AzusaAdapterError(f"Azusa search returned TypeError: {str(exc)}", retryable= True) from exc
        return SearchResponse(hits=hits, mode="hnsw")

    def get_chunk_by_id(self, chunk_id: str) -> ChunkResponse:
        """
        TODO 7c

        背景:
        chunk: 块; chunk_id: 稳定的证据块标识.
        旧 Rust 服务没有 GET /chunks/{id} 端点(不保存正文), 但 AzusaAdapter
        Protocol 要求 get_chunk_by_id 可用. 因此本方法不走 HTTP, 而是复用
        search_azusa 里同一套 resolver 契约, 从本地取正文构造 ChunkResponse.

        函数功能:
        get_chunk_by_id 接收 chunk_id, 寻找证据正文, 构建ChunkResponse, 并返回.
        它负责校验 chunk_id、经 _resolve_text 调用 resolver、统一包装错误.

        具体说明:
        必须复用 _resolve_text, 不能让直接读取绕过 resolver 校验和错误包装.
        chunk_id 为 None 或非空字符串是调用方参数错误: retryable=False
        (参数不变, 重试多少次都会失败).
        resolver 异常或非法返回是本地数据问题: retryable=False.
        """
        
        # 下面两个地方设置retryable=False, 是因为调用方传入了非法的chunk_id, 只要请求参数不变, 重试多少次都会失败.
        # 校验1: chunk_id 是否为空?
        if chunk_id is None:
            raise AzusaAdapterError("chunk_id does not exist",retryable=False)
        # 校验2: chunk_id 是否确实是非空字符串？
        if not (isinstance(chunk_id, str) and chunk_id.strip()):
            raise AzusaAdapterError("chunk_id should be a non-blank string", retryable=False)    

        text = self._resolve_text(chunk_id=chunk_id)
        return ChunkResponse(
            chunk_id=chunk_id,
            text=text,
        )

    def close(self) -> None:
        """关闭底层 HTTP 客户端连接池. """

        self._client.close()


class MockAzusaAdapter:
    """提供无需网络、GPU 或真实 Azusa 服务的确定性检索结果. """

    def __init__(self, chunks: list[ChunkResponse] | None = None) -> None:
        self._chunks = {
            chunk.chunk_id: chunk
            for chunk in (
                chunks if chunks is not None else [
                    ChunkResponse(
                        chunk_id="kaiji-001",
                        text="Kaiji owns tool, state, workflow, trace, and eval.",
                        source="mock://kaiji",
                    ),
                    ChunkResponse(
                        chunk_id="azusa-001",
                        text="Azusa provides HNSW and RAG retrieval through adapters.",
                        source="mock://azusa",
                    ),
                ]
            )
        }

    def search_azusa(self, query: str, k: int, mode: str) -> SearchResponse:
        """用简单词项匹配搜索内存中的教学证据. """

        terms = set(query.lower().split())
        ranked = sorted(
            self._chunks.values(),
            key=lambda chunk: -sum(term in chunk.text.lower() for term in terms),
        )
        hits = [
            SearchHit(**chunk.model_dump(), score=float(len(ranked) - index))
            for index, chunk in enumerate(ranked[:k])
            if not terms or any(term in chunk.text.lower() for term in terms)
        ]
        return SearchResponse(hits=hits, mode=mode)

    def get_chunk_by_id(self, chunk_id: str) -> ChunkResponse:
        """按 chunk_id 返回内存证据; 未知 ID 为不可重试失败. """

        try:
            return self._chunks[chunk_id]
        except KeyError as exc:
            raise AzusaAdapterError(
                f"unknown chunk_id: {chunk_id}", retryable=False
            ) from exc
