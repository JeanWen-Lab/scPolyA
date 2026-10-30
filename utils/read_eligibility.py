"""One read-eligibility rule shared by PAS discovery and PAS counting.

``legacy`` keeps the historical, inconsistent behaviour byte for byte: the
poly(A) extract drops unmapped reads and reads below ``--minMAPQ``; expression
coverage uses every read; featureCounts counts only NH<=1 reads that touch one
window. Nothing in this module is consulted in legacy mode.

``unified`` applies the same rule at all three places (poly(A) extract,
expression coverage input and counting). A read is eligible when it is

- mapped,
- primary (neither secondary 0x100 nor supplementary 0x800),
- MAPQ >= ``--minMAPQ``, and
- allowed by ``--multimapper-policy``:
  ``unique`` additionally rejects reads with NH>1 or ``mm:i:1`` (Cell Ranger
  sets ``mm:i:1`` and MAPQ 255 on STAR multimappers it rescued to one gene);
  ``rescued`` keeps such reads when they pass the MAPQ rule.

NH is never a filter on its own under ``rescued``: rescued Cell Ranger reads
carry NH>1 with MAPQ 255, and primary + MAPQ decides. Each read contributes
one primary record, so counting it does not double count.

How an eligible read is placed on a PAS window is a separate setting,
``--count-assignment``. ``three_prime_end`` (the unified default) assigns each
read by its 3'-most aligned base in read (= transcript, with ``-s 1``)
orientation (featureCounts ``--read2pos 3``): a read whose aligned blocks touch
two adjacent PAS windows goes to the window holding its 3' end instead of
being dropped as ambiguous, and a read whose 3' end lies in no window stays
unassigned with its own reason. ``overlap`` keeps featureCounts' overlap
assignment (a read touching two windows is ambiguous). ``legacy`` always
means ``overlap``. ``-O`` is never used, so no read is counted at two PAS.

Artifact identity: :attr:`ReadRule.token` names every eligibility parameter
(policy, minMAPQ and the primary-only flag mask) and is used for coverage;
:attr:`ReadRule.counting_token` adds the assignment and is used in the
poly(A) status, which every peak/annotation/matrix name hashes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pysam

READ_RULE_LEGACY = "legacy"
READ_RULE_UNIFIED = "unified"
READ_RULES = (READ_RULE_LEGACY, READ_RULE_UNIFIED)

MULTIMAPPER_UNIQUE = "unique"
MULTIMAPPER_RESCUED = "rescued"
MULTIMAPPER_POLICIES = (MULTIMAPPER_UNIQUE, MULTIMAPPER_RESCUED)

COUNT_ASSIGNMENT_OVERLAP = "overlap"
COUNT_ASSIGNMENT_THREE_PRIME_END = "three_prime_end"
COUNT_ASSIGNMENTS = (COUNT_ASSIGNMENT_OVERLAP, COUNT_ASSIGNMENT_THREE_PRIME_END)
_ASSIGNMENT_TOKENS = {
    COUNT_ASSIGNMENT_OVERLAP: "ovl",
    COUNT_ASSIGNMENT_THREE_PRIME_END: "3p",
}

# Rejection reasons, in the order they are tested.
REASON_UNMAPPED = "unmapped"
REASON_SECONDARY = "secondary"
REASON_SUPPLEMENTARY = "supplementary"
REASON_MAPQ = "mapq"
REASON_MULTIMAPPER = "multimapper_policy"

# BAM flag bits rejected by the rule: unmapped | secondary | supplementary.
INELIGIBLE_FLAG_MASK = 0x4 | 0x100 | 0x800
# samtools filter expression with the same meaning as is_multimapper().
# A missing NH/mm tag never marks a read as a multimapper.
SAMTOOLS_UNIQUE_EXPRESSION = "!([NH]>1) && !([mm]==1)"


def is_multimapper(read: pysam.AlignedSegment) -> bool:
    """NH>1 or Cell Ranger's rescued-multimapper tag ``mm:i:1``."""

    if read.has_tag("NH") and int(read.get_tag("NH")) > 1:
        return True
    return read.has_tag("mm") and int(read.get_tag("mm")) == 1


