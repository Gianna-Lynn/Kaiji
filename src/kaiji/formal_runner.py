"""提供逐 Episode 落盘、预算持久化和安全断点续跑的 Formal Runner。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import numbers
import os
from collections import Counter
from pathlib import Path
from collections.abc import Callable
from typing import Any, Literal
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

from kaiji.eval_runner import AgentEpisode, AgentEvalCase, AgentEvalRunner
from kaiji.eval_scorer import AgentAggregateReport, aggregate_scores, score_episode
from kaiji.models import ModelCallResult, ProviderErrorCode


LEDGER_ABNORMAL_CODES = {
    ProviderErrorCode.INVALID_JSON,
    ProviderErrorCode.INVALID_RESPONSE,
    ProviderErrorCode.HTTP_ERROR,
}


def _fingerprint(value: Any) -> str:
    """对 JSON 兼容对象计算稳定 SHA-256。"""

    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _atomic_json(path: Path, value: BaseModel) -> None:
    """先写同目录临时文件再替换，避免留下半截 Manifest 或账本。"""

    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(value.model_dump_json(indent=2))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


class FormalRunConfig(BaseModel):
    """冻结会影响实验可比性、预算和停止行为的 Formal 配置。"""

    model_config = ConfigDict(extra="forbid")

    suite: Literal["real_behavior", "reliability"]
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    base_url: str = Field(default="https://api.deepseek.com", min_length=1)
    repeats: StrictInt = Field(default=3, ge=1)
    temperature: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    thinking: Literal["disabled"] | None = "disabled"
    max_tokens: StrictInt = Field(default=512, ge=1)
    request_limit: StrictInt = Field(ge=1)
    cost_limit_usd: float = Field(gt=0, allow_inf_nan=False)
    input_cost_per_million: float = Field(default=0.14, ge=0, allow_inf_nan=False)
    output_cost_per_million: float = Field(default=0.28, ge=0, allow_inf_nan=False)
    system_prompt_sha256: str = Field(min_length=64, max_length=64)
    tool_schema_sha256: str = Field(min_length=64, max_length=64)

    @field_validator("repeats", "max_tokens", "request_limit", mode="before")
    @classmethod
    def reject_bool_in_integer_boundaries(cls, value: object) -> object:
        """预算与矩阵计数不得接受 Python bool 冒充整数。"""

        if type(value) is bool:
            raise ValueError("Formal 整数配置不得为 bool")
        return value

    @field_validator(
        "temperature",
        "cost_limit_usd",
        "input_cost_per_million",
        "output_cost_per_million",
        mode="before",
    )
    @classmethod
    def reject_bool_float_boundaries(cls, value: object) -> object:
        """浮点配置允许整数写法，但不得接受 bool、字符串或非有限数值。"""

        if type(value) is bool or not isinstance(value, numbers.Real):
            raise ValueError("Formal 数值配置必须是数值且不得为 bool")
        if not math.isfinite(float(value)):
            raise ValueError("Formal 数值配置必须是有限数值")
        return value

    @field_validator("base_url")
    @classmethod
    def require_http_base_url(cls, value: str) -> str:
        """Formal 不接受无法形成 OpenAI-compatible HTTP 端点的地址。"""

        if not value.startswith(("http://", "https://")):
            raise ValueError("Formal base_url 必须使用 http 或 https")
        return value.rstrip("/")


class FormalManifest(BaseModel):
    """保存不可在恢复时悄然变化的实验身份与矩阵边界。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: StrictInt = 1
    experiment_id: str = Field(min_length=1)
    config: FormalRunConfig
    config_fingerprint: str = Field(min_length=64, max_length=64)
    dataset_hash: str = Field(min_length=64, max_length=64)
    expected_episode_count: StrictInt = Field(ge=1)
    case_ids: list[str]


