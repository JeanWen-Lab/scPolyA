from dataclasses import dataclass, field, replace
from typing import Literal, Any

from logging_config import get_logger, LOG_NC, CURRENT_SUFFIX, CURRENT_TASK, CURRENT_STAGE
from task_manage.sc_item_contracts import (
    ScItemArtifactFlags,
    ScItemEffectiveParams,
    ScItemIdentity,
    ScItemWorkerPayload,
    build_effective_params,
    format_cohort_name,
    format_polyA_status,
    format_sc_item_base_name,
    polyA_token,
    resolve_dedup_method,
)

# 哨兵对象
_NOT_SET = object()

logger_nc = get_logger(LOG_NC)


@dataclass
class ScItem:
    # --- 基础身份 (Identity) ---
    uid: str = None
    sid: str = None
    ctg: str = None
    dedup_status: str = None
    strand: Literal["plus", "minus"] | str | None = None

    # --- 模式开关 (Flags) ---
    # 这些 flag 决定了任务的性质，通常属于 Identity 的一部分 (比如 "这是一个 Peak 任务")
    is_cohort: bool = False
    is_coverage: bool = False
    is_negate: bool = False
    remove_CDS: bool = False

    is_peak: bool = False
    is_merged_peak: bool = False
    is_assigned_peak: bool = False
    is_annotated_bam: bool = False

    # --- 参数开关 (Param Flags) ---
    # 这些 flag 决定了具体参数取 args 里的哪一个
    is_polyA: bool = False

    # --- 显式参数 (Explicit Params) ---
    # 如果 scaling_factor 是 None，可能就代表 unscaled
    scaling_factor: float | None = None
    prefix: str = None

    # --- Execution contract fields (do not affect artifact names) ---
    coverage_source: str | None = None
    strand_filter: Literal["plus", "minus"] | str | None = None
    materialize_strand_bams: bool | None = None

    # --- 格式化控制 (Formatting) ---
    # 这些通常只影响文件名，不影响任务逻辑，但在你的设计里它们似乎也开启了某种逻辑
    add_dedup_method: bool = True
    add_polyA: bool = False
    add_threshold: bool = False
    add_merge_dist: bool = False

    # --- 外部配置 ---
    args: Any = None

    # 内部状态
    _dedup_method: str = field(init=False, default=None)

    # 附加：
    extra_info: dict = None
    extra_key: str = None

    def __post_init__(self):
        self._refresh_internal_state()

    def _refresh_internal_state(self):
        """刷新内部状态，确保 _dedup_method 同步"""
        if self.add_dedup_method and self.args:
            self._dedup_method = getattr(self.args, 'dedup_method', 'unknown')

    @property
    def task_identity(self) -> ScItemIdentity:
        return ScItemIdentity(
            uid=self.uid,
            sid=self.sid,
            ctg=self.ctg,
            dedup_status=self.dedup_status,
            strand=self.strand,
            is_cohort=self.is_cohort,
            is_polyA=self.is_polyA,
        )

    @property
    def artifact_flags(self) -> ScItemArtifactFlags:
        return ScItemArtifactFlags(
            prefix=self.prefix,
            is_coverage=self.is_coverage,
            is_negate=self.is_negate,
            remove_CDS=self.remove_CDS,
            is_peak=self.is_peak,
            is_merged_peak=self.is_merged_peak,
            is_assigned_peak=self.is_assigned_peak,
            is_annotated_bam=self.is_annotated_bam,
            add_polyA=self.add_polyA,
            add_threshold=self.add_threshold,
            add_merge_dist=self.add_merge_dist,
            scaling_factor=self.scaling_factor,
        )

    @property
    def effective_params(self) -> ScItemEffectiveParams:
        return build_effective_params(
            args=self.args,
            is_polyA=self.is_polyA,
            dedup_status=self.dedup_status,
            fallback_dedup_method=self._dedup_method,
            scaling_factor=self.scaling_factor,
        )

    @property
    def worker_payload(self) -> ScItemWorkerPayload:
        return ScItemWorkerPayload(
            extra_info=self.extra_info,
            extra_key=self.extra_key,
        )

    # =========================================================================
    # 核心属性：用于 TaskManager 提取 Key
    # =========================================================================

    @property
    def dedup_method(self) -> str:
        """获取实际使用的 dedup method"""
        return resolve_dedup_method(
            dedup_status=self.dedup_status,
            fallback_dedup_method=self._dedup_method,
        )

    @property
    def _polyA_str(self) -> str | None:
        """获取实际的 PolyA 参数字符串 (如果 is_polyA=False 则为 None/Empty)"""
        # 注意：如果这影响任务逻辑，即使 is_polyA=False，也应该返回一个明确的值(如 "raw")而不是 None，
        # 除非 is_polyA=False 意味着完全不同的处理路径。
        return polyA_token(is_polyA=self.is_polyA, args=self.args)

    @property
    def bigwig_conversion_schema(self) -> str:
        # Invalidate pre-validation bigWigs without invalidating peak/count tasks.
        return "validated-sorted-bedgraph-v1"

    @property
    def depth_threshold(self) -> float | str | None:
        """获取实际使用的阈值数值"""
        return self.effective_params.depth_threshold

    @property
    def peak_threshold_mode(self) -> str:
        return self.effective_params.peak_threshold_mode

    @property
    def dynamic_threshold_stat(self) -> str:
        return self.effective_params.dynamic_threshold_stat

    @property
    def dynamic_threshold_alpha(self) -> float:
        return self.effective_params.dynamic_threshold_alpha

    @property
    def dynamic_threshold_floor(self) -> float:
        return self.effective_params.dynamic_threshold_floor

    @property
    def dynamic_threshold_normalized(self) -> float:
        return self.effective_params.dynamic_threshold_normalized

    @property
    def merge_distance(self) -> int | None:
        """获取实际使用的 merge distance"""
        return self.effective_params.merge_distance

    # =========================================================================
    # 文件名生成 (复用上面的 effective_ 属性)
    # =========================================================================
    @property
    def name(self):
        return format_sc_item_base_name(
            identity=self.task_identity,
            flags=self.artifact_flags,
            params=self.effective_params,
        )

    @property
    def cohort_name(self):
        if not self.is_cohort:
            raise ValueError("ScItem is not a cohort.")
        return format_cohort_name(
            is_polyA=self.is_polyA,
            dedup_method=self._dedup_method,
            polyA_status=self._fmt_polyA(),
        )

    def _fmt_polyA(self):
        return format_polyA_status(self._polyA_str)

    @staticmethod
    def fmt_polyA(args):
        return format_polyA_status(polyA_token(is_polyA=True, args=args))

    @property
    def read_rule(self) -> str | None:
        """Unified read-rule token of expression coverage tasks (None in legacy)."""
        return self.effective_params.read_rule

    @property
    def polyA_status(self):
        return self._fmt_polyA() if self.is_polyA else None

    def _fmt_scaling(self):
        val = self.scaling_factor
        return f"{val}X" if val is not None else None

    def format_name(self,
                    suffix: str = None,
                    *,
                    uid: str | None = _NOT_SET,
                    sid: str | None = _NOT_SET,
                    ctg: str | None = _NOT_SET,
                    dedup_status: str | None = _NOT_SET,
                    strand: Literal["plus", "minus"] | None = _NOT_SET,
                    scaling_factor: float | None = _NOT_SET,
                    prefix: str | None = _NOT_SET,
                    is_polyA: bool = _NOT_SET,
                    is_cohort: bool = _NOT_SET,
                    is_coverage: bool = _NOT_SET,
                    is_negate: bool = _NOT_SET,
                    is_scale: bool = _NOT_SET,
                    remove_CDS: bool = _NOT_SET,

                    is_peak: bool = _NOT_SET,
                    is_merged_peak: bool = _NOT_SET,
                    is_assigned_peak: bool = _NOT_SET,
                    is_annotated_bam: bool = _NOT_SET,

                    add_dedup_method: bool = _NOT_SET,
                    add_polyA: bool = _NOT_SET,
                    add_threshold: bool = _NOT_SET,
                    add_merge_dist: bool = _NOT_SET,
                    args: Any = _NOT_SET) -> str:

        changes = {}
        params = locals()

        for k, v in params.items():
            if k in ['self', 'suffix', 'changes', 'params']:
                continue
            if v is not _NOT_SET:
                changes[k] = v

        if not changes:
            temp_item = self
        else:
            temp_item = replace(self, **changes)
            temp_item._refresh_internal_state()

        base = temp_item.name

        res = f"{base}.{suffix}" if suffix else base

        logger_nc.debug(
            f"ScItemName={res} | "
            f"stage={CURRENT_STAGE.get()!r} "
            f"task={CURRENT_TASK.get()!r} "
            f"suffix={CURRENT_SUFFIX.get()!r}"
        )

        return res

    # =========================================================================

    def replace(self, **changes) -> "ScItem":
        """
        创建一个当前实例的副本，并根据传入的参数修改对应的属性。
        修改后会自动触发 __post_init__ 以刷新内部状态（如 _dedup_method）。
        """
        # dataclasses.replace 会创建一个新对象，并重新调用 __post_init__
        new_instance = replace(self, **changes)
        return new_instance

    @classmethod
    def cls_format_name(cls,
                        suffix,
                        *,
                        uid: str = None,
                        sid: str = None,

                        ctg: str = None,
                        dedup_status: str = None,
                        strand: Literal["plus", "minus"] | str | None = None,
                        is_cohort: bool = False,
                        is_coverage: bool = False,
                        is_negate: bool = False,
                        is_peak: bool = False,
                        is_merged_peak: bool = False,

                        is_assigned_peak: bool = False,
                        is_annotated_bam: bool = False,
                        remove_CDS: bool = False,

                        # --- 参数开关 (Param Flags) ---
                        # 这些 flag 决定了具体参数取 args 里的哪一个
                        is_polyA: bool = False,

                        # --- 显式参数 (Explicit Params) ---
                        # 如果 scaling_factor 是 None，可能就代表 unscaled
                        scaling_factor: float | None = None,
                        prefix: str = None,

                        # --- 格式化控制 (Formatting) ---
                        # 这些通常只影响文件名，不影响任务逻辑，但在你的设计里它们似乎也开启了某种逻辑
                        add_dedup_method: bool = True,
                        add_polyA: bool = False,
                        add_threshold: bool = False,
                        add_merge_dist: bool = False,

                        # --- 外部配置 ---
                        args: Any = None) -> str:
        """
        静态工厂方法：用于直接生成文件名，替代外部的 NBAM 函数。
        无需手动实例化 ScItem 对象即可调用。
        """
        # 1. 冲突检查 (复用原 NBAM 逻辑)
        if uid is not None and (sid is not None or ctg is not None):
            raise ValueError(f"Conflict: 'unit_id' ({uid}) cannot be used with 'sid'/'contig'.")

        # 2. 实例化临时对象
        # 利用 cls() 动态创建，并在 polyA_override 传入字符串
        tmp = cls(uid=uid,
                  sid=sid,
                  ctg=ctg,
                  dedup_status=dedup_status,
                  strand=strand,
                  is_cohort=is_cohort,
                  is_coverage=is_coverage,
                  is_negate=is_negate,
                  is_peak=is_peak,
                  is_merged_peak=is_merged_peak,
                  is_assigned_peak=is_assigned_peak,
                  is_annotated_bam=is_annotated_bam,
                  remove_CDS=remove_CDS,

                  is_polyA=is_polyA,

                  scaling_factor=scaling_factor,
                  prefix=prefix,
                  add_dedup_method=add_dedup_method,
                  add_polyA=add_polyA,
                  add_threshold=add_threshold,
                  add_merge_dist=add_merge_dist,

                  args=args
                  )

        # 3. 返回格式化结果
        return tmp.format_name(suffix=suffix)
