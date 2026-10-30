from logging_config._logging_config import (
    get_logger,
    setup_main_logger,
    setup_module_logger,
    stage_scope, task_scope,
    suffix_scope, invocation_scope, identity_scope,
    log_prefix,
    ScPolyALogger,
    LoggersWrapper,
    C_PINK, C_GREEN, C_RESET, C_RED,
    CURRENT_STAGE, CURRENT_TASK, CURRENT_SUFFIX,
    CURRENT_INVOCATION_ID, CURRENT_IDENTITY,
)

from logging_config.runtime_session import (
    CURRENT_INVOCATION_METADATA_SCHEMA,
    INVOCATION_METADATA_SCHEMA_V1,
    INVOCATION_METADATA_SCHEMA_V2,
    RunLoggingSession,
    get_active_logging_session,
    setup_run_logging,
)

from logging_config.tool_execution import (
    TOOL_EXECUTION_SCHEMA_V1,
    ToolExecution,
    check_tool_output,
    run_tool_command,
)

from logging_config.define import (
    LOG_MAIN,
    LOG_UTILS,
    LOG_TASK,
    LOG_NC,
    LOG_STAGE_init_references,
    LOG_STAGE_register_inputs,
    LOG_STAGE_preprocess_units,
    LOG_STAGE_extract_subsets,
    LOG_STAGE_calculate_umi,
    LOG_STAGE_merge_units,
    LOG_STAGE_call_peaks,
    LOG_STAGE_annotate_peaks,
    LOG_STAGE_annotate_bams,
    LOG_STAGE_count_matrix,
    LOG_STAGE_summarize_matrix,
    LOG_STAGE_rna_gene_matrix,

    LOG_STAGE_bulk_references
)

from logging_config._debug import fmt_kwargs