class RequestLedgerState(BaseModel):
    """保守记录已开始请求；未完成请求也永久占用硬预算。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: StrictInt = 1
    requests_started: StrictInt = Field(default=0, ge=0)
    responses_finished: StrictInt = Field(default=0, ge=0)
    input_tokens: StrictInt = Field(default=0, ge=0)
    output_tokens: StrictInt = Field(default=0, ge=0)
    consecutive_abnormal: StrictInt = Field(default=0, ge=0)


class PersistentRequestLedger:
    """在每次真实请求前后同步刷盘的调用与费用账本。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.state = (
            RequestLedgerState.model_validate_json(path.read_text(encoding="utf-8"))
            if path.exists()
            else RequestLedgerState()
        )
        self._validate()
        if not path.exists():
            _atomic_json(path, self.state)

    def _validate(self) -> None:
        """拒绝响应数量超过已开始请求的损坏账本。"""

        if self.state.responses_finished > self.state.requests_started:
            raise ValueError("请求账本损坏：完成响应数超过已开始请求数")

    def record_started(self) -> None:
        """在触达 Provider 前增加计数并立即刷盘。"""

        self.state.requests_started += 1
        _atomic_json(self.path, self.state)

    def record_finished(self, result: ModelCallResult) -> None:
        """响应返回后累计已报告 Token；未知用量不伪造。"""

        if self.state.responses_finished >= self.state.requests_started:
            raise ValueError("请求账本损坏：没有对应 started 的待完成请求")
        self.state.responses_finished += 1
        usage = result.metadata.usage
        self.state.input_tokens += usage.input_tokens or 0
        self.state.output_tokens += usage.output_tokens or 0
        if result.failure is not None and result.failure.code in LEDGER_ABNORMAL_CODES:
            self.state.consecutive_abnormal += 1
        else:
            self.state.consecutive_abnormal = 0
        self._validate()
        _atomic_json(self.path, self.state)

    def estimated_cost(self, config: FormalRunConfig) -> float:
        """按配置冻结的单价估算已报告 Token 成本。"""

        return (
            self.state.input_tokens * config.input_cost_per_million
            + self.state.output_tokens * config.output_cost_per_million
        ) / 1_000_000


class FormalEpisodeRecord(BaseModel):
    """用前向 hash 串联一次已完成 Episode 及其矩阵坐标。

    该链只提供篡改检测：没有密钥签名或可信外部锚点，能够修改文件的攻击者也能
    重新计算整条链，因此不能把 ``record_hash`` 当成真实性或作者身份证明。
    """

    model_config = ConfigDict(extra="forbid")

    record_index: StrictInt = Field(ge=0)
    case_id: str = Field(min_length=1)
    repeat_index: StrictInt = Field(ge=0)
    previous_hash: str
    episode: AgentEpisode
    record_hash: str = Field(min_length=64, max_length=64)


class FormalRunReport(BaseModel):
    """保存 Formal 状态；Provider 计数语义由带配置的 ``_report`` 绑定。

    模型层只能校验结构关系：``expected_request_count=0`` 合法地表示
    Scripted/Mock Preflight 未发送真实请求。因此不能单独把该模型当作 real provider
    账本证明；真实 Provider 必须由 ``ResumableFormalRunner._report`` 根据
    ``FormalRunConfig.provider`` 将 expected count 绑定到模型回合与 prior retry。
    """

    experiment_id: str
    model_config = ConfigDict(extra="forbid")

    complete: StrictBool
    stop_reason: str | None = None
    expected_episode_count: int
    actual_episode_count: int
    requests_started: int
    responses_finished: int
    model_request_count: int
    expected_request_count: int
    estimated_cost_usd: float
    dataset_hash: str
    config_fingerprint: str
    latest_record_hash: str
    retried_request_count: int = 0
    retry_failure_distribution: dict[str, int] = Field(default_factory=dict)
    aggregate: AgentAggregateReport | None = None
    bad_case_run_ids: list[str] = Field(default_factory=list)
    scenario_distribution: dict[str, int]

    @model_validator(mode="after")
    def complete_report_has_ledger_invariant(self) -> "FormalRunReport":
        """完整报告必须无未决请求，并满足模型回合/重试与账本的计数关系。"""

        if self.complete:
            if self.actual_episode_count != self.expected_episode_count:
                raise ValueError("Formal 完整报告的 Episode 数量不一致")
            if self.aggregate is None:
                raise ValueError("Formal 完整报告必须包含 aggregate")
            if self.requests_started != self.responses_finished:
                raise ValueError("Formal 完整报告存在未决请求")
            if self.requests_started != self.expected_request_count:
                raise ValueError(
                    "Formal 完整报告的账本请求数必须等于模型请求数（含模型重试）"
                )
            if self.expected_request_count not in {
                0,
                self.model_request_count,
            }:
                raise ValueError("Formal 报告的请求计数口径与 Episode 不一致")
        elif self.aggregate is not None:
            raise ValueError("Formal incomplete 报告不得包含 aggregate")
        return self