@dataclass(frozen=True)
class ReadRule:
    mode: str
    min_mapq: int
    multimapper_policy: str | None = None
    # None resolves to the rule's default: overlap (legacy) or
    # three_prime_end (unified).
    assignment: str | None = None

    def __post_init__(self) -> None:
        if self.mode not in READ_RULES:
            raise ValueError(f"Unknown count read rule: {self.mode!r}")
        if self.mode == READ_RULE_UNIFIED:
            if self.multimapper_policy not in MULTIMAPPER_POLICIES:
                raise ValueError(
                    "--count-read-rule unified requires --multimapper-policy "
                    f"{' or '.join(MULTIMAPPER_POLICIES)}"
                )
        elif self.multimapper_policy is not None:
            raise ValueError(
                "--multimapper-policy applies only to --count-read-rule unified"
            )
        if int(self.min_mapq) < 0:
            raise ValueError(f"minMAPQ must be >= 0, got {self.min_mapq}")
        assignment = self.assignment
        if assignment is None:
            assignment = (
                COUNT_ASSIGNMENT_THREE_PRIME_END
                if self.mode == READ_RULE_UNIFIED
                else COUNT_ASSIGNMENT_OVERLAP
            )
        if assignment not in COUNT_ASSIGNMENTS:
            raise ValueError(f"Unknown count assignment: {assignment!r}")
        if self.mode == READ_RULE_LEGACY and assignment != COUNT_ASSIGNMENT_OVERLAP:
            raise ValueError(
                "--count-read-rule legacy keeps overlap assignment; "
                "--count-assignment three_prime_end needs --count-read-rule unified"
            )
        object.__setattr__(self, "assignment", assignment)

    @property
    def unified(self) -> bool:
        return self.mode == READ_RULE_UNIFIED

    @property
    def three_prime_end(self) -> bool:
        return self.assignment == COUNT_ASSIGNMENT_THREE_PRIME_END

    @property
    def token(self) -> str | None:
        """Eligibility identity token; ``None`` keeps legacy names unchanged.

        Names every parameter of :meth:`rejection_reason`: the multimapper
        policy, minMAPQ and the primary-only flag mask (unmapped, secondary
        and supplementary records rejected), e.g. ``unique-q30-primary``.
        """

        if not self.unified:
            return None
        return f"{self.multimapper_policy}-q{int(self.min_mapq)}-primary"

    @property
    def counting_token(self) -> str | None:
        """:attr:`token` plus the count assignment, e.g. ``unique-q30-primary-3p``."""

        if not self.unified:
            return None
        return f"{self.token}-{_ASSIGNMENT_TOKENS[self.assignment]}"

    def rejection_reason(self, read: pysam.AlignedSegment) -> str | None:
        if not self.unified:
            raise ValueError("The legacy rule has no shared eligibility predicate")
        if read.is_unmapped:
            return REASON_UNMAPPED
        if read.is_secondary:
            return REASON_SECONDARY
        if read.is_supplementary:
            return REASON_SUPPLEMENTARY
        if read.mapping_quality < self.min_mapq:
            return REASON_MAPQ
        if self.multimapper_policy == MULTIMAPPER_UNIQUE and is_multimapper(read):
            return REASON_MULTIMAPPER
        return None

    def is_eligible(self, read: pysam.AlignedSegment) -> bool:
        return self.rejection_reason(read) is None

    def samtools_view_filter_args(self) -> list[str]:
        """``samtools view`` options that keep exactly the eligible reads."""

        if not self.unified:
            raise ValueError("The legacy rule has no samtools filter")
        args = ["-F", hex(INELIGIBLE_FLAG_MASK), "-q", str(int(self.min_mapq))]
        if self.multimapper_policy == MULTIMAPPER_UNIQUE:
            args += ["-e", SAMTOOLS_UNIQUE_EXPRESSION]
        return args

    def featurecounts_args(self) -> list[str]:
        """featureCounts options for the unified rule.

        ``-M`` lets eligible NH>1 reads through, ``--primary`` keeps one record
        per read, ``-Q`` applies the MAPQ cut and, with the ``three_prime_end``
        assignment, ``--read2pos 3`` assigns the read by its 3'-most aligned
        base. The multimapper policy and
        the supplementary exclusion are enforced afterwards by
        :meth:`rejection_reason`, so featureCounts never decides alone.
        """

        if not self.unified:
            raise ValueError("The legacy rule keeps the historical featureCounts options")
        args = ["-M", "--primary", "-Q", str(int(self.min_mapq))]
        if self.three_prime_end:
            args += ["--read2pos", "3"]
        return args


def read_rule_from_args(args: Any) -> ReadRule:
    mode = getattr(args, "count_read_rule", None) or READ_RULE_LEGACY
    policy = getattr(args, "multimapper_policy", None)
    return ReadRule(
        mode=mode,
        min_mapq=int(getattr(args, "minMAPQ", 0) or 0),
        multimapper_policy=policy,
        assignment=getattr(args, "count_assignment", None),
    )


def read_rule_token(args: Any) -> str | None:
    if not args:
        return None
    return read_rule_from_args(args).token


def read_rule_counting_token(args: Any) -> str | None:
    if not args:
        return None
    return read_rule_from_args(args).counting_token
