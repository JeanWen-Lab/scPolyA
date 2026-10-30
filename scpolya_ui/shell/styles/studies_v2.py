"""Parameter studies: study cards, parameter panel and variant matrix."""

from __future__ import annotations


STUDIES_V2_CSS = """
.product-page-parameter_studies .product-page-heading {
  display: none;
}

.product-page-content.product-page-content-parameter_studies {
  padding: 0;
  overflow-y: auto;
  background: var(--sp-bg);
}

.st-page {
  width: 100%;
  max-width: 1360px;
  padding: 26px 34px 56px;
}

.st-header {
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 20px;
  flex-wrap: nowrap;
}

.st-heading {
  flex: 1 1 auto;
  min-width: 0;
}

.st-title {
  color: var(--sp-ink-strong);
  font-family: var(--sp-font-serif);
  font-size: 26px;
  font-weight: 600;
  letter-spacing: -0.2px;
  line-height: 1.25;
}

.st-subtitle {
  margin-top: 4px;
  color: var(--sp-muted);
  font-size: var(--sp-font-body);
}

.st-header-actions {
  flex: none;
  align-items: center;
  gap: 6px;
  padding-top: 4px;
  flex-wrap: nowrap;
}

.st-icon-button {
  color: var(--sp-subtle) !important;
}

.st-muted {
  color: var(--sp-subtle);
  font-size: 12.5px;
}

.st-empty {
  padding: 40px 0;
}

.st-empty-title {
  font-family: var(--sp-font-serif);
  font-size: 16px;
  font-weight: 600;
}

.st-spacer {
  flex: 1 1 auto;
}

/* ---------- study cards ---------- */
.st-cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 10px;
  margin-bottom: 26px;
}

.st-card {
  min-width: 0;
  padding: 12px 14px;
  border: 1px solid var(--sp-border);
  border-radius: 11px;
  background: var(--sp-surface);
  cursor: pointer;
}

.st-card:hover {
  border-color: var(--sp-border-strong);
}

.st-card-active,
.st-card-active:hover {
  border-color: var(--sp-ink);
  box-shadow: 0 0 0 0.5px var(--sp-ink);
  cursor: default;
}

.st-card-top {
  align-items: center;
  gap: 8px;
  flex-wrap: nowrap;
}

.st-card-name {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  color: var(--sp-ink-strong);
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.st-card-status {
  flex: none;
}

.st-card-meta {
  margin-top: 4px;
  color: var(--sp-subtle);
  font-size: 11.5px;
}

/* ---------- builder layout ---------- */
.st-layout {
  display: grid;
  grid-template-columns: 360px minmax(0, 1fr);
  gap: 28px;
  align-items: start;
}

.st-side,
.st-main {
  min-width: 0;
}

.st-section-title {
  margin-bottom: 8px;
  color: var(--sp-muted);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.02em;
}

.st-section-row {
  align-items: baseline;
  gap: 8px;
  flex-wrap: nowrap;
}

.st-section-count {
  color: var(--sp-subtle);
  font-family: var(--sp-font-mono);
  font-weight: 400;
}

.st-section-spaced {
  margin-top: 22px;
}

.st-panel {
  padding: 14px 16px 12px;
  border: 1px solid var(--sp-border);
  border-radius: 12px;
  background: var(--sp-surface);
}

.st-field {
  margin-bottom: 12px;
}

.st-field-label {
  color: var(--sp-muted);
  font-size: 12px;
}

.st-params {
  margin-top: 4px;
  border-top: 1px solid var(--sp-divider);
}

.st-param {
  padding: 10px 0;
  border-bottom: 1px solid var(--sp-divider);
}

.st-param-off {
  opacity: 0.55;
}

.st-param-top {
  align-items: center;
  gap: 6px;
  flex-wrap: nowrap;
}

.st-param-key {
  flex: 1 1 auto;
  min-width: 0;
}

.st-param-key .q-field__control,
.st-param-key .q-field__native {
  min-height: 28px !important;
  height: 28px;
  padding: 0;
}

.st-param-key .q-field__native > span {
  overflow: hidden;
  color: var(--sp-ink-strong);
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.st-axis-chip {
  flex: none;
  padding: 1px 7px;
  border-radius: 5px;
  background: var(--sp-surface-muted);
  color: var(--sp-muted);
  font-size: 11px;
}

.st-param-remove {
  flex: none;
  color: var(--sp-subtle) !important;
}

.st-param-inactive {
  color: var(--sp-warning-ink);
  font-size: 11.5px;
}

.st-param-code {
  margin: -4px 0 2px 30px;
  color: var(--sp-subtle);
  font-family: var(--sp-font-mono);
  font-size: 10.5px;
}

.st-param-values .q-field__native {
  font-family: var(--sp-font-mono);
  font-size: 12px;
}

.st-add-param {
  align-self: flex-start;
  margin-top: 8px;
  color: var(--sp-muted) !important;
}

.st-hint {
  margin-top: 10px;
  color: var(--sp-subtle);
  font-size: 11.5px;
  line-height: 1.5;
}

.st-hint-right {
  align-self: flex-end;
  text-align: right;
}

/* ---------- matrix ---------- */
.st-matrix {
  display: grid;
  gap: 6px;
  width: 100%;
}

.st-matrix-corner {
  justify-content: flex-end;
  padding: 0 4px 4px 0;
  color: var(--sp-subtle);
  font-size: 11px;
  line-height: 1.35;
}

.st-col-axis {
  align-items: baseline;
  justify-content: center;
  gap: 8px;
  min-width: 0;
  padding: 0 4px 2px;
  border-bottom: 1px solid var(--sp-divider);
  flex-wrap: nowrap;
}

.st-axis-name {
  min-width: 0;
  color: var(--sp-muted);
  font-size: 11.5px;
  font-weight: 500;
  line-height: 1.35;
  overflow-wrap: anywhere;
}

.st-axis-key {
  min-width: 0;
  overflow: hidden;
  color: var(--sp-subtle);
  font-family: var(--sp-font-mono);
  font-size: 10.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.st-matrix-corner .st-axis-key {
  max-width: 100%;
}

.st-matrix-head {
  align-items: flex-end;
  min-width: 0;
  padding: 0 4px 4px;
  color: var(--sp-muted);
  font-size: 11.5px;
}

.st-matrix-head-key {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.st-matrix-axis {
  align-items: center;
  justify-content: flex-end;
  min-width: 0;
  padding-right: 8px;
}

.st-axis-value {
  color: var(--sp-ink);
  font-family: var(--sp-font-mono);
  font-size: 12px;
  font-weight: 500;
}

.st-cell {
  min-width: 0;
  min-height: 62px;
  padding: 9px 11px;
  border: 1px solid var(--sp-border);
  border-radius: 9px;
  background: var(--sp-surface);
}

.st-cell-work {
  border-color: var(--sp-border-strong);
}

.st-cell-reuse {
  border-style: dashed;
  background: var(--sp-surface-subtle);
}

.st-cell-link {
  cursor: pointer;
}

.st-cell-link:hover {
  border-color: var(--sp-ink);
}

.st-cell-top {
  align-items: center;
  gap: 6px;
  min-height: 20px;
  flex-wrap: nowrap;
}

.st-cell-id {
  flex: 1 1 auto;
  color: var(--sp-ink-strong);
  font-family: var(--sp-font-mono);
  font-size: 12px;
  font-weight: 600;
}

.st-cell-status {
  flex: none;
  font-size: 11.5px;
}

.st-cell-button {
  color: var(--sp-subtle) !important;
}

.st-cell-detail {
  margin-top: 4px;
  color: var(--sp-muted);
  font-size: 11.5px;
  line-height: 1.4;
}

.st-cell-reuse .st-cell-detail {
  color: var(--sp-subtle);
}

/* ---------- actions & callouts ---------- */
.st-actions {
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 18px;
  flex-wrap: wrap;
}

.st-actions .st-preview-note {
  flex: 1 1 220px;
}

.st-preview-note {
  color: var(--sp-subtle);
  font-size: 12px;
}

.st-preview-note-stale {
  color: var(--sp-warning);
}

.st-button {
  background: var(--sp-surface) !important;
}

.st-actions .q-icon,
.st-detail-actions .q-icon,
.st-header-actions .q-icon {
  font-size: 17px;
}

.st-callout {
  align-items: flex-start;
  gap: 8px;
  margin-top: 12px;
  padding: 9px 12px;
  border: 1px solid;
  border-radius: 9px;
  font-size: 12.5px;
  line-height: 1.5;
  flex-wrap: nowrap;
}

.st-callout .q-icon {
  margin-top: 1px;
  font-size: 17px;
}

.st-callout-crit {
  border-color: var(--sp-danger-line);
  background: var(--sp-danger-soft);
  color: var(--sp-danger-ink);
  overflow-wrap: anywhere;
}

.st-callout-info {
  border-color: var(--sp-info-line);
  background: var(--sp-info-soft);
  color: var(--sp-ink);
}

/* ---------- saved detail ---------- */
.st-detail-head {
  align-items: flex-start;
  gap: 16px;
  flex-wrap: nowrap;
}

.st-detail-title-row {
  align-items: center;
  gap: 12px;
  flex-wrap: nowrap;
}

.st-detail-title {
  overflow: hidden;
  color: var(--sp-ink-strong);
  font-family: var(--sp-font-serif);
  font-size: 20px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.st-detail-chips {
  align-items: center;
  gap: 6px;
  margin-top: 8px;
  flex-wrap: wrap;
}

.st-chip {
  padding: 2px 8px;
  border-radius: 6px;
  background: var(--sp-surface-muted);
  color: var(--sp-muted);
  font-size: 11.5px;
  white-space: nowrap;
}

.st-chip-mono {
  font-family: var(--sp-font-mono);
  font-size: 11px;
}

.st-detail-actions {
  flex: none;
  gap: 8px;
  padding-top: 2px;
  flex-wrap: nowrap;
}

.st-command-dialog {
  width: 860px;
  max-width: 92vw;
}
"""