class ResumableFormalRunner:
    """执行固定矩阵，并在每条完成后持久化可验证记录与部分状态。"""

    def __init__(
        self,
        config: FormalRunConfig,
        cases: list[AgentEvalCase],
        runner: AgentEvalRunner | None,
        run_dir: Path,
        *,
        resume: bool = False,
        stop_check: Callable[[], str | None] | None = None,
    ) -> None:
        if not cases:
            raise ValueError("Formal 至少需要一条 case")
        ids = [case.case_id for case in cases]
        if len(ids) != len(set(ids)):
            raise ValueError("Formal case_id 必须唯一")
        suites = {case.suite for case in cases}
        allowed_suites = (
            {"behavior", "paraphrase"}
            if config.suite == "real_behavior"
            else {"reliability"}
        )
        if not suites or not suites <= allowed_suites:
            raise ValueError("Formal case suite 与配置不一致")
        self.config = config
        self.cases = cases
        self.runner = runner
        self.run_dir = run_dir
        self.episodes_path = run_dir / "episodes.jsonl"
        self.manifest_path = run_dir / "manifest.json"
        self.report_path = run_dir / "report.json"
        self.stop_check = stop_check
        dataset_hash = _fingerprint([case.model_dump(mode="json") for case in cases])
        config_hash = _fingerprint(config.model_dump(mode="json"))
        if resume:
            if not self.manifest_path.exists():
                raise FileNotFoundError("恢复目录缺少 manifest.json")
            self.manifest = FormalManifest.model_validate_json(
                self.manifest_path.read_text(encoding="utf-8")
            )
            if self.manifest.config != config:
                raise ValueError("恢复 Manifest 中的配置正文不一致")
            if self.manifest.config_fingerprint != _fingerprint(
                self.manifest.config.model_dump(mode="json")
            ):
                raise ValueError("恢复 Manifest 自身的配置 fingerprint 损坏")
            if self.manifest.dataset_hash != dataset_hash:
                raise ValueError("恢复数据集 hash 不一致")
            if self.manifest.config_fingerprint != config_hash:
                raise ValueError("恢复配置 hash 不一致")
            if self.manifest.case_ids != ids:
                raise ValueError("恢复 case 顺序不一致")
            if self.manifest.expected_episode_count != len(cases) * config.repeats:
                raise ValueError("恢复期望 Episode 数与矩阵不一致")
        else:
            run_dir.mkdir(parents=True, exist_ok=False)
            self.manifest = FormalManifest(
                experiment_id=uuid4().hex,
                config=config,
                config_fingerprint=config_hash,
                dataset_hash=dataset_hash,
                expected_episode_count=len(cases) * config.repeats,
                case_ids=ids,
            )
            _atomic_json(self.manifest_path, self.manifest)
        if resume and not (run_dir / "request-ledger.json").exists():
            raise FileNotFoundError("恢复目录缺少 request-ledger.json")
        self.ledger = PersistentRequestLedger(run_dir / "request-ledger.json")

    def attach_execution(
        self,
        runner: AgentEvalRunner,
        *,
        stop_check: Callable[[], str | None] | None = None,
    ) -> None:
        """在 Manifest 与账本打开后装配依赖账本恢复状态的真实 Runner。"""

        self.runner = runner
        self.stop_check = stop_check

    def _load_records(self) -> list[FormalEpisodeRecord]:
        """校验 JSONL、hash 链、矩阵坐标与 run_id 唯一性。"""

        if not self.episodes_path.exists():
            return []
        records: list[FormalEpisodeRecord] = []
        previous_hash = ""
        coordinates: set[tuple[str, int]] = set()
        run_ids: set[str] = set()
        allowed = {
            (case.case_id, repeat)
            for repeat in range(self.config.repeats)
            for case in self.cases
        }
        cases_by_id = {case.case_id: case for case in self.cases}
        for index, line in enumerate(
            self.episodes_path.read_text(encoding="utf-8").splitlines()
        ):
            if not line.strip():
                raise ValueError("Episode JSONL 不允许空行")
            record = FormalEpisodeRecord.model_validate_json(line)
            payload = record.model_dump(mode="json", exclude={"record_hash"})
            expected_hash = _fingerprint(payload)
            coordinate = (record.case_id, record.repeat_index)
            if record.record_index != index or record.previous_hash != previous_hash:
                raise ValueError("Episode hash 链或顺序损坏")
            if record.record_hash != expected_hash:
                raise ValueError("Episode 记录 hash 不一致")
            if coordinate not in allowed or coordinate in coordinates:
                raise ValueError("Episode 矩阵坐标非法或重复")
            if record.episode.case_id != record.case_id:
                raise ValueError("Episode 内外 case_id 不一致")
            if record.episode.repeat_index != record.repeat_index:
                raise ValueError("Episode 内外 repeat_index 不一致")
            if record.episode.suite != cases_by_id[record.case_id].suite:
                raise ValueError("Episode suite 与 case 契约不一致")
            if record.episode.run_id in run_ids:
                raise ValueError("Episode run_id 重复")
            records.append(record)
            coordinates.add(coordinate)
            run_ids.add(record.episode.run_id)
            previous_hash = record.record_hash
        return records

    def _append(
        self, episode: AgentEpisode, records: list[FormalEpisodeRecord]
    ) -> None:
        """构造下一条 hash 记录并追加、flush、fsync。"""

        coordinate = (episode.case_id, episode.repeat_index)
        existing_coordinates = {
            (record.case_id, record.repeat_index) for record in records
        }
        if coordinate in existing_coordinates:
            raise ValueError("Episode 矩阵坐标重复")
        if episode.run_id in {record.episode.run_id for record in records}:
            raise ValueError("Episode run_id 重复")

        payload = {
            "record_index": len(records),
            "case_id": episode.case_id,
            "repeat_index": episode.repeat_index,
            "previous_hash": records[-1].record_hash if records else "",
            "episode": episode.model_dump(mode="json"),
        }
        record = FormalEpisodeRecord(
            **payload,
            record_hash=_fingerprint(payload),
        )
        with self.episodes_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(record.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        records.append(record)

    @staticmethod
    def _expected_request_count(records: list[FormalEpisodeRecord]) -> int:
        """从 Episode 计算 Provider 实际请求数：每个模型回合加其已记录重试。"""

        return sum(
            1 + len(model_call.prior_attempt_failures)
            for record in records
            for model_call in record.episode.result.model_calls
        )

    def _report(
        self,
        records: list[FormalEpisodeRecord],
        *,
        stop_reason: str | None,
        persist: bool = True,
    ) -> FormalRunReport:
        """完整矩阵才聚合；可在纯离线审计时只返回而不改写产物。"""

        episodes = [record.episode for record in records]
        complete = len(records) == self.manifest.expected_episode_count
        aggregate = aggregate_scores(self.cases, episodes) if complete else None
        model_request_count = self._expected_request_count(records)
        offline_provider = self.config.provider in {
            "mock",
            "scripted",
            "scripted_preflight",
        }
        # Scripted/Mock Preflight 不发送模型请求，因此其账本应为零；真实 Provider
        # 则要求 ledger=request 回合数+prior_attempt_failures。这个 provider 选择
        # 语义必须在这里由 config 绑定，不能只依赖 FormalRunReport 模型字段。
        expected_request_count = 0 if offline_provider else model_request_count
        if complete and self.ledger.state.requests_started != expected_request_count:
            raise ValueError("Formal 账本请求数与模型回合/重试数不一致")
        bad_ids: list[str] = []
        scores = {}
        if complete:
            by_id = {case.case_id: case for case in self.cases}
            scores = {
                episode.run_id: score_episode(by_id[episode.case_id], episode)
                for episode in episodes
            }
            bad_ids = [
                episode.run_id
                for episode in episodes
                if not scores[episode.run_id].task_success
            ]
        retry_distribution: dict[str, int] = {}
        for episode in episodes:
            for call in episode.result.model_calls:
                for attempt in call.prior_attempt_failures:
                    code = attempt.failure.code.value
                    retry_distribution[code] = retry_distribution.get(code, 0) + 1
        report = FormalRunReport(
            experiment_id=self.manifest.experiment_id,
            complete=complete,
            stop_reason=None if complete else stop_reason,
            expected_episode_count=self.manifest.expected_episode_count,
            actual_episode_count=len(records),
            requests_started=self.ledger.state.requests_started,
            responses_finished=self.ledger.state.responses_finished,
            model_request_count=model_request_count,
            expected_request_count=expected_request_count,
            estimated_cost_usd=self.ledger.estimated_cost(self.config),
            dataset_hash=self.manifest.dataset_hash,
            config_fingerprint=self.manifest.config_fingerprint,
            latest_record_hash=records[-1].record_hash if records else "",
            retried_request_count=sum(retry_distribution.values()),
            retry_failure_distribution=retry_distribution,
            aggregate=aggregate,
            bad_case_run_ids=bad_ids,
            scenario_distribution=dict(Counter(case.scenario for case in self.cases)),
        )
        if persist:
            _atomic_json(self.report_path, report)
        if complete and persist:
            bad_cases_path = self.run_dir / "bad_cases.json"
            bad_cases = [
                {
                    "episode": episode.model_dump(mode="json"),
                    "score": scores[episode.run_id].model_dump(mode="json"),
                }
                for episode in episodes
                if episode.run_id in bad_ids
            ]
            temporary = bad_cases_path.with_suffix(".json.tmp")
            try:
                with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                    json.dump(bad_cases, handle, ensure_ascii=False, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, bad_cases_path)
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        return report

    def run(self, *, max_new_episodes: int | None = None) -> FormalRunReport:
        """跳过已完成坐标；测试可用 max_new_episodes 模拟受控中断。"""

        if max_new_episodes is not None and (
            type(max_new_episodes) is not int or max_new_episodes < 0
        ):
            raise ValueError("max_new_episodes 必须是真正的非负整数")
        records = self._load_records()
        if self.runner is None:
            raise RuntimeError("Formal Runner 尚未装配执行环境")
        completed = {(item.case_id, item.repeat_index) for item in records}
        produced = 0

        def budget_stop_reason() -> str | None:
            """在启动新 Episode 前阻断未决请求和已耗尽的共享预算。"""

            if self.ledger.state.requests_started != self.ledger.state.responses_finished:
                return "unresolved_request"
            if self.ledger.state.requests_started >= self.config.request_limit:
                return "request_limit"
            if self.ledger.estimated_cost(self.config) >= self.config.cost_limit_usd:
                return "cost_limit"
            return None

        try:
            initial_stop = budget_stop_reason()
            if initial_stop is None and self.stop_check is not None:
                initial_stop = self.stop_check()
            if initial_stop is not None:
                return self._report(records, stop_reason=initial_stop)
            for repeat in range(self.config.repeats):
                for case in self.cases:
                    coordinate = (case.case_id, repeat)
                    if coordinate in completed:
                        continue
                    stop_reason = budget_stop_reason()
                    if stop_reason is None and self.stop_check is not None:
                        stop_reason = self.stop_check()
                    if stop_reason is not None:
                        return self._report(records, stop_reason=stop_reason)
                    if max_new_episodes is not None and produced >= max_new_episodes:
                        return self._report(records, stop_reason="preflight_pause")
                    episode = self.runner.run(case, repeat_index=repeat)
                    if episode.case_id != case.case_id or episode.repeat_index != repeat:
                        raise ValueError("Runner 返回的 Episode 坐标与矩阵不一致")
                    self._append(episode, records)
                    completed.add(coordinate)
                    produced += 1
                    self._report(records, stop_reason="in_progress")
                    stop_reason = budget_stop_reason()
                    if stop_reason is None and self.stop_check is not None:
                        stop_reason = self.stop_check()
                    if stop_reason is not None:
                        if len(records) == self.manifest.expected_episode_count:
                            return self._report(records, stop_reason=None)
                        return self._report(records, stop_reason=stop_reason)
        except BaseException:
            # 报告是 best-effort；不能让报告写入失败遮蔽原始 Provider/磁盘异常。
            try:
                self._report(records, stop_reason="interrupted")
            except BaseException:
                pass
            raise
        return self._report(records, stop_reason=None)


def _preserve_before_rescore(path: Path) -> None:
    """首次迁移前保留原始产物，后续重算不得覆盖这份审计基线。"""

    if not path.exists():
        return
    backup = path.with_name(f"{path.stem}.before-rescore{path.suffix}")
    if backup.exists():
        return
    with backup.open("xb") as handle:
        handle.write(path.read_bytes())
        handle.flush()
        os.fsync(handle.fileno())


def rescore_formal_run(
    run_dir: Path,
    cases: list[AgentEvalCase],
    *,
    persist: bool = False,
) -> FormalRunReport:
    """只读取冻结 Episode、Manifest 与账本，重新校验并生成当前口径报告。

    该入口不装配 Provider，也不会发送网络请求。默认只返回重算结果；显式持久化时，
    首次迁移会保留旧 ``report.json`` 与 ``bad_cases.json`` 的原始副本。
    """

    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError("重评分目录缺少 manifest.json")
    manifest = FormalManifest.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )
    session = ResumableFormalRunner(
        manifest.config,
        cases,
        None,
        run_dir,
        resume=True,
    )
    records = session._load_records()
    if len(records) != manifest.expected_episode_count:
        raise ValueError("只有完整 Formal 矩阵才能迁移正式报告")
    report = session._report(records, stop_reason=None, persist=False)
    if persist:
        _preserve_before_rescore(session.report_path)
        _preserve_before_rescore(run_dir / "bad_cases.json")
        report = session._report(records, stop_reason=None, persist=True)
    return report


