#!/usr/bin/env Rscript
# Native donor-level APA endpoints. Derived from the supplied handoff, with
# separate length eligibility, modern pinned edgeR API and strict completion.
options(stringsAsFactors=FALSE, warn=1, digits=17)
argv <- commandArgs(trailingOnly=TRUE)
a <- list()
if (!length(argv) || length(argv) %% 2L) stop("Backend requires key/value arguments; use scpolya-diff-apa --help")
for (i in seq.int(1L,length(argv),by=2L)) a[[sub("^--", "", argv[i])]] <- argv[i+1L]
if (nzchar(a$r_library)) .libPaths(c(a$r_library, .libPaths()))
write_tsv <- function(d, name) {
    d <- as.data.frame(d,check.names=FALSE)
    # write.table's numeric conversion may use fewer than 17 significant digits.
    # Explicit round-trip strings keep delivered p-values/effects reproducible.
    for(j in seq_along(d)) if(is.numeric(d[[j]]))
        d[[j]] <- ifelse(is.na(d[[j]]),NA_character_,sprintf("%.17g",d[[j]]))
    write.table(d,file.path(a$outdir,name),sep="\t",row.names=FALSE,quote=FALSE,na="NA")
}
write_matrix <- function(m,id,name) {
    d <- data.frame(rownames(m),m,check.names=FALSE); names(d)[1] <- id; write_tsv(d,name)
}
read_tsv <- function(name) read.delim(file.path(a$input,name),check.names=FALSE,colClasses="character",na.strings=character(),quote="",comment.char="")
need <- function(d, cols) {
    if (anyDuplicated(names(d))) stop("Duplicate table headers")
    if (!all(cols %in% names(d))) stop("Missing columns: ",paste(setdiff(cols,names(d)),collapse=", "))
}
warnlog <- list(); stage <- "preflight"; untestable <- FALSE
mode <- if(a$descriptive=="true") "descriptive" else "inference"
mode_reason <- if(a$descriptive=="true") "explicit_descriptive_only" else "sufficient_replicates"
empty_descriptive <- function(s, reason) {
    mode <<- "descriptive"; mode_reason <<- reason
    write_tsv(s,"samples_used.tsv")
    write_tsv(data.frame(unit_id=character(),gene_id=character(),strand=character(),n_pas=integer(),analysis_distal_status=character()),"differential_apa_summary.tsv")
    write_tsv(data.frame(unit_id=character(),feature_id=character(),mean_usage_control=numeric(),mean_usage_case=numeric(),delta_usage_pp=numeric()),"pas_effects.tsv")
    if(!file.exists(file.path(a$outdir,"native_distal_effects.tsv")))
        write_tsv(data.frame(unit_id=character(),native_delta_distal_usage_pp=numeric(),native_distal_status=character()),"native_distal_effects.tsv")
    writeLines(reason,file.path(a$outdir,"descriptive_reason.txt"))
    write_tsv(data.frame(package="R",version=as.character(getRversion())),"versions.tsv")
}
run <- function() {
    requested_methods <- strsplit(a$methods,",",fixed=TRUE)[[1]]
    cts <- read_tsv("pas_counts.tsv"); ann <- read_tsv("pas_annotation.tsv"); s <- read_tsv("samples.tsv")
    native_cts <- read_tsv("native_pas_counts.tsv"); native_ann <- read_tsv("native_pas_annotation.tsv")
    need(cts,c("unit_id","feature_id")); need(ann,c("unit_id","feature_id","gene_id","strand","length_bp","distance_to_stop_codon","is_distal_peak"))
    need(native_cts,c("unit_id","feature_id")); need(native_ann,c("unit_id","feature_id","is_distal_peak"))
    need(s,c("sample_id","donor_id","condition","cell_type","batch"))
    if (anyDuplicated(cts$feature_id) || anyDuplicated(ann$feature_id) || anyDuplicated(s$sample_id)) stop("Duplicate feature/sample IDs")
    if (a$control == a$case) stop("case and control must differ")
    excluded_pb <- read_tsv("excluded_pseudobulks.tsv")
    known_conditions <- unique(c(s$condition,excluded_pb$condition))
    if(!all(c(a$control,a$case) %in% known_conditions)) stop("Requested condition label is absent from input metadata (including excluded pseudobulks)")
    s <- s[s$condition %in% c(a$control,a$case),,drop=FALSE]
    if (any(!s$sample_id %in% names(cts))) stop("Missing requested sample/count columns")
    if (nrow(s) && length(unique(s$cell_type)) != 1L) stop("Analyze one cell type at a time")
    if (any(!nzchar(s$donor_id))) stop("donor_id is required")
    s$group <- factor(ifelse(s$condition==a$control,"control","case"),levels=c("control","case"))
    if (anyDuplicated(s[c("donor_id","condition")])) stop("Multiple pseudobulks for one donor/condition; aggregate technical libraries")
    paired <- a$paired == "true"; batch <- a$batch == "true"
    excluded_donors <- data.frame(donor_id=character(),reason=character(),sample_ids=character())
    if (paired && nrow(s)) {
        tab <- table(s$donor_id,s$group)
        incomplete <- rownames(tab)[rowSums(tab==1L)!=2L]
        if(length(incomplete)) {
            excluded_donors <- data.frame(donor_id=incomplete,reason="incomplete_pair",
                sample_ids=vapply(incomplete,function(d) paste(s$sample_id[s$donor_id==d],collapse=","),character(1)))
            s <- s[!s$donor_id %in% incomplete,,drop=FALSE]
        }
    } else if (!paired && anyDuplicated(s$donor_id)) stop("A donor occurs in both groups; use --paired")
    write_tsv(excluded_donors,"excluded_donors.tsv")
    if(!nrow(s)) { empty_descriptive(s,"insufficient_replicates_no_eligible_pseudobulks_or_complete_pairs"); return(invisible(NULL)) }
    if (any(table(s$group)<2L)) {
        mode <<- "descriptive"; mode_reason <<- "insufficient_replicates"
    }
    descriptive <- mode=="descriptive"
    methods <- if(descriptive) character() else requested_methods
    if (!descriptive && any(table(s$group)<3L)) warning("Fewer than 3 donors in a group; inference is fragile")
    terms <- character()
    if (paired && !descriptive) { s$donor_id <- factor(s$donor_id); terms <- "donor_id" }
    if (batch && !descriptive) {
        if (any(!nzchar(s$batch)) || length(unique(s$batch))<2L) stop("--batch requires >=2 nonempty batch levels")
        s$batch <- factor(s$batch); terms <- c(terms,"batch")
    }
    design <- model.matrix(reformulate(c(terms,"group")),s); rownames(design) <- s$sample_id
    if (!descriptive && (qr(design)$rank != ncol(design) || nrow(design)<=ncol(design))) stop("Design is confounded or has no residual degrees of freedom")
    if (!descriptive && all(nzchar(as.character(s$batch))) && length(unique(s$batch))>=2L) {
        if(!batch && length(unique(s$batch))==nrow(s)) {
            warning("Saturated per-pseudobulk batch labels cannot support batch adjustment or confounding assessment; --batch was omitted and selected inference remains unadjusted for batch")
        } else {
            nuisance_terms <- c(if(paired) "donor_id", "batch")
            nuisance <- model.matrix(reformulate(nuisance_terms),s)
            audit <- model.matrix(reformulate(c(nuisance_terms,"group")),s)
            if(qr(audit)$rank<=qr(nuisance)$rank) stop("Condition is confounded with recorded batch/donor; cannot infer a group effect even if --batch is omitted")
            if(!batch && qr(cbind(design,model.matrix(~batch,s)))$rank>qr(design)$rank)
                warning("Recorded batch varies beyond the selected design but --batch was omitted; descriptive effects are unadjusted and inference uses the recorded design")
        }
    }
    packages <- unique(c(if ("limma" %in% methods) c("limma","statmod"),
                         if ("edger" %in% methods) c("edgeR","limma","statmod"),
                         if ("drimseq" %in% methods) c("DRIMSeq","BiocParallel")))
    missing <- packages[!vapply(packages,requireNamespace,logical(1),quietly=TRUE)]
    if (length(missing)) stop("Missing R packages: ",paste(missing,collapse=", "),"; no requested endpoint skipped")
    write_tsv(data.frame(package=c("R",packages),version=c(as.character(getRversion()),vapply(packages,function(p) as.character(packageVersion(p)),character(1)))),"versions.tsv")
    if ("edger" %in% methods && (!exists("diffSplice.DGEGLM",envir=asNamespace("edgeR"),inherits=FALSE) || packageVersion("edgeR") < "4.6.0"))
        stop("This module requires modern edgeR >= 4.6 with diffSplice.DGEGLM; no automatic legacy-method substitution")
    coef_name <- "groupcase"
    x <- as.matrix(cts[,s$sample_id,drop=FALSE]); suppressWarnings(storage.mode(x)<-"double")
    if (any(!is.finite(x)) || any(x<0) || any(x!=floor(x)) || any(x>.Machine$integer.max)) stop("Counts must be nonnegative raw integers <= R integer max")
    rownames(x) <- cts$feature_id
    ann <- ann[match(cts$feature_id,ann$feature_id),,drop=FALSE]
    if (anyNA(ann$feature_id) || any(ann$unit_id!=cts$unit_id) || any(!nzchar(ann$unit_id))) stop("Count/annotation unit mapping differs")
    ann$length_bp <- suppressWarnings(as.numeric(ann$length_bp))
    if (any(is.finite(ann$length_bp) & ann$length_bp<0)) stop("Invalid negative length in prepared input")
    ann$distance_to_stop_codon <- suppressWarnings(as.numeric(ann$distance_to_stop_codon))
    native_ann <- native_ann[match(native_cts$feature_id,native_ann$feature_id),,drop=FALSE]
    nx <- as.matrix(native_cts[,s$sample_id,drop=FALSE]); storage.mode(nx)<-"double"; rownames(nx)<-native_cts$feature_id
    if(anyNA(native_ann$feature_id) || any(native_ann$unit_id!=native_cts$unit_id) || any(!is.finite(nx)) || any(nx<0) || any(nx!=floor(nx))) stop("Invalid native count/annotation mapping")
    nt <- rowsum(nx,native_ann$unit_id,reorder=TRUE)
    native_ids <- rownames(nt)
    native_distal_ids <- lapply(native_ids,function(u) native_ann$feature_id[native_ann$unit_id==u & native_ann$is_distal_peak=="true"])
    names(native_distal_ids)<-native_ids
    nusage <- nx/nt[match(native_ann$unit_id,native_ids),,drop=FALSE]
    ndistal <- rowsum(nusage*(native_ann$is_distal_peak=="true"),native_ann$unit_id,reorder=TRUE)
    native_npas <- as.integer(table(native_ann$unit_id)[native_ids])
    native_status <- ifelse(lengths(native_distal_ids)==0L,"unavailable_native_distal_identity",ifelse(native_npas<2L,"fewer_than_two_native_pas","native_catalogue_all_ties"))
    ndistal[native_status!="native_catalogue_all_ties",]<-NA_real_
    # Coverage status does not invalidate the other measured pseudobulks.
    # Keep the preceding identity/low-PAS priorities and mask only true 0/0 cells.
    native_status[native_status=="native_catalogue_all_ties" & rowSums(nt==0)>0L]<-"zero_native_denominator"
    control <- s$group=="control"; case <- s$group=="case"
    native_effects <- data.frame(unit_id=native_ids,n_native_pas=native_npas,
        native_mean_distal_usage_control=rowMeans(ndistal[,control,drop=FALSE]),native_mean_distal_usage_case=rowMeans(ndistal[,case,drop=FALSE]),
        native_distal_status=native_status)
    native_effects$native_delta_distal_usage_pp<-100*(native_effects$native_mean_distal_usage_case-native_effects$native_mean_distal_usage_control)
    write_tsv(native_effects,"native_distal_effects.tsv")
    write_matrix(nt,"unit_id","native_unit_counts.tsv");write_matrix(ndistal,"unit_id","native_distal_usage.tsv")
    full_ann <- ann; full_x <- x
    full_totals <- rowsum(x,ann$unit_id,reorder=TRUE)
    write_matrix(full_totals,"unit_id","analysis_set_unit_counts.tsv")
    min_samples <- if (a$min_pas_samples=="auto") max(1L,min(table(s$group))) else as.numeric(a$min_pas_samples)
    if (!is.finite(min_samples) || min_samples<1 || min_samples>ncol(x) || min_samples!=floor(min_samples)) stop("Invalid --min-pas-samples")
    pas_keep <- rowSums(x>=as.numeric(a$min_pas_count))>=min_samples
    kept_x <- x[pas_keep,,drop=FALSE]; kept_ann <- ann[pas_keep,,drop=FALSE]
    if (nrow(kept_x)) {
        unit_totals <- rowsum(kept_x,kept_ann$unit_id,reorder=TRUE)
        n_pas <- table(kept_ann$unit_id)
        unit_keep <- rownames(unit_totals)[rowSums(unit_totals>=as.numeric(a$min_unit_count))==ncol(x) & n_pas[rownames(unit_totals)]>=2L]
    } else unit_keep <- character()
    pas_reason <- ifelse(!pas_keep,"below_pas_coverage",ifelse(ann$unit_id %in% unit_keep,"retained","unit_below_coverage_or_fewer_than_two_pas"))
    write_tsv(data.frame(unit_id=ann$unit_id,feature_id=ann$feature_id,status=pas_reason),"pas_filtering.tsv")
    unit_ids <- rownames(full_totals)
    unit_diag <- data.frame(unit_id=unit_ids,n_pas_before=as.integer(table(ann$unit_id)[unit_ids]),n_pas_after=0L,status="excluded")
    after_counts <- table(kept_ann$unit_id)
    unit_diag$n_pas_after <- as.integer(after_counts[match(unit_ids,names(after_counts))]); unit_diag$n_pas_after[is.na(unit_diag$n_pas_after)]<-0L
    unit_diag$status[unit_diag$n_pas_after<2L]<-"fewer_than_two_supported_pas"
    unit_diag$status[unit_diag$n_pas_after>=2L]<-"insufficient_unit_count_in_at_least_one_sample"
    unit_diag$status[unit_diag$unit_id %in% unit_keep]<-"retained"
    write_tsv(unit_diag,"unit_filtering.tsv")
    write_tsv(data.frame(stage=c("analysis_set","pas_coverage","unit_coverage_and_multi_pas"),
        pas=c(nrow(x),sum(pas_keep),sum(pas_reason=="retained")),
        units=c(length(unique(ann$unit_id)),length(unique(kept_ann$unit_id)),length(unit_keep))),"filtering.tsv")
    if (!length(unit_keep)) {
        if(descriptive) { empty_descriptive(s,paste0(mode_reason,"; no_units_after_filtering")); return(invisible(NULL)) }
        stop("No units retain >=2 supported PAS and sufficient counts in every pseudobulk")
    }
    keep <- pas_reason=="retained"; x <- x[keep,,drop=FALSE]; ann <- ann[keep,,drop=FALSE]
    ord <- order(ann$unit_id,ann$feature_id); x <- x[ord,,drop=FALSE]; ann <- ann[ord,,drop=FALSE]
    totals <- rowsum(x,ann$unit_id,reorder=TRUE)
    weighted <- rowsum(x*ann$length_bp,ann$unit_id,reorder=TRUE)/totals
    usage <- x/totals[match(ann$unit_id,rownames(totals)),,drop=FALSE]
    control <- s$group=="control"; case <- s$group=="case"
    summary <- data.frame(unit_id=rownames(totals),gene_id=ann$gene_id[match(rownames(totals),ann$unit_id)],
        strand=ann$strand[match(rownames(totals),ann$unit_id)],n_pas=as.integer(table(ann$unit_id)[rownames(totals)]),n_control=sum(control),n_case=sum(case),
        mean_umi_control=rowMeans(totals[,control,drop=FALSE]),mean_umi_case=rowMeans(totals[,case,drop=FALSE]),
        mean_weighted_length_control=rowMeans(weighted[,control,drop=FALSE]),mean_weighted_length_case=rowMeans(weighted[,case,drop=FALSE]))
    summary$delta_weighted_length_bp <- summary$mean_weighted_length_case-summary$mean_weighted_length_control
    # Endpoint uses the SAME retained PAS denominator, but excludes an entire unit
    # if ANY retained PAS has unknown length. No dropping only inconvenient PAS.
    length_valid <- vapply(summary$unit_id,function(u) all(is.finite(ann$length_bp[ann$unit_id==u])),logical(1))
    positive <- rowSums(is.finite(weighted)&weighted>0)==ncol(weighted)
    summary$length_status <- ifelse(!length_valid,"missing_or_invalid_pas_length",ifelse(!positive,"nonpositive_weighted_length","eligible"))
    summary$descriptive_log2_length_ratio <- NA_real_
    eligible <- summary$length_status=="eligible"
    summary$descriptive_log2_length_ratio[eligible] <- log2(summary$mean_weighted_length_case[eligible]/summary$mean_weighted_length_control[eligible])
    # Identity comes from the FULL NATIVE catalogue, including all ties. Any
    # missing distal member after analysis-set/count filtering masks the endpoint.
    distal <- ann$feature_id %in% unlist(native_distal_ids,use.names=FALSE)
    distal_usage <- rowsum(usage*distal,ann$unit_id,reorder=TRUE)
    distal_status <- vapply(summary$unit_id,function(u) {
        ids <- native_distal_ids[[u]]
        if(!length(ids)) "unavailable_native_distal_identity" else if(!all(ids %in% ann$feature_id)) "distal_filtered" else "complete_native_distal_set"
    },character(1))
    distal_usage[distal_status!="complete_native_distal_set",]<-NA_real_
    summary$analysis_mean_distal_usage_control <- rowMeans(distal_usage[,control,drop=FALSE])
    summary$analysis_mean_distal_usage_case <- rowMeans(distal_usage[,case,drop=FALSE])
    summary$analysis_delta_distal_usage_pp <- 100*(summary$analysis_mean_distal_usage_case-summary$analysis_mean_distal_usage_control)
    summary$analysis_distal_status <- distal_status
    ni <- match(summary$unit_id,native_effects$unit_id)
    summary <- cbind(summary,native_effects[ni,setdiff(names(native_effects),"unit_id"),drop=FALSE])
    pas_effects <- cbind(ann,mean_usage_control=rowMeans(usage[,control,drop=FALSE]),mean_usage_case=rowMeans(usage[,case,drop=FALSE]))
    pas_effects$delta_usage_pp <- 100*(pas_effects$mean_usage_case-pas_effects$mean_usage_control)
    write_tsv(s,"samples_used.tsv"); write_matrix(design,"sample_id","design.tsv")
    write_tsv(ann,"pas_annotation_used.tsv")
    write_tsv(cbind(ann[c("unit_id","feature_id")],as.data.frame(x,check.names=FALSE)),"pas_counts_used.tsv")
    write_matrix(totals,"unit_id","retained_unit_counts.tsv"); write_matrix(weighted,"unit_id","weighted_length.tsv")
    write_matrix(usage,"feature_id","pas_usage.tsv"); write_matrix(distal_usage,"unit_id","analysis_distal_usage.tsv")
    write_tsv(pas_effects,"pas_effects.tsv"); write_tsv(summary,"unit_effects.tsv")
    writeLines(capture.output(dput(a)),file.path(a$outdir,"arguments.R.txt"))
    set.seed(as.integer(a$seed))
    validate_p <- function(p, label) {
        if (any(!is.finite(p)) || any(p<0 | p>1)) stop(label,": unavailable/nonfinite/out-of-range tests; execution cannot be COMPLETE")
    }
    if ("edger" %in% methods) {
        stage <<- "edger"
        y <- edgeR::DGEList(counts=x,genes=ann[c("unit_id","feature_id")])
        y <- edgeR::calcNormFactors(y,method="TMM")
        fit <- edgeR::glmQLFit(y,design,robust=TRUE,legacy=FALSE)
        ds <- limma::diffSplice(fit,coef=coef_name,geneid="unit_id",exonid="feature_id")
        eg <- limma::topSplice(ds,test="F",number=Inf,sort.by="none")
        if (!all(c("unit_id","P.Value") %in% names(eg)) || nrow(eg)!=nrow(totals) || anyDuplicated(eg$unit_id) || !setequal(eg$unit_id,rownames(totals))) stop("Unexpected edgeR unit result schema/coverage")
        validate_p(eg$P.Value,"edgeR")
        eg$FDR <- p.adjust(eg$P.Value,"BH")
        write_tsv(eg,"edger_units.tsv")
        # PAS-level effects are descriptive; no PAS hypothesis family is silently added.
        write_tsv(data.frame(sample_id=rownames(y$samples),y$samples,check.names=FALSE),"edger_normalization.tsv")
        writeLines("edgeR glmQLFit(robust=TRUE, legacy=FALSE); limma generic dispatch diffSplice.DGEGLM; topSplice(test=F); BH across all retained units",file.path(a$outdir,"edger_engine.txt"))
        idx <- match(summary$unit_id,eg$unit_id); summary$edger_pvalue<-eg$P.Value[idx]; summary$edger_FDR<-eg$FDR[idx]
    }
    if ("limma" %in% methods) {
        stage <<- "limma_length"
        res <- data.frame(unit_id=summary$unit_id,log2_length_ratio=NA_real_,P.Value=NA_real_,FDR=NA_real_,status=summary$length_status)
        z <- log2(weighted[eligible,,drop=FALSE])
        variable <- if(nrow(z)) apply(z,1L,function(v) diff(range(v))>1e-10) else logical()
        res$status[eligible]<-"constant_length"
        idxv <- match(rownames(z)[variable],res$unit_id); res$status[idxv]<-"insufficient_units_for_moderation"
        if (sum(variable)>=2L) {
            fit <- limma::lmFit(z[variable,,drop=FALSE],design)
            estimable <- is.finite(fit$sigma)&fit$sigma>1e-8
            res$status[idxv[!estimable]]<-"zero_residual_variance"
            if (sum(estimable)>=2L) {
                fit <- limma::eBayes(fit[estimable,],robust=TRUE)
                tt <- limma::topTable(fit,coef=coef_name,number=Inf,sort.by="none")
                validate_p(tt$P.Value,"limma")
                ix <- match(rownames(tt),res$unit_id)
                res$log2_length_ratio[ix]<-tt$logFC; res$P.Value[ix]<-tt$P.Value
                res$FDR[ix]<-p.adjust(tt$P.Value,"BH"); res$status[ix]<-"tested"
            }
        }
        untestable <<- any(res$status!="tested")
        write_tsv(res,"limma_length_units.tsv")
        summary$limma_length_log2_ratio<-res$log2_length_ratio; summary$limma_length_pvalue<-res$P.Value
        summary$limma_length_FDR<-res$FDR; summary$limma_length_status<-res$status
        if (!any(res$status=="tested")) warning("No estimable limma length tests; see explicit endpoint status")
    }
    if ("drimseq" %in% methods) {
        stage <<- "drimseq_dmPrecision"
        # DRIMSeq calls these gene_id, but values remain gene+strand unit IDs.
        da <- data.frame(gene_id=ann$unit_id,feature_id=ann$feature_id,check.names=FALSE)
        d <- DRIMSeq::dmDSdata(counts=cbind(da,as.data.frame(x,check.names=FALSE)),samples=s)
        bp <- BiocParallel::SerialParam()
        d <- DRIMSeq::dmPrecision(d,design=design,prec_subset=1,BPPARAM=bp)
        precision <- DRIMSeq::genewise_precision(d); write_tsv(precision,"drimseq_precision.tsv")
        if (nrow(precision)!=nrow(totals) || !setequal(precision$gene_id,rownames(totals)) ||
            any(!is.finite(precision$genewise_precision)) || any(precision$genewise_precision<=0)) stop("DRIMSeq missing/invalid precision")
        stage <<- "drimseq_dmFit"; d <- DRIMSeq::dmFit(d,design=design,bb_model=FALSE,BPPARAM=bp)
        # Public fitted proportions plus documented likelihood slots are retained
        # for diagnosis. The package does not expose a general convergence flag.
        fitted <- DRIMSeq::proportions(d); write_tsv(fitted,"drimseq_fitted_proportions.tsv")
        fp <- as.matrix(fitted[,s$sample_id,drop=FALSE]); storage.mode(fp)<-"double"
        if(any(!is.finite(fp)) || any(fp<0 | fp>1)) stop("DRIMSeq invalid fitted proportions")
        full_lik <- methods::slot(d,"lik_full")
        if(length(full_lik)!=nrow(totals) || any(!is.finite(full_lik))) stop("DRIMSeq invalid full-model likelihood")
        stage <<- "drimseq_dmTest"; d <- DRIMSeq::dmTest(d,coef=coef_name,BPPARAM=bp)
        null_lik <- methods::slot(d,"lik_null")
        write_tsv(data.frame(unit_id=names(full_lik),full_log_likelihood=as.numeric(full_lik),null_log_likelihood=as.numeric(null_lik)),"drimseq_likelihoods.tsv")
        if(length(null_lik)!=nrow(totals) || any(!is.finite(null_lik))) stop("DRIMSeq invalid null-model likelihood")
        dg <- DRIMSeq::results(d,level="gene"); names(dg)[names(dg)=="gene_id"]<-"unit_id"
        write_tsv(dg,"drimseq_units_raw.tsv")
        if(nrow(dg)!=nrow(totals) || anyDuplicated(dg$unit_id) || !setequal(dg$unit_id,rownames(totals))) stop("DRIMSeq missing unit results")
        validate_p(dg$pvalue,"DRIMSeq")
        dg$FDR<-p.adjust(dg$pvalue,"BH"); write_tsv(dg,"drimseq_units.tsv")
        ix <- match(summary$unit_id,dg$unit_id); summary$drimseq_pvalue<-dg$pvalue[ix]; summary$drimseq_FDR<-dg$FDR[ix]
        writeLines("Sensitivity endpoint. Finite precision and p-values do not establish optimizer convergence; inspect warnings and fitting diagnostics. No union with primary FDR.",file.path(a$outdir,"drimseq_limits.txt"))
    }
    write_tsv(summary,"differential_apa_summary.tsv")
    if(descriptive) writeLines(mode_reason,file.path(a$outdir,"descriptive_reason.txt"))
}
error <- NULL
tryCatch(withCallingHandlers(run(),warning=function(w) {
    warnlog[[length(warnlog)+1L]] <<- data.frame(stage=stage,warning=gsub("[\t\r\n]"," ",conditionMessage(w)))
    invokeRestart("muffleWarning")
}),error=function(e) error <<- conditionMessage(e))
write_tsv(if(length(warnlog)) do.call(rbind,warnlog) else data.frame(stage=character(),warning=character()),"warnings.tsv")
writeLines(capture.output(sessionInfo()),file.path(a$outdir,"sessionInfo.txt"))
status <- if(!is.null(error)) "FAILED" else if(mode=="descriptive") "DESCRIPTIVE_ONLY" else if(length(warnlog)) "COMPLETE_WITH_WARNINGS" else if(untestable) "COMPLETE_WITH_UNTESTABLE_LENGTH_UNITS" else "COMPLETE"
write_tsv(data.frame(status=status,mode=mode,reason=mode_reason,warning_count=length(warnlog),untestable_length_units=untestable,error=if(is.null(error)) "" else gsub("[\t\r\n]"," ",error)),"backend_status.tsv")
if (!is.null(error)) {message(error);quit(status=1L)}
message("Finished: ",status,"; contrast ",a$case," - ",a$control)
