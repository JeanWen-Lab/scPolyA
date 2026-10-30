"""Composable CSS modules for the product workbench."""

from __future__ import annotations

from .components import COMPONENTS_CSS
from .foundation import FOUNDATION_CSS
from .differential import DIFFERENTIAL_CSS
from .environment import ENVIRONMENT_CSS
from .genome import GENOME_CSS
from .notes import NOTES_CSS
from .onboarding import ONBOARDING_CSS
from .operations import OPERATIONS_CSS
from .overview import OVERVIEW_CSS
from .responsive import RESPONSIVE_CSS
from .run_setup import RUN_SETUP_CSS
from .runs import RUNS_CSS
from .shell import SHELL_CSS
from .site_scores import SITE_SCORES_CSS
from .studies import STUDIES_CSS
from .studies_v2 import STUDIES_V2_CSS
from .tokens import TOKENS_CSS
from .workbenches import WORKBENCHES_CSS


PRODUCT_CSS = "\n".join(
    (
        "<style>",
        TOKENS_CSS,
        FOUNDATION_CSS,
        SHELL_CSS,
        WORKBENCHES_CSS,
        STUDIES_CSS,
        GENOME_CSS,
        OPERATIONS_CSS,
        RESPONSIVE_CSS,
        COMPONENTS_CSS,
        RUNS_CSS,
        RUN_SETUP_CSS,
        OVERVIEW_CSS,
        STUDIES_V2_CSS,
        ENVIRONMENT_CSS,
        SITE_SCORES_CSS,
        DIFFERENTIAL_CSS,
        ONBOARDING_CSS,
        NOTES_CSS,
        "</style>",
    )
)


__all__ = ["PRODUCT_CSS"]