def build_deepseek_behavior_runner(
    config: FormalRunConfig,
    ledger: PersistentRequestLedger,
    *,
    api_key_env: str,
) -> tuple[AgentEvalRunner, Callable[[], str | None]]:
    """按持久账本恢复共享预算，并构造五工具 DeepSeek Formal Runner。"""

    if config.suite != "real_behavior":
        raise ValueError("真实模型入口只接受 real_behavior suite")
    from kaiji.providers.openai_compatible import (
        OpenAICompatibleConfig,
        OpenAICompatibleProvider,
    )
    from kaiji.real_pilot import (
        BEHAVIOR_SYSTEM_PROMPT,
        GuardedProvider,
        PilotGuard,
        build_behavior_registry,
    )
    from kaiji.tools import ToolExecutor

    if config.system_prompt_sha256 != _fingerprint(BEHAVIOR_SYSTEM_PROMPT):
        raise ValueError("真实 Formal 的 System Prompt hash 与实现不一致")
    if config.tool_schema_sha256 != _fingerprint(build_behavior_registry().schemas()):
        raise ValueError("真实 Formal 的工具 Schema hash 与实现不一致")

    guard = PilotGuard(
        request_limit=config.request_limit,
        cost_limit_usd=config.cost_limit_usd,
        input_cost_per_million=config.input_cost_per_million,
        output_cost_per_million=config.output_cost_per_million,
        requests_used=ledger.state.requests_started,
        input_tokens=ledger.state.input_tokens,
        output_tokens=ledger.state.output_tokens,
        consecutive_abnormal=ledger.state.consecutive_abnormal,
    )

    def provider_factory(case: AgentEvalCase):
        del case
        registry = build_behavior_registry()
        delegate = OpenAICompatibleProvider(
            OpenAICompatibleConfig(
                base_url=config.base_url,
                model=config.model,
                api_key_env=api_key_env,
                timeout_seconds=30,
                temperature=config.temperature,
                system_prompt=BEHAVIOR_SYSTEM_PROMPT,
                thinking=config.thinking,
                max_tokens=config.max_tokens,
            ),
            registry,
        )
        return GuardedProvider(
            delegate,
            guard,
            attempt_recorder=ledger,
        )

    def executor_factory(case: AgentEvalCase):
        del case
        return ToolExecutor(build_behavior_registry())

    def stop_check() -> str | None:
        """不发送探测请求，只依据恢复后的共享 Guard 判断停止状态。"""

        if ledger.state.requests_started != ledger.state.responses_finished:
            return "unresolved_request"
        if guard.stop_reason is not None:
            return guard.stop_reason
        if guard.requests_used >= guard.request_limit:
            return "request_limit"
        if guard.estimated_cost_usd >= guard.cost_limit_usd:
            return "cost_limit"
        if guard.consecutive_abnormal >= guard.consecutive_abnormal_limit:
            return "consecutive_abnormal"
        return None

    return AgentEvalRunner(provider_factory, executor_factory), stop_check


