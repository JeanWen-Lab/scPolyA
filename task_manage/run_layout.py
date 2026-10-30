from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Sequence
import json
import re

from logging_config import get_logger, LOG_NC, CURRENT_SUFFIX, CURRENT_TASK, CURRENT_STAGE
from task_manage.artifact_naming import (
    ARTIFACT_NAME_MANIFEST_FILE_V1,
    ArtifactNameManifestError,
    ScopedArtifactNameV1,
    annotation_name_payload,
    build_scoped_artifact_name_v1,
    configured_artifact_naming_mode,
    load_artifact_name_manifest_v1,
    matrix_name_payload,
    normalize_artifact_naming_mode,
    peak_name_payload,
    register_artifact_name_v1,
    rna_gene_name_payload,
)
from quantification_config import (
    resolved_quantification_mode,
    summary_quantification_signature,
    terminal_aware_quantification_payload,
    terminal_annotation_schema_payload,
)

logger_nc = get_logger(LOG_NC)


UNFILTERED_STD_CHROM_SIZES = "unfiltered_standard_chrom_sizes"
FILTERED_STD_CHROM_SIZES = "filtered_standard_chrom_sizes"
UNFILTERED_ADJUSTED_GTF = "unfiltered_adjusted_gtf"
FILTERED_ADJUSTED_GTF = "filtered_adjusted_gtf"

GENE_BED = "gene_bed"
SORTED_GENE_BED = "sorted_gene_bed"
GFFDB = "gffdb"
CANONICAL_TRANSCRIPTS = "canonical_transcripts"
STOP_CODON_POSITIONS = "stop_codon_positions_json"
STOP_CODON_POSITIONS_TSV = "stop_codon_positions_tsv"
SORTED_STOP_CODON_BED = "sorted_stop_codon_bed"
SORTED_STOP_CODON_BIGBED = "sorted_stop_codon_bigbed"
CDS_GTF = "cds_gtf"
CDS_BED = "cds_bed"
SORTED_CDS_BED = "sorted_cds_bed"
ARG_CHROM_SIZES = "chrom_sizes"
ARG_GENE_GTF = "gene_gtf"
ARG_BULK_WINDOW_GTF = "bulk_window_gtf"
ARG_BULK_GENE_GTF = "bulk_gene_gtf"
ASSIGNED_PEAKS_BED = "assigned_peaks_bed"

UNFILTERED_ADJUSTED_BULK_WINDOW_GTF = "unfiltered_adjusted_bulk_window_gtf"
FILTERED_ADJUSTED_BULK_WINDOW_GTF = "filtered_adjusted_bulk_window_gtf"
BULK_WINDOW_BED = "bulk_window_bed"
SORTED_BULK_WINDOW_BED = "sorted_bulk_window_bed"
UNFILTERED_ADJUSTED_BULK_GENE_GTF = "unfiltered_adjusted_bulk_gene_gtf"
FILTERED_ADJUSTED_BULK_GENE_GTF = "filtered_adjusted_bulk_gene_gtf"
BULK_GENE_BED = "bulk_gene_bed"
SORTED_BULK_GENE_BED = "sorted_bulk_gene_bed"


VALID_REF_KEYS = {
    UNFILTERED_STD_CHROM_SIZES: (lambda x: f"{Path(x).stem}.chrom.sizes.standardized", ARG_CHROM_SIZES),
    FILTERED_STD_CHROM_SIZES: (lambda x: f"{Path(x).stem}.chrom.sizes.standardized.filtered", ARG_CHROM_SIZES),
    UNFILTERED_ADJUSTED_GTF: (lambda x: f"{Path(x).stem}.adjusted.gtf", ARG_GENE_GTF),
    FILTERED_ADJUSTED_GTF: (lambda x: f"{Path(x).stem}.adjusted.filtered.gtf", ARG_GENE_GTF),
    GENE_BED: (lambda x: f"{Path(x).stem}.gene.bed", ARG_GENE_GTF),
    SORTED_GENE_BED: (lambda x: f"{Path(x).stem}.gene.sorted.bed", ARG_GENE_GTF),
    GFFDB: (lambda x: f"{Path(x).stem}.gffdb.db", ARG_GENE_GTF),
    CANONICAL_TRANSCRIPTS: (lambda x: f"{Path(x).stem}.canonical_transcripts.json", ARG_GENE_GTF),
    STOP_CODON_POSITIONS: (lambda x: f"{Path(x).stem}.stop_codon_positions.json", ARG_GENE_GTF),
    STOP_CODON_POSITIONS_TSV: (lambda x: f"{Path(x).stem}.stop_codon_positions.tsv", ARG_GENE_GTF),
    SORTED_STOP_CODON_BED: (lambda x: f"{Path(x).stem}.stop_codon_positions.sorted.bed", ARG_GENE_GTF),
    SORTED_STOP_CODON_BIGBED: (lambda x: f"{Path(x).stem}.stop_codon_positions.sorted.bb", ARG_GENE_GTF),
    CDS_GTF: (lambda x: f"{Path(x).stem}.CDS.gtf", ARG_GENE_GTF),
    CDS_BED: (lambda x: f"{Path(x).stem}.CDS.bed", ARG_GENE_GTF),
    SORTED_CDS_BED: (lambda x: f"{Path(x).stem}.CDS.sorted.bed", ARG_GENE_GTF),

    UNFILTERED_ADJUSTED_BULK_WINDOW_GTF: (lambda x: f"{Path(x).stem}.bulk_window.adjusted.gtf", ARG_BULK_WINDOW_GTF),
    FILTERED_ADJUSTED_BULK_WINDOW_GTF: (lambda x: f"{Path(x).stem}.bulk_window.adjusted.filtered.gtf",
                                        ARG_BULK_WINDOW_GTF),
    BULK_WINDOW_BED: (lambda x: f"{Path(x).stem}.bulk_window.bed", ARG_BULK_WINDOW_GTF),
    SORTED_BULK_WINDOW_BED: (lambda x: f"{Path(x).stem}.bulk_window.sorted.bed", ARG_BULK_WINDOW_GTF),
    UNFILTERED_ADJUSTED_BULK_GENE_GTF: (lambda x: f"{Path(x).stem}.bulk_gene.adjusted.gtf", ARG_BULK_GENE_GTF),
    FILTERED_ADJUSTED_BULK_GENE_GTF: (lambda x: f"{Path(x).stem}.bulk_gene.adjusted.filtered.gtf",
                                      ARG_BULK_GENE_GTF),
    BULK_GENE_BED: (lambda x: f"{Path(x).stem}.bulk_gene.bed", ARG_BULK_GENE_GTF),
    SORTED_BULK_GENE_BED: (lambda x: f"{Path(x).stem}.bulk_gene.sorted.bed", ARG_BULK_GENE_GTF)
}

