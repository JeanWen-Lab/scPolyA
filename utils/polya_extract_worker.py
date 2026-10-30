"""Single-task subprocess entry point for Poly(A) BAM extraction."""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

from .polya_extract import bam_index_path, extract_polya_bam, validate_polya_bam
from .read_eligibility import MULTIMAPPER_POLICIES, READ_RULES, READ_RULE_LEGACY, ReadRule


def _cleanup_output(output_bam: Path) -> None:
    output_bam.unlink(missing_ok=True)
    bam_index_path(output_bam).unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-bam", type=Path, required=True)
    parser.add_argument("--output-bam", type=Path, required=True)
    parser.add_argument("--nonA-allowed", type=int, required=True)
    parser.add_argument("--minpolyA", type=int, required=True)
    parser.add_argument("--minMAPQ", type=int, required=True)
    parser.add_argument("--count-read-rule", choices=READ_RULES, default=READ_RULE_LEGACY)
    parser.add_argument("--multimapper-policy", choices=MULTIMAPPER_POLICIES, default=None)
    args = parser.parse_args(argv)

    output_bam = args.output_bam.resolve()
    output_index = bam_index_path(output_bam)
    try:
        if not args.input_bam.is_file():
            raise FileNotFoundError(f"Input BAM not found: {args.input_bam}")
        if output_bam.exists() or output_index.exists():
            raise FileExistsError(
                f"Refusing to overwrite non-unique temporary output: {output_bam}"
            )
        output_bam.parent.mkdir(parents=True, exist_ok=True)
        stats = extract_polya_bam(
            args.input_bam,
            output_bam,
            args.nonA_allowed,
            args.minpolyA,
            args.minMAPQ,
            read_rule=ReadRule(
                mode=args.count_read_rule,
                min_mapq=args.minMAPQ,
                multimapper_policy=args.multimapper_policy,
            ),
        )
        validate_polya_bam(output_bam)
        print(json.dumps(stats.as_dict(), sort_keys=True), flush=True)
        return 0
    except BaseException:
        _cleanup_output(output_bam)
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