def open_deepseek_behavior_session(
    config: FormalRunConfig,
    cases: list[AgentEvalCase],
    run_dir: Path,
    *,
    api_key_env: str,
    resume: bool = False,
) -> ResumableFormalRunner:
    """先打开 Manifest/账本，再用恢复后的预算装配真实 DeepSeek 执行环境。"""

    session = ResumableFormalRunner(
        config,
        cases,
        None,
        run_dir,
        resume=resume,
    )
    runner, stop_check = build_deepseek_behavior_runner(
        config,
        session.ledger,
        api_key_env=api_key_env,
    )
    session.attach_execution(runner, stop_check=stop_check)
    return session


def make_deepseek_formal_config(
    *,
    request_limit: int,
    cost_limit_usd: float,
) -> FormalRunConfig:
    """按当前受审计 Prompt、五工具与官方价格构造真实 Formal 配置。"""

    from kaiji.real_pilot import BEHAVIOR_SYSTEM_PROMPT, build_behavior_registry
    from kaiji.real_smoke import BASE_URL, MODEL

    return FormalRunConfig(
        suite="real_behavior",
        provider="deepseek_openai_compatible",
        model=MODEL,
        base_url=BASE_URL,
        repeats=3,
        temperature=0,
        thinking="disabled",
        max_tokens=512,
        request_limit=request_limit,
        cost_limit_usd=cost_limit_usd,
        input_cost_per_million=0.14,
        output_cost_per_million=0.28,
        system_prompt_sha256=_fingerprint(BEHAVIOR_SYSTEM_PROMPT),
        tool_schema_sha256=_fingerprint(build_behavior_registry().schemas()),
    )