@dataclass(frozen=True)
class RunLayout:
    out: Path
    run_id: str
    prefix: str = ""
    artifact_naming_mode: str | None = None

    # -------- run roots --------
    @property
    def Root(self) -> Path:
        return self.out / self.clean_name(self.run_id)

    @property
    def Inputs(self) -> Path:
        return self.Root / "inputs"

    @property
    def InputTables(self) -> Path:
        return self.Root / "input_tables"

    @property
    def UnitOutputsDir(self) -> Path:
        return self.Root / "unit_outputs"





    def UnitOutput(self, unit_id) -> Path:
        _ = self.Root / "unit_outputs" / str(unit_id)
        _.mkdir(exist_ok=True)
        return _

    @property
    def MergedCohort(self) -> Path:
        return self.Root / "merged_cohort"

    @property
    def CohortOutputDir(self) -> Path:
        return self.Root / "cohort_output"

    def CohortOutput(self, cohort_name) -> Path:
        _ = self.Root / "cohort_output" / str(cohort_name)
        _.mkdir(exist_ok=True)
        return _

    @property
    def FinalOutput(self) -> Path:
        return self.Root / "final_output"

    @property
    def ArtifactNameManifest(self) -> Path:
        return self.State / ARTIFACT_NAME_MANIFEST_FILE_V1

    @property
    def ArtifactNamingMode(self) -> Literal["short", "legacy"]:
        requested = (
            normalize_artifact_naming_mode(self.artifact_naming_mode)
            if self.artifact_naming_mode is not None
            else configured_artifact_naming_mode()
        )
        manifest = load_artifact_name_manifest_v1(self.ArtifactNameManifest)
        if manifest is not None:
            if requested != "auto" and requested != manifest.mode:
                raise ArtifactNameManifestError(
                    "Artifact naming mode cannot change within one run: "
                    f"{manifest.mode!r} -> {requested!r}"
                )
            return manifest.mode

        has_legacy, has_short = self._artifact_namespace_presence()
        if has_legacy and has_short:
            raise ArtifactNameManifestError(
                "Run contains mixed legacy and short artifact namespaces "
                "without an artifact name manifest"
            )
        if has_short:
            raise ArtifactNameManifestError(
                "Run contains short artifact namespaces but lacks "
                f"{ARTIFACT_NAME_MANIFEST_FILE_V1}"
            )
        if requested == "short":
            if has_legacy:
                raise ArtifactNameManifestError(
                    "Explicit short artifact naming would split an existing "
                    "legacy run"
                )
            return "short"
        if requested == "legacy":
            return "legacy"
        return "legacy" if has_legacy else "short"


    @staticmethod
    def FinalName(dedup_method: str, polyA_status:str,
                  coverage_scaling: str,
                  depth_threshold: float | str, depth_threshold_polyA: float | str,
                  merge_distance: int,
                  merge_distance_polyA: int | None,
                  misprime_A_count: int,
                  misprime_in: int,
                  misprimed_ratio_threshold:float,
                  quantification_mode: str = "legacy", ):
        cat_list = [
            "final",
            dedup_method,
            polyA_status,  # args.minpolyA, args.minMAPQ, args.nonA_allowed
            "cov-scl-" + str(coverage_scaling),
            "dp" + str(depth_threshold),
            "dpA" + str(depth_threshold_polyA),
            "md" + str(merge_distance),
            "mdA" + str(merge_distance_polyA),
            f"misp-A{misprime_A_count}-in{misprime_in}-rt{misprimed_ratio_threshold}"
        ]
        if quantification_mode != "legacy":
            cat_list.append(f"quant-{quantification_mode}")
        final = ".".join(cat_list)

        logger_nc.debug(
            f"FinalName={final} | "
            f"stage={CURRENT_STAGE.get()!r} "
            f"task={CURRENT_TASK.get()!r} "
            f"suffix={CURRENT_SUFFIX.get()!r}"
        )

        return final


    @staticmethod
    def LegacyFinalNameFromDict(d: Mapping[str, Any]) -> str:
        return RunLayout.FinalName(
            dedup_method=d["dedup_method"],
            polyA_status=d["polyA_status"],
            coverage_scaling=d["coverage_scaling"],
            depth_threshold=d["depth_threshold"],
            depth_threshold_polyA=d["depth_threshold_polyA"],
            merge_distance=d["merge_distance"],
            merge_distance_polyA=d["merge_distance_polyA"],
            misprime_A_count=d["misprime_A_count"],
            misprime_in=d["misprime_in"],
            misprimed_ratio_threshold=d["misprimed_ratio_threshold"],
            quantification_mode=resolved_quantification_mode(d),
        )

    @staticmethod
    def LegacyAnnotationSubdirName(d: Mapping[str, Any]) -> str:
        source = str(d.get("annotate_peak_source", "resolved")).strip()
        if not source:
            raise ArtifactNameManifestError(
                "Annotation source cannot be blank"
            )
        name = f"annotate-src-{source}"
        schema = d.get("terminal_annotation_schema") or (
            terminal_annotation_schema_payload(d).get(
                "terminal_annotation_schema"
            )
        )
        if schema not in (None, ""):
            name = f"{name}.{schema}"
        if "/" in name or "\\" in name:
            raise ArtifactNameManifestError(
                "Annotation source must be one path segment"
            )
        return name

    @staticmethod
    def LegacyMatrixSubdirName(
        psi_min_gene_umis: Any,
        quantification_config: Mapping[str, Any] | None = None,
    ) -> str:
        name = (
            f"psi-min-gene-umis-{psi_min_gene_umis}"
            f"{summary_quantification_signature(quantification_config or {})}"
        )
        if "/" in name or "\\" in name:
            raise ArtifactNameManifestError(
                "Matrix summary name must be one path segment"
            )
        return name

    def PeakNameRecord(
        self,
        d: Mapping[str, Any],
    ) -> ScopedArtifactNameV1:
        return build_scoped_artifact_name_v1(
            "peak",
            peak_name_payload(d),
            legacy_name=self.LegacyFinalNameFromDict(d),
        )

    def AnnotationNameRecord(
        self,
        d: Mapping[str, Any],
    ) -> ScopedArtifactNameV1:
        peak_record = self.PeakNameRecord(d)
        schema = d.get("terminal_annotation_schema") or (
            terminal_annotation_schema_payload(d).get(
                "terminal_annotation_schema"
            )
        )
        legacy_name = (
            f"{peak_record.legacy_name}."
            f"{self.LegacyAnnotationSubdirName(d)}"
        )
        return build_scoped_artifact_name_v1(
            "annotation",
            annotation_name_payload(
                peak_record.payload,
                annotate_peak_source=d.get(
                    "annotate_peak_source",
                    "resolved",
                ),
                terminal_annotation_schema=schema,
            ),
            legacy_name=legacy_name,
        )

    def MatrixNameRecord(
        self,
        d: Mapping[str, Any],
        *,
        psi_min_gene_umis: Any,
    ) -> ScopedArtifactNameV1:
        annotation_record = self.AnnotationNameRecord(d)
        legacy_name = (
            f"{annotation_record.legacy_name}."
            f"{self.LegacyMatrixSubdirName(psi_min_gene_umis, d)}"
        )
        return build_scoped_artifact_name_v1(
            "matrix",
            matrix_name_payload(
                annotation_record.payload,
                psi_min_gene_umis=psi_min_gene_umis,
                quantification_config=terminal_aware_quantification_payload(d),
            ),
            legacy_name=legacy_name,
        )

    def RnaGeneNameRecord(
        self,
        d: Mapping[str, Any],
    ) -> ScopedArtifactNameV1:
        return build_scoped_artifact_name_v1(
            "rna_gene",
            rna_gene_name_payload(d),
            legacy_name=self.LegacyRnaGeneMatrixNameFromDict(d),
        )

    def FinalNameFromDict(self, d: Mapping[str, Any]) -> str:
        record = self.PeakNameRecord(d)
        return (
            record.code
            if self.ArtifactNamingMode == "short"
            else record.legacy_name
        )

    def FinalNameCandidatesFromDict(
        self,
        d: Mapping[str, Any],
    ) -> tuple[str, ...]:
        record = self.PeakNameRecord(d)
        return self._active_name_candidates(
            short_name=record.code,
            legacy_name=record.legacy_name,
        )

    def AnnotationSubdirNameFromDict(
        self,
        d: Mapping[str, Any],
    ) -> str:
        if self.ArtifactNamingMode == "short":
            return self.AnnotationNameRecord(d).code
        return self.LegacyAnnotationSubdirName(d)

    def AnnotationSubdirNameCandidatesFromDict(
        self,
        d: Mapping[str, Any],
    ) -> tuple[str, ...]:
        return self._active_name_candidates(
            short_name=self.AnnotationNameRecord(d).code,
            legacy_name=self.LegacyAnnotationSubdirName(d),
        )

    def AnnotatedPeakSignatureFromDict(
        self,
        d: Mapping[str, Any],
    ) -> str:
        record = self.AnnotationNameRecord(d)
        return (
            record.code
            if self.ArtifactNamingMode == "short"
            else record.legacy_name
        )

    def AnnotatedPeakSignatureCandidatesFromDict(
        self,
        d: Mapping[str, Any],
    ) -> tuple[str, ...]:
        record = self.AnnotationNameRecord(d)
        return self._active_name_candidates(
            short_name=record.code,
            legacy_name=record.legacy_name,
        )

    def MatrixSubdirNameFromDict(
        self,
        d: Mapping[str, Any],
        *,
        psi_min_gene_umis: Any,
    ) -> str:
        if self.ArtifactNamingMode == "short":
            return self.MatrixNameRecord(
                d,
                psi_min_gene_umis=psi_min_gene_umis,
            ).code
        return self.LegacyMatrixSubdirName(psi_min_gene_umis, d)

    def MatrixSignatureFromDict(
        self,
        d: Mapping[str, Any],
        *,
        psi_min_gene_umis: Any,
    ) -> str:
        record = self.MatrixNameRecord(
            d,
            psi_min_gene_umis=psi_min_gene_umis,
        )
        if self.ArtifactNamingMode == "short":
            return (
                f"{self.AnnotationNameRecord(d).code}."
                f"{self.LegacyMatrixSubdirName(psi_min_gene_umis, d)}"
            )
        return record.legacy_name

    def FinalFile(self, suffix: str, d: Mapping[str, Any]) -> str:
        return f"{self.FinalNameFromDict(d)}.{suffix}"

    def FinalFileCandidates(
        self,
        suffix: str,
        d: Mapping[str, Any],
    ) -> tuple[str, ...]:
        return tuple(
            f"{name}.{suffix}"
            for name in self.FinalNameCandidatesFromDict(d)
        )

    @staticmethod
    def RnaGeneMatrixName(dedup_method: str, cb_tag: str, umi_tag: str):
        return ".".join([
            "rna-gene-matrix",
            dedup_method,
            f"cb-{cb_tag}",
            f"umi-{umi_tag}",
        ])

    @staticmethod
    def LegacyRnaGeneMatrixNameFromDict(
        d: Mapping[str, Any],
    ) -> str:
        return RunLayout.RnaGeneMatrixName(
            dedup_method=d["dedup_method"],
            cb_tag=d["cb_tag"],
            umi_tag=d["umi_tag"],
        )

    def RnaGeneMatrixNameFromDict(
        self,
        d: Mapping[str, Any],
    ) -> str:
        record = self.RnaGeneNameRecord(d)
        return (
            record.code
            if self.ArtifactNamingMode == "short"
            else record.legacy_name
        )

    def RnaGeneMatrixNameCandidatesFromDict(
        self,
        d: Mapping[str, Any],
    ) -> tuple[str, ...]:
        record = self.RnaGeneNameRecord(d)
        return self._active_name_candidates(
            short_name=record.code,
            legacy_name=record.legacy_name,
        )

    def FinalOutputSubFromName(self, final_name: str) -> Path:
        if self.ArtifactNamingMode == "short":
            raise ArtifactNameManifestError(
                "FinalOutputSubFromName cannot create an unregistered short "
                "artifact path; use FinalOutputSub with a config mapping"
            )
        path = self.FinalOutput / str(final_name)
        path.mkdir(exist_ok=True)
        return path

    def FinalOutputSub(
        self,
        d: Mapping[str, Any],
    ) -> Path:
        self._register_artifact_records(self.PeakNameRecord(d))
        path = self.FinalOutput / self.FinalNameFromDict(d)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def FinalOutputSubCandidates(
        self,
        d: Mapping[str, Any],
    ) -> tuple[Path, ...]:
        return tuple(
            self.FinalOutput / name
            for name in self.FinalNameCandidatesFromDict(d)
        )

    def FinalOutputFileCandidates(
        self,
        *,
        suffix: str,
        d: Mapping[str, Any],
    ) -> tuple[Path, ...]:
        return tuple(
            self.FinalOutput / name / f"{name}.{suffix}"
            for name in self.FinalNameCandidatesFromDict(d)
        )

    def ResolveFinalOutputFile(
        self,
        *,
        suffix: str,
        d: Mapping[str, Any],
    ) -> Path:
        return self.ResolveExistingArtifact(
            self.FinalOutputFileCandidates(suffix=suffix, d=d),
            label=f"final output {suffix}",
        )

    def AnnotatedFinalOutputSub(
        self,
        d: Mapping[str, Any],
    ) -> Path:
        self._register_artifact_records(
            self.PeakNameRecord(d),
            self.AnnotationNameRecord(d),
        )
        path = (
            self.FinalOutput
            / self.FinalNameFromDict(d)
            / self.AnnotationSubdirNameFromDict(d)
        )
        path.mkdir(parents=True, exist_ok=True)
        return path

    def AnnotatedFinalOutputSubCandidates(
        self,
        d: Mapping[str, Any],
    ) -> tuple[Path, ...]:
        peak_record = self.PeakNameRecord(d)
        annotation_record = self.AnnotationNameRecord(d)
        short_path = (
            self.FinalOutput / peak_record.code / annotation_record.code
        )
        legacy_path = (
            self.FinalOutput
            / peak_record.legacy_name
            / self.LegacyAnnotationSubdirName(d)
        )
        return self._active_path_candidates(
            short_path=short_path,
            legacy_path=legacy_path,
        )

    def AnnotatedFinalOutputFileCandidates(
        self,
        *,
        suffix: str,
        d: Mapping[str, Any],
    ) -> tuple[Path, ...]:
        peak_record = self.PeakNameRecord(d)
        annotation_record = self.AnnotationNameRecord(d)
        short_path = (
            self.FinalOutput
            / peak_record.code
            / annotation_record.code
            / f"{peak_record.code}.{suffix}"
        )
        legacy_path = (
            self.FinalOutput
            / peak_record.legacy_name
            / self.LegacyAnnotationSubdirName(d)
            / f"{peak_record.legacy_name}.{suffix}"
        )
        return self._active_path_candidates(
            short_path=short_path,
            legacy_path=legacy_path,
        )

    def ResolveAnnotatedFinalOutputFile(
        self,
        *,
        suffix: str,
        d: Mapping[str, Any],
    ) -> Path:
        return self.ResolveExistingArtifact(
            self.AnnotatedFinalOutputFileCandidates(
                suffix=suffix,
                d=d,
            ),
            label=f"annotated final output {suffix}",
        )

    @property
    def AnnotatedBamsTemp(self) -> Path:
        return self.Root / "annotated_bams_temp"

    def AnnotatedBamsTempSub(
        self,
        d: Mapping[str, Any],
    ) -> Path:
        return self._peak_scoped_output_subdir(
            self.AnnotatedBamsTemp,
            d,
        )

    @property
    def AnnotatedBamsOutputs(self) -> Path:
        return self.Root / "annotated_bams"

    def AnnotatedBamsSub(
        self,
        d: Mapping[str, Any],
    ) -> Path:
        return self._peak_scoped_output_subdir(
            self.AnnotatedBamsOutputs,
            d,
        )

    @property
    def PeakSidecarsOutputs(self) -> Path:
        return self.Root / "peak_sidecars"

    def PeakSidecarsSub(
        self,
        d: Mapping[str, Any],
    ) -> Path:
        return self._peak_scoped_output_subdir(
            self.PeakSidecarsOutputs,
            d,
        )

    def PeakSidecarsConfigSub(
        self,
        d: Mapping[str, Any],
    ) -> Path:
        return self._annotation_scoped_output_subdir(
            self.PeakSidecarsOutputs,
            d,
        )

    def PeakSidecarsConfigSubCandidates(
        self,
        d: Mapping[str, Any],
    ) -> tuple[Path, ...]:
        return self._annotation_scoped_output_candidates(
            self.PeakSidecarsOutputs,
            d,
        )

    def PeakSidecarFileCandidates(
        self,
        *,
        file_name: str,
        d: Mapping[str, Any],
    ) -> tuple[Path, ...]:
        return tuple(
            root / file_name
            for root in self.PeakSidecarsConfigSubCandidates(d)
        )

    def ResolvePeakSidecarFile(
        self,
        *,
        file_name: str,
        d: Mapping[str, Any],
    ) -> Path:
        return self.ResolveExistingArtifact(
            self.PeakSidecarFileCandidates(
                file_name=file_name,
                d=d,
            ),
            label=f"peak sidecar {file_name}",
        )

    @property
    def CountMatrixOutputs(self) -> Path:
        return self.Root / "count_matrix"

    def CountMatrixSub(
        self,
        d: Mapping[str, Any],
    ) -> Path:
        return self._peak_scoped_output_subdir(
            self.CountMatrixOutputs,
            d,
        )

    def CountMatrixConfigSub(
        self,
        d: Mapping[str, Any],
    ) -> Path:
        return self._annotation_scoped_output_subdir(
            self.CountMatrixOutputs,
            d,
        )

    def CountMatrixConfigSubCandidates(
        self,
        d: Mapping[str, Any],
    ) -> tuple[Path, ...]:
        return self._annotation_scoped_output_candidates(
            self.CountMatrixOutputs,
            d,
        )

    def ResolveCountMatrixFile(
        self,
        *,
        file_name: str,
        d: Mapping[str, Any],
    ) -> Path:
        return self.ResolveExistingArtifact(
            tuple(
                root / file_name
                for root in self.CountMatrixConfigSubCandidates(d)
            ),
            label=f"count matrix {file_name}",
        )

    @property
    def CountMatrixTempOutputs(self) -> Path:
        return self.Root / "count_matrix_temp"

    def CountMatrixTempSub(
        self,
        d: Mapping[str, Any],
    ) -> Path:
        return self._peak_scoped_output_subdir(
            self.CountMatrixTempOutputs,
            d,
        )

    def CountMatrixTempConfigSub(
        self,
        d: Mapping[str, Any],
    ) -> Path:
        return self._annotation_scoped_output_subdir(
            self.CountMatrixTempOutputs,
            d,
        )

    def CountMatrixTempConfigSubCandidates(
        self,
        d: Mapping[str, Any],
    ) -> tuple[Path, ...]:
        return self._annotation_scoped_output_candidates(
            self.CountMatrixTempOutputs,
            d,
        )

    @property
    def MatrixSummaryOutputs(self) -> Path:
        return self.Root / "matrix_summary"

    def MatrixSummarySub(
        self,
        d: Mapping[str, Any],
    ) -> Path:
        return self._peak_scoped_output_subdir(
            self.MatrixSummaryOutputs,
            d,
        )

    def MatrixSummaryConfigSub(
        self,
        d: Mapping[str, Any],
        *,
        psi_min_gene_umis: Any,
    ) -> Path:
        records = (
            self.PeakNameRecord(d),
            self.AnnotationNameRecord(d),
            self.MatrixNameRecord(
                d,
                psi_min_gene_umis=psi_min_gene_umis,
            ),
        )
        self._register_artifact_records(*records)
        path = (
            self.MatrixSummaryOutputs
            / self.FinalNameFromDict(d)
            / self.AnnotationSubdirNameFromDict(d)
            / self.MatrixSubdirNameFromDict(
                d,
                psi_min_gene_umis=psi_min_gene_umis,
            )
        )
        path.mkdir(parents=True, exist_ok=True)
        return path

    def MatrixSummaryConfigSubCandidates(
        self,
        d: Mapping[str, Any],
        *,
        psi_min_gene_umis: Any,
    ) -> tuple[Path, ...]:
        peak_record = self.PeakNameRecord(d)
        annotation_record = self.AnnotationNameRecord(d)
        matrix_record = self.MatrixNameRecord(
            d,
            psi_min_gene_umis=psi_min_gene_umis,
        )
        short_path = (
            self.MatrixSummaryOutputs
            / peak_record.code
            / annotation_record.code
            / matrix_record.code
        )
        legacy_path = (
            self.MatrixSummaryOutputs
            / peak_record.legacy_name
            / self.LegacyAnnotationSubdirName(d)
            / self.LegacyMatrixSubdirName(psi_min_gene_umis, d)
        )
        return self._active_path_candidates(
            short_path=short_path,
            legacy_path=legacy_path,
        )

    @property
    def RnaGeneMatrixOutputs(self) -> Path:
        return self.Root / "rna_gene_matrix"

    def RnaGeneMatrixSub(
        self,
        d: Mapping[str, Any],
    ) -> Path:
        self._register_artifact_records(self.RnaGeneNameRecord(d))
        path = (
            self.RnaGeneMatrixOutputs
            / self.RnaGeneMatrixNameFromDict(d)
        )
        path.mkdir(parents=True, exist_ok=True)
        return path

    def RnaGeneMatrixSubCandidates(
        self,
        d: Mapping[str, Any],
    ) -> tuple[Path, ...]:
        return tuple(
            self.RnaGeneMatrixOutputs / name
            for name in self.RnaGeneMatrixNameCandidatesFromDict(d)
        )

    @property
    def RnaGeneMatrixTempOutputs(self) -> Path:
        return self.Root / "rna_gene_matrix_temp"

    def RnaGeneMatrixTempSub(
        self,
        d: Mapping[str, Any],
    ) -> Path:
        self._register_artifact_records(self.RnaGeneNameRecord(d))
        path = (
            self.RnaGeneMatrixTempOutputs
            / self.RnaGeneMatrixNameFromDict(d)
        )
        path.mkdir(parents=True, exist_ok=True)
        return path

    def RnaGeneMatrixTempSubCandidates(
        self,
        d: Mapping[str, Any],
    ) -> tuple[Path, ...]:
        return tuple(
            self.RnaGeneMatrixTempOutputs / name
            for name in self.RnaGeneMatrixNameCandidatesFromDict(d)
        )

    def RnaGeneSidecarsSub(
        self,
        d: Mapping[str, Any],
    ) -> Path:
        path = self.RnaGeneMatrixTempSub(d) / "sidecars"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def FinalOutputSubFile(
        self,
        d: Mapping[str, Any],
        file_name: str,
    ) -> Path:
        path = self.FinalOutputSub(d) / file_name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def ResolveExistingArtifact(
        candidates: Sequence[Path],
        *,
        label: str,
    ) -> Path:
        ordered = tuple(dict.fromkeys(Path(path) for path in candidates))
        if not ordered:
            raise ArtifactNameManifestError(
                f"No path candidates were supplied for {label}"
            )
        existing = tuple(path for path in ordered if path.exists())
        if len(existing) > 1:
            raise ArtifactNameManifestError(
                f"Ambiguous {label}; multiple naming variants exist: "
                + ", ".join(str(path) for path in existing)
            )
        return existing[0] if existing else ordered[0]

    def _active_name_candidates(
        self,
        *,
        short_name: str,
        legacy_name: str,
    ) -> tuple[str, ...]:
        if self.ArtifactNamingMode == "short":
            return tuple(dict.fromkeys((short_name, legacy_name)))
        return tuple(dict.fromkeys((legacy_name, short_name)))

    def _active_path_candidates(
        self,
        *,
        short_path: Path,
        legacy_path: Path,
    ) -> tuple[Path, ...]:
        if self.ArtifactNamingMode == "short":
            return tuple(dict.fromkeys((short_path, legacy_path)))
        return tuple(dict.fromkeys((legacy_path, short_path)))

    def _peak_scoped_output_subdir(
        self,
        root: Path,
        d: Mapping[str, Any],
    ) -> Path:
        self._register_artifact_records(self.PeakNameRecord(d))
        path = root / self.FinalNameFromDict(d)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _annotation_scoped_output_subdir(
        self,
        root: Path,
        d: Mapping[str, Any],
    ) -> Path:
        self._register_artifact_records(
            self.PeakNameRecord(d),
            self.AnnotationNameRecord(d),
        )
        path = (
            root
            / self.FinalNameFromDict(d)
            / self.AnnotationSubdirNameFromDict(d)
        )
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _annotation_scoped_output_candidates(
        self,
        root: Path,
        d: Mapping[str, Any],
    ) -> tuple[Path, ...]:
        peak_record = self.PeakNameRecord(d)
        annotation_record = self.AnnotationNameRecord(d)
        return self._active_path_candidates(
            short_path=root / peak_record.code / annotation_record.code,
            legacy_path=(
                root
                / peak_record.legacy_name
                / self.LegacyAnnotationSubdirName(d)
            ),
        )

    def _register_artifact_records(
        self,
        *records: ScopedArtifactNameV1,
    ) -> None:
        mode = self.ArtifactNamingMode
        self._assert_write_namespace_consistent(mode)
        for record in records:
            register_artifact_name_v1(
                self.ArtifactNameManifest,
                mode=mode,
                record=record,
            )

    def _assert_write_namespace_consistent(
        self,
        mode: Literal["short", "legacy"],
    ) -> None:
        has_legacy, has_short = self._artifact_namespace_presence()
        if mode == "short" and has_legacy:
            raise ArtifactNameManifestError(
                "Refusing to write short artifact paths into a run that "
                "contains legacy artifact paths"
            )
        if mode == "legacy" and has_short:
            raise ArtifactNameManifestError(
                "Refusing to write legacy artifact paths into a run that "
                "contains short artifact paths"
            )

    def _artifact_namespace_presence(self) -> tuple[bool, bool]:
        has_legacy = False
        has_short = False
        peak_roots = (
            self.FinalOutput,
            self.AnnotatedBamsTemp,
            self.AnnotatedBamsOutputs,
            self.PeakSidecarsOutputs,
            self.CountMatrixOutputs,
            self.CountMatrixTempOutputs,
            self.MatrixSummaryOutputs,
        )
        for root in peak_roots:
            if not root.is_dir():
                continue
            for child in root.iterdir():
                if child.name.startswith("final."):
                    has_legacy = True
                elif re.fullmatch(r"pk-[0-9a-f]{16}", child.name):
                    has_short = True
        for root in (
            self.RnaGeneMatrixOutputs,
            self.RnaGeneMatrixTempOutputs,
        ):
            if not root.is_dir():
                continue
            for child in root.iterdir():
                if child.name.startswith("rna-gene-matrix."):
                    has_legacy = True
                elif re.fullmatch(r"rn-[0-9a-f]{16}", child.name):
                    has_short = True
        return has_legacy, has_short




    @property
    def Reference(self) -> Path:
        return self.Root / "reference"


    def ReferenceFile(self, file_key, args):
        if file_key in VALID_REF_KEYS:
            fn, arg_key = VALID_REF_KEYS[file_key]
            p = getattr(args, arg_key, None) if arg_key is not None else None
            return self.Reference / fn(p)
        else:
            raise KeyError(f"Invalid ref key: {file_key}. Supported: {sorted(VALID_REF_KEYS.keys())}.")

    @property
    def Samples(self) -> Path:
        return self.Root / "samples"

    @property
    def State(self) -> Path:
        return self.Root / "state"

    @property
    def Logs(self) -> Path:
        return self.Root / "logs"

    @property
    def LogSamtools(self) -> Path:
        return self.Root / "logs" / "samtools"

    @property
    def LogBedtools(self) -> Path:
        return self.Root / "logs" / "bedtools"


    @property
    def LogBedGraphToBigWig(self) -> Path:
        return self.Root / "logs" / "bedGraphToBigWig"

    @property
    def LogWigToBigWig(self) -> Path:
        return self.LogBedGraphToBigWig

    @property
    def LogUmitools(self) -> Path:
        return self.Root / "logs" / "umi_tools"

    @property
    def Backup(self) -> Path:
        return self.Root / "backup"

    # -------- reference layout --------
    @property
    def RefShare(self) -> Path:
        return self.Reference / "shared"

    @property
    def RefSpec(self) -> Path:
        return self.Reference / "specific"

    @property
    def chrom_sizes_standardized(self) -> Path:
        return self.Reference / "chrom.sizes.standardized"

    @property
    def chrom_sizes_filtered(self) -> Path:
        return self.Reference / "chrom.sizes.filtered.standardized"

    # -------- input registry --------
    @property
    def samples_registry(self) -> Path:
        # sample_id -> original bam path, etc.
        return self.Inputs / "samples.json"

    def input_bam_link(self, sample_id: str) -> Path:
        # global stable link name
        return self.Inputs / f"{sample_id}.bam"

    def input_bai_link(self, sample_id: str) -> Path:
        return self.Inputs / f"{sample_id}.bam.bai"

    # -------- per-sample, contig-centric workspace --------
    def sample_root(self, sample_id: str) -> Path:
        return self.Samples / sample_id

    def contigs_root(self, sample_id: str) -> Path:
        return self.sample_root(sample_id) / "contigs"

    def contig_root(self, sample_id: str, contig: str) -> Path:
        # contig: "all", "chr1", "chr2", ...
        return self.contigs_root(sample_id) / contig

    def stage_dir(self, sample_id: str, contig: str, stage: str) -> Path:
        # stage: "A_input", "B_split", "C_dedup", ...
        return self.contig_root(sample_id, contig) / stage

    def file(self, sample_id: str, contig: str, stage: str, name: str) -> Path:
        return self.stage_dir(sample_id, contig, stage) / name

    # # canonical input inside sample workspace (per-contig "all")
    # def canonical_input_bam(self, sample_id: str) -> Path:
    #     return self.file(sample_id, "all", "A_input", "bam.bam")
    #
    # def canonical_input_bai(self, sample_id: str) -> Path:
    #     return self.file(sample_id, "all", "A_input", "bam.bam.bai")

    # -------- state (machine-readable) --------
    @property
    def manifest(self) -> Path:
        return self.State / "manifest.json"

    def task_state_file(self, sample_id: str, contig: str) -> Path:
        safe = contig.replace("/", "_")
        return self.State / "tasks" / f"{sample_id}__{safe}.json"

    # -------- logs (human-readable) --------
    def stage_log(self, sample_id: str, contig: str, stage: str, name: str = "stage.log") -> Path:
        safe = contig.replace("/", "_")
        return self.Logs / sample_id / safe / stage / name

    def tool_log(self, sample_id: str, contig: str, stage: str, tool: str) -> Path:
        return self.stage_log(sample_id, contig, stage, f"{tool}.log")

    # -------- init helpers --------
    def ensure_base_dirs(self, sample_ids: Iterable[str] = ()) -> None:
        # ---- run-level roots ----
        for p in [
            self.Root,
            self.Inputs,
            self.InputTables,  # ✅ missing before
            self.UnitOutputsDir,
            # self.MergedCohort,
            self.CohortOutputDir,
            self.FinalOutput,
            self.Reference,
            self.RefShare,
            self.RefSpec,
            # self.Samples,
            self.State,
            self.Logs,
            self.LogSamtools,
            self.LogUmitools,
            self.LogBedGraphToBigWig,
            self.LogBedtools,
            self.Backup,
            self.PeakSidecarsOutputs,
            self.CountMatrixOutputs,
            self.CountMatrixTempOutputs,
            self.MatrixSummaryOutputs,
            self.RnaGeneMatrixOutputs,
            self.RnaGeneMatrixTempOutputs,
        ]:
            p.mkdir(parents=True, exist_ok=True)

        # ---- state subdirs ----
        (self.State / "tasks").mkdir(parents=True, exist_ok=True)

        # (optional but nice) if you ever write manifest early
        self.manifest.parent.mkdir(parents=True, exist_ok=True)

        # ---- minimal per-sample dirs ----
        for sid in sample_ids:
            self.sample_root(sid).mkdir(parents=True, exist_ok=True)
            self.contigs_root(sid).mkdir(parents=True, exist_ok=True)
            # (optional) create log root per sample so writing logs never fails
            (self.Logs / sid).mkdir(parents=True, exist_ok=True)

    def ensure_task_dir(self, sample_id: str, contig: str, stage: str) -> Path:
        d = self.stage_dir(sample_id, contig, stage)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write_samples_registry(self, mapping: dict[str, str]) -> None:
        self.Inputs.mkdir(parents=True, exist_ok=True)
        self.samples_registry.write_text(json.dumps(mapping, indent=2, sort_keys=True))

    @staticmethod
    def clean_name(string: str) -> str:
        """Replaces special characters in a string to make it a valid path component."""
        return re.sub(r'[^a-zA-Z0-9._-]', '_', string)
