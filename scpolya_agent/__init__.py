"""Read-only, versioned scPolyA run-context inspection.

No execution, recommendation, scientific analysis or report-export API is
provided. See README.md for the evidence boundaries and resource limits.
"""

from .inspection import INSPECTION_SCHEMA, inspect_workspace
from .bounds import encode_inspection

__all__ = ["INSPECTION_SCHEMA", "inspect_workspace", "encode_inspection"]