class FormalPreflightReport(BaseModel):
    """分别保存真实行为与确定性可靠性两套离线 Formal 预检结果。"""

    preflight_id: str
    real_behavior: FormalRunReport
    reliability: FormalRunReport


def run_formal_preflight(root: Path, output_dir: Path) -> FormalPreflightReport:
    """离线执行 40×3 行为矩阵和 20×3 故障矩阵，绝不构造真实 Provider。"""

    from kaiji.experiment import build_scripted_runner, load_agent_cases
    from kaiji.harnesses import HarnessVariant
    from kaiji.real_pilot import BEHAVIOR_SYSTEM_PROMPT
    from kaiji.real_pilot import build_behavior_registry

    preflight_id = uuid4().hex
    target = output_dir / preflight_id
    behavior_cases = load_agent_cases(root / "eval" / "real_behavior_cases.jsonl")
    behavior_cases += load_agent_cases(root / "eval" / "real_paraphrase_cases.jsonl")
    reliability_cases = load_agent_cases(root / "eval" / "fault_cases.jsonl")
    behavior_config = FormalRunConfig(
        suite="real_behavior",
        provider="scripted_preflight",
        model="scripted",
        repeats=3,
        request_limit=1,
        cost_limit_usd=1,
        input_cost_per_million=0,
        output_cost_per_million=0,
        system_prompt_sha256=_fingerprint(BEHAVIOR_SYSTEM_PROMPT),
        tool_schema_sha256=_fingerprint(build_behavior_registry().schemas()),
    )
    reliability_config = behavior_config.model_copy(
        update={
            "suite": "reliability",
            "system_prompt_sha256": _fingerprint("scripted-reliability-preflight"),
        }
    )
    behavior = ResumableFormalRunner(
        behavior_config,
        behavior_cases,
        build_scripted_runner(HarnessVariant.FULL_KAIJI),
        target / "real-behavior",
    ).run()
    reliability = ResumableFormalRunner(
        reliability_config,
        reliability_cases,
        build_scripted_runner(HarnessVariant.FULL_KAIJI),
        target / "reliability",
    ).run()
    report = FormalPreflightReport(
        preflight_id=preflight_id,
        real_behavior=behavior,
        reliability=reliability,
    )
    _atomic_json(target / "preflight-report.json", report)
    return report


