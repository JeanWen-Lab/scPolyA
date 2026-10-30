"""Workspace overview: header, recent runs, attention and workspace registry."""

from __future__ import annotations


OVERVIEW_CSS = """
.product-page-workspaces .product-page-heading {
  display: none;
}

.product-page-content.product-page-content-workspaces {
  padding: 0;
  overflow-y: auto;
  background: var(--sp-bg);
}

.ov-page {
  width: 100%;
  max-width: 1360px;
  padding: 26px 34px 56px;
}

/* ---------- header ---------- */
.ov-header {
  align-items: flex-start;
  gap: 16px;
  flex-wrap: nowrap;
}

.ov-heading {
  flex: 1 1 auto;
  min-width: 0;
}

.ov-eyebrow {
  margin-bottom: 3px;
  color: var(--sp-subtle);
  font-size: 11.5px;
}

.ov-title-row {
  align-items: center;
  gap: 12px;
  min-width: 0;
  flex-wrap: nowrap;
}

.ov-title {
  min-width: 0;
  overflow: hidden;
  color: var(--sp-ink-strong);
  font-family: var(--sp-font-serif);
  font-size: 26px;
  font-weight: 600;
  letter-spacing: -0.2px;
  line-height: 1.25;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ov-path {
  margin-top: 3px;
  overflow: hidden;
  color: var(--sp-subtle);
  font-family: var(--sp-font-mono);
  font-size: 11.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ov-actions {
  flex: none;
  gap: 8px;
  padding-top: 16px;
  flex-wrap: nowrap;
}

.ov-action {
  background: var(--sp-surface) !important;
}

.ov-actions .q-icon {
  font-size: 17px;
}

.ov-chips {
  align-items: center;
  gap: 6px;
  margin: 12px 0 22px;
  flex-wrap: wrap;
}

.ov-chip {
  padding: 2px 9px;
  border-radius: 6px;
  background: var(--sp-surface-muted);
  color: var(--sp-muted);
  font-size: 11.5px;
  white-space: nowrap;
}

.ov-chip-warn {
  background: var(--sp-warning-soft);
  color: var(--sp-warning);
}

.ov-chip-run {
  background: var(--sp-info-soft);
  color: var(--sp-info);
}

/* ---------- grid & cards ---------- */
.ov-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.6fr) minmax(300px, 1fr);
  gap: 16px;
  align-items: start;
}

.ov-col {
  min-width: 0;
  gap: 16px !important;
}

.ov-card {
  border: 1px solid var(--sp-border);
  border-radius: 12px;
  background: var(--sp-surface);
  /* clip, not hidden: keeps rounded corners without breaking sticky table headers */
  overflow: clip;
}

.ov-card-head {
  align-items: center;
  gap: 8px;
  min-height: 46px;
  padding: 8px 10px 8px 16px;
  border-bottom: 1px solid var(--sp-divider);
  flex-wrap: nowrap;
}

.ov-card-title {
  color: var(--sp-ink-strong);
  font-size: 13px;
  font-weight: 600;
}

.ov-card-count {
  color: var(--sp-subtle);
  font-size: 11.5px;
}

.ov-spacer {
  flex: 1 1 auto;
}

.ov-card-body {
  padding: 12px 16px 14px;
}

.ov-link-button {
  min-height: 28px !important;
  padding: 0 8px !important;
  color: var(--sp-muted) !important;
  font-size: 12px !important;
}

.ov-link-button .q-icon {
  font-size: 15px;
}

.ov-icon-button {
  color: var(--sp-subtle) !important;
}

.ov-more {
  align-self: flex-start;
  margin: 4px 8px 8px;
  color: var(--sp-muted) !important;
  font-size: 12px !important;
}

/* ---------- tables ---------- */
.ov-table {
  display: flex;
  flex-direction: column;
}

.ov-tr {
  display: grid;
  align-items: center;
  gap: 12px;
  min-height: 42px;
  padding: 6px 16px;
  border-bottom: 1px solid var(--sp-divider);
  font-size: 12.5px;
}

.ov-tr:last-child {
  border-bottom: 0;
}

.ov-runs-table .ov-tr {
  grid-template-columns: minmax(0, 1.6fr) 104px minmax(0, 1fr) 64px 96px;
}

.ov-ws-table .ov-tr {
  grid-template-columns: minmax(0, 2.2fr) 48px 48px 130px 96px;
}

.ov-th {
  position: sticky;
  top: var(--sp-sticky-offset, 0px);
  z-index: 2;
  min-height: 32px;
  padding-top: 4px;
  padding-bottom: 4px;
  background: var(--sp-surface-subtle);
  color: var(--sp-subtle);
  font-size: 11.5px;
}

.ov-tr-link {
  cursor: pointer;
}

.ov-tr-link:hover {
  background: var(--sp-control-hover);
}

.ov-tr-active {
  box-shadow: inset 2px 0 0 var(--sp-ink);
  background: var(--sp-surface-subtle);
}

.ov-td {
  min-width: 0;
  overflow: hidden;
  color: var(--sp-muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ov-td-run {
  align-items: baseline;
  gap: 8px;
  flex-wrap: nowrap;
}

.ov-run-name {
  min-width: 0;
  overflow: hidden;
  color: var(--sp-ink-strong);
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ov-run-attempts {
  flex: none;
  color: var(--sp-subtle);
  font-size: 11px;
}

.ov-td-artifacts,
.ov-td-runs,
.ov-td-studies {
  font-family: var(--sp-font-mono);
  font-size: 11.5px;
  text-align: right;
}

.ov-th .ov-td-artifacts,
.ov-th .ov-td-runs,
.ov-th .ov-td-studies {
  font-family: inherit;
}

.ov-td-started,
.ov-td-last_opened {
  font-size: 11.5px;
}

.ov-mono {
  font-family: var(--sp-font-mono);
  font-size: 11px;
}

.ov-td-workspace > * {
  max-width: 100%;
}

.ov-ws-path {
  overflow: hidden;
  color: var(--sp-subtle);
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ---------- attention ---------- */
.ov-alert {
  padding: 9px 12px;
  border: 1px solid;
  border-radius: 9px;
  flex-wrap: nowrap;
}

.ov-alert-warn {
  border-color: var(--sp-warning-line);
  background: var(--sp-warning-soft);
  color: var(--sp-warning-ink);
}

.ov-alert-info {
  border-color: var(--sp-border);
  background: var(--sp-surface-muted);
  color: var(--sp-ink);
}

.ov-alert-crit {
  border-color: var(--sp-danger-line);
  background: var(--sp-danger-soft);
  color: var(--sp-danger-ink);
}

.ov-alert-title {
  font-size: 12.5px;
  font-weight: 600;
}

.ov-alert-detail {
  font-size: 12px;
  line-height: 1.5;
  overflow-wrap: anywhere;
  opacity: 0.9;
}

.ov-alert-link {
  align-self: flex-start;
  min-height: 22px !important;
  margin-left: -6px;
  padding: 0 6px !important;
  color: inherit !important;
  font-size: 12px !important;
  text-decoration: underline;
}

.ov-quiet {
  align-items: center;
  gap: 8px;
  color: var(--sp-muted);
  font-size: 12.5px;
  flex-wrap: nowrap;
}

.ov-quiet-dot > div {
  display: none;
}

/* ---------- get started ---------- */
.ov-steps {
  padding-top: 6px;
  padding-bottom: 8px;
}

.ov-step {
  align-items: center;
  gap: 9px;
  min-height: 32px;
  font-size: 12.5px;
  flex-wrap: nowrap;
}

.ov-step-icon {
  color: var(--sp-disabled);
  font-size: 17px;
}

.ov-step-done .ov-step-icon {
  color: var(--sp-good-dot);
}

.ov-step-label {
  flex: 1 1 auto;
  color: var(--sp-ink);
}

.ov-step-done .ov-step-label {
  color: var(--sp-subtle);
}

.ov-step-go {
  min-height: 24px !important;
  padding: 0 8px !important;
  color: var(--sp-info) !important;
  font-size: 12px !important;
}

/* ---------- details ---------- */
.ov-detail {
  align-items: center;
  gap: 10px;
  min-height: 32px;
  padding: 4px 0;
  border-bottom: 1px solid var(--sp-divider);
  font-size: 12.5px;
  flex-wrap: nowrap;
}

.ov-detail:last-child {
  border-bottom: 0;
}

.ov-detail-key {
  flex: 0 0 116px;
  color: var(--sp-subtle);
}

.ov-detail-value {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  color: var(--sp-ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ov-copy {
  flex: none;
  color: var(--sp-subtle) !important;
}

/* ---------- welcome ---------- */
.ov-welcome {
  max-width: 980px;
  padding-top: 8px;
}

.ov-welcome .ov-title {
  font-size: 30px;
}

.ov-welcome-text {
  max-width: 720px;
  margin: 6px 0 20px;
  color: var(--sp-muted);
  font-size: 13.5px;
  line-height: 1.6;
}

.ov-card .ov-welcome-text {
  margin: 0;
}

.ov-moved-map {
  padding: 8px 12px;
  border: 1px solid var(--sp-divider);
  border-radius: 8px;
  background: var(--sp-surface-subtle);
}

.ov-moved-path {
  overflow-wrap: anywhere;
  color: var(--sp-ink);
  margin-bottom: 4px;
}

.ov-untracked-actions {
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.ov-step-cards {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.ov-step-card {
  padding: 18px 18px 16px;
  border: 1px solid var(--sp-border);
  border-radius: 12px;
  background: var(--sp-surface);
}

.ov-card .ov-step-card {
  background: var(--sp-surface-subtle);
}

.ov-step-number {
  color: var(--sp-disabled);
  font-family: var(--sp-font-serif);
  font-size: 26px;
  line-height: 1.1;
}

.ov-step-title {
  margin: 6px 0 4px;
  color: var(--sp-ink-strong);
  font-size: 13.5px;
  font-weight: 600;
}

.ov-step-detail {
  flex: 1 1 auto;
  min-height: 40px;
  color: var(--sp-muted);
  font-size: 12.5px;
  line-height: 1.6;
}

.ov-step-action {
  align-self: flex-start;
  margin-top: 14px;
}

.ov-step-action.q-btn--outline {
  background: var(--sp-surface) !important;
}

/* ---------- mapping dialog ---------- */
.ov-mapping-dialog {
  width: 640px;
  max-width: 92vw;
}

.ov-mapping-dialog .product-confirm-heading {
  align-items: flex-start;
  flex-wrap: nowrap;
}

.ov-mapping-list {
  border: 1px solid var(--sp-border);
  border-radius: 9px;
}

.ov-mapping-row {
  padding: 8px 12px;
  border-bottom: 1px solid var(--sp-divider);
}

.ov-mapping-row:last-child {
  border-bottom: 0;
}

.ov-mapping-paths {
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  overflow-wrap: anywhere;
}

.ov-mapping-arrow {
  color: var(--sp-subtle);
  font-size: 14px;
}

.ov-mapping-ignored {
  color: var(--sp-warning-ink);
  font-size: 12px;
}

.ov-muted-small {
  color: var(--sp-subtle);
  font-size: 12px;
}

.ov-mapping-form {
  display: grid;
  gap: 8px;
  margin-top: 4px;
}

.ov-index-missing {
  flex-wrap: nowrap;
  gap: 10px;
  min-width: 0;
  padding: 4px 0;
  border-top: 1px solid var(--sp-border);
}

.ov-index-label {
  flex: none;
  max-width: 45%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ov-index-path {
  min-width: 0;
  overflow: hidden;
  color: var(--sp-subtle);
  text-overflow: ellipsis;
  white-space: nowrap;
  direction: rtl;
  text-align: left;
}
"""