def main() -> None:
    """显式区分离线 Preflight 与需凭据、预算和目录的真实 Formal。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--real-behavior", action="store_true")
    parser.add_argument("--rescore", action="store_true")
    parser.add_argument("--write-rescored-report", action="store_true")
    parser.add_argument("--api-key-env")
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--request-limit", type=int)
    parser.add_argument("--cost-limit-usd", type=float)
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/formal-preflight")
    )
    args = parser.parse_args()
    if sum((args.preflight, args.real_behavior, args.rescore)) != 1:
        parser.error("必须且只能选择 --preflight、--real-behavior 或 --rescore")
    if args.write_rescored_report and not args.rescore:
        parser.error("--write-rescored-report 只能与 --rescore 同时使用")
    root = Path(__file__).resolve().parents[2]
    if args.preflight:
        report = run_formal_preflight(root, args.output_dir)
        print(
            {
                "preflight_id": report.preflight_id,
                "real_behavior": (
                    report.real_behavior.actual_episode_count,
                    report.real_behavior.complete,
                ),
                "reliability": (
                    report.reliability.actual_episode_count,
                    report.reliability.complete,
                ),
            }
        )
        return
    if args.rescore:
        if args.run_dir is None:
            parser.error("离线重评分必须显式提供 --run-dir")
        manifest = FormalManifest.model_validate_json(
            (args.run_dir / "manifest.json").read_text(encoding="utf-8")
        )
        if manifest.config.suite == "real_behavior":
            cases = load_formal_behavior_cases(root)
        else:
            from kaiji.experiment import load_agent_cases

            cases = load_agent_cases(root / "eval" / "fault_cases.jsonl")
        report = rescore_formal_run(
            args.run_dir,
            cases,
            persist=args.write_rescored_report,
        )
        print(report.model_dump_json(indent=2))
        return
    if (
        args.api_key_env is None
        or args.run_dir is None
        or args.request_limit is None
        or args.cost_limit_usd is None
    ):
        parser.error(
            "真实 Formal 必须显式提供 --api-key-env、--run-dir、"
            "--request-limit 与 --cost-limit-usd"
        )
    if not os.environ.get(args.api_key_env):
        parser.error("指定的 API Key 环境变量未设置")
    cases = load_formal_behavior_cases(root)
    config = make_deepseek_formal_config(
        request_limit=args.request_limit,
        cost_limit_usd=args.cost_limit_usd,
    )
    session = open_deepseek_behavior_session(
        config,
        cases,
        args.run_dir,
        api_key_env=args.api_key_env,
        resume=args.resume,
    )
    report = session.run()
    print(
        {
            "experiment_id": report.experiment_id,
            "complete": report.complete,
            "episodes": (
                report.actual_episode_count,
                report.expected_episode_count,
            ),
            "requests_started": report.requests_started,
            "responses_finished": report.responses_finished,
            "estimated_cost_usd": report.estimated_cost_usd,
            "stop_reason": report.stop_reason,
            "task_success_rate": (
                report.aggregate.task_success_rate
                if report.aggregate is not None
                else None
            ),
        }
    )


def load_formal_behavior_cases(root: Path) -> list[AgentEvalCase]:
    """按冻结顺序加载 20 条核心行为与 20 条同义变体。"""

    from kaiji.experiment import load_agent_cases

    cases = load_agent_cases(root / "eval" / "real_behavior_cases.jsonl")
    cases += load_agent_cases(root / "eval" / "real_paraphrase_cases.jsonl")
    if len(cases) != 40:
        raise ValueError("真实 Formal 必须恰好包含 40 条行为 case")
    return cases


if __name__ == "__main__":
    main()
