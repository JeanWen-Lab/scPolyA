"""New run page: single-scroll form, section index, sticky change bar, review dialog."""

from __future__ import annotations


RUN_SETUP_CSS = """
.product-page-content.product-page-content-run_setup {
  padding: 0;
  overflow-y: auto;
  background: var(--sp-bg);
}

.rs-page {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  padding: 0 28px;
}

.rs-toolbar {
  align-items: center;
  gap: 10px;
  padding: 2px 0 16px;
  flex-wrap: nowrap;
}

.rs-base {
  align-items: center;
  gap: 8px;
  flex-wrap: nowrap;
}

.rs-inline-label {
  color: var(--sp-subtle);
  font-size: 12px;
  white-space: nowrap;
}

.rs-base-select {
  width: 300px;
}

.rs-note {
  width: 300px;
}

.rs-base-select .q-field__control,
.rs-note .q-field__control {
  height: 34px;
  min-height: 34px !important;
  background: var(--sp-surface);
}

.rs-view-toggle {
  border: 1px solid var(--sp-border);
  border-radius: 8px;
  overflow: hidden;
  background: var(--sp-surface);
}

.rs-view-toggle .q-btn {
  min-height: 32px !important;
  padding: 0 12px !important;
  border-radius: 0 !important;
  color: var(--sp-muted) !important;
  background: transparent !important;
  font-size: 12.5px !important;
}

.rs-view-toggle .q-btn[aria-pressed="true"] {
  color: var(--sp-surface) !important;
  background: var(--sp-ink) !important;
}

.rs-layout {
  display: grid;
  grid-template-columns: 170px minmax(0, 1fr);
  gap: 30px;
  align-items: start;
  max-width: 1180px;
}

.rs-index {
  position: sticky;
  top: 12px;
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding-top: 6px;
}

.rs-index-link {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border-radius: 6px;
  color: var(--sp-muted) !important;
  font-size: 12.5px;
  text-decoration: none !important;
}

.rs-index-link:hover {
  background: var(--sp-control-hover);
  color: var(--sp-ink);
}

.rs-index-changed,
.rs-index-missing {
  margin-left: auto;
  font-size: 11px;
  white-space: nowrap;
}

.rs-index-changed { color: var(--sp-info); }
.rs-index-missing { color: var(--sp-danger); }

.rs-sections {
  align-items: stretch !important;
  min-width: 0;
  padding-bottom: 24px;
}

.rs-section {
  width: 100%;
  margin-bottom: 22px;
  scroll-margin-top: 12px;
}

.rs-section-head {
  align-items: baseline;
  gap: 10px;
  margin-bottom: 8px;
}

.rs-section-title {
  color: var(--sp-ink-strong);
  font-family: var(--sp-font-serif);
  font-size: 16px;
  font-weight: 600;
}

.rs-section-more {
  color: var(--sp-subtle);
  font-size: 11.5px;
}

.rs-card {
  padding: 4px 18px;
  border: 1px solid var(--sp-border);
  border-radius: 12px;
  background: var(--sp-surface);
}

.rs-json-editor textarea {
  height: min(520px, calc(100vh - 300px)) !important;
  font-family: var(--sp-font-mono);
  font-size: 12px;
  line-height: 1.5;
}

.rs-card-collapsed {
  align-items: center;
  gap: 8px;
  padding: 10px 18px;
}

.rs-card .structured-form {
  display: block;
  border-top: 0;
}

.rs-card .form-section {
  padding: 0;
  border-bottom: 0;
}

.rs-card .form-section-title {
  display: none;
}

.rs-card .form-grid-workbench,
.rs-card .runtime-control-grid {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 0;
  max-width: none;
}

.rs-card .form-control {
  grid-template-columns: 220px minmax(0, 1fr);
  align-items: start;
  gap: 16px;
  padding: 10px 0;
  border-bottom: 1px solid var(--sp-divider);
}

.rs-card .form-control:last-child {
  border-bottom: 0;
}

.rs-card .form-control-label-stack {
  padding-top: 6px;
}

.form-control-label-row {
  align-items: center;
  gap: 5px;
  flex-wrap: nowrap;
  min-width: 0;
}

.rs-card .form-control-label {
  color: var(--sp-ink);
  font-weight: 400;
  white-space: normal;
}

.form-control-help {
  flex: none;
  color: var(--sp-subtle);
  font-size: 15px;
  cursor: help;
}

.form-control-help:hover,
.form-control-help:focus-visible {
  color: var(--sp-info);
}

.form-control-help:focus-visible {
  outline: 0;
  border-radius: 50%;
  box-shadow: var(--sp-focus-ring);
}

.rs-card .form-control-technical-key {
  white-space: normal;
  overflow-wrap: anywhere;
}

.rs-card .form-control-value .q-field__control {
  min-height: 34px;
}

.rs-card .runtime-control-groups {
  gap: 0;
}

.rs-card .runtime-control-group {
  padding: 10px 0 2px;
  border: 0;
  background: transparent;
}

.rs-footnote {
  color: var(--sp-subtle);
  font-size: 12px;
}

.rs-empty {
  padding: 40px 0;
}

.rs-page .run-setup-state-diagnostics {
  margin: 0 0 16px;
  border: 1px solid var(--sp-warning-line);
  border-radius: 10px;
  max-width: 1180px;
}

/* ---------- sticky change bar ---------- */
.rs-bar {
  position: sticky;
  bottom: 0;
  z-index: 5;
  align-items: center;
  gap: 14px;
  max-width: 1180px;
  margin-top: auto;
  padding: 10px 14px 10px 20px;
  border-radius: 12px 12px 0 0;
  background: rgba(29, 28, 25, 0.96);
  color: #eceae4;
  box-shadow: 0 -10px 30px rgba(0, 0, 0, 0.12);
  flex-wrap: nowrap;
}

.rs-bar-count {
  color: #fff;
  font-weight: 500;
  white-space: nowrap;
}

.rs-bar-diff {
  gap: 6px;
  min-width: 0;
  overflow: hidden;
  flex-wrap: nowrap;
}

.rs-bar-chip {
  padding: 2px 7px;
  border-radius: 5px;
  background: #33322e;
  color: #d8d5cc;
  font-family: var(--sp-font-mono);
  font-size: 11px;
  white-space: nowrap;
}

.rs-bar-missing {
  color: #f6a3a3;
  white-space: nowrap;
}

.rs-bar-dirty {
  color: #f0c869;
  font-size: 12px;
  white-space: nowrap;
}

.rs-bar-button {
  color: #eceae4 !important;
  background: #2c2b28 !important;
  padding: 0 11px !important;
}

.product-dark .rs-bar {
  background: rgba(42, 42, 38, 0.97);
  box-shadow: 0 -10px 30px rgba(0, 0, 0, 0.4), inset 0 1px 0 var(--sp-border);
}

.rs-bar-primary {
  padding: 0 14px !important;
  color: #1d1c19 !important;
  background: #fff !important;
  font-weight: 500;
}

.rs-page .run-setup-result {
  max-width: 1180px;
  padding: 10px 0;
}

/* ---------- dialogs ---------- */
.rs-dialog {
  width: 760px;
  max-width: 92vw;
  max-height: 88vh;
  padding: 0 !important;
  border-radius: 16px !important;
  overflow-y: auto;
}

.rs-review {
  width: 100%;
  padding: 22px 26px;
}

.rs-review-title {
  font-family: var(--sp-font-serif);
  font-size: 19px;
  font-weight: 600;
}

.rs-review-sub {
  margin: 2px 0 16px;
  color: var(--sp-subtle);
}

.rs-review-label {
  margin-bottom: 6px;
  color: var(--sp-subtle);
  font-size: 11.5px;
}

.rs-mode-toggle {
  align-self: flex-start;
  margin-bottom: 14px;
  border: 1px solid var(--sp-border);
  border-radius: 8px;
  overflow: hidden;
}

.rs-mode-toggle .q-btn {
  border-radius: 0 !important;
  color: var(--sp-muted) !important;
  background: transparent !important;
}

.rs-mode-toggle .q-btn[aria-pressed="true"] {
  color: var(--sp-surface) !important;
  background: var(--sp-ink) !important;
}

.rs-notice {
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 10px;
  padding: 9px 12px;
  border: 1px solid;
  border-radius: 9px;
  font-size: 12.5px;
  line-height: 1.5;
  flex-wrap: nowrap;
}

.rs-notice .q-icon {
  margin-top: 1px;
  font-size: 17px;
}

.rs-notice-warn {
  border-color: var(--sp-warning-line);
  background: var(--sp-warning-soft);
  color: var(--sp-warning-ink);
}

.rs-notice-info {
  border-color: var(--sp-info-line);
  background: var(--sp-surface-subtle);
  color: var(--sp-ink);
}

.rs-destination-title {
  font-weight: 600;
}

.rs-destination-path {
  font-family: var(--sp-font-mono);
  font-size: 11.5px;
  overflow-wrap: anywhere;
}

.rs-destination-detail {
  opacity: 0.85;
}

/* Long paths wrap instead of pushing the column out of view; labels keep a readable width. */
.rs-dialog .q-table th,
.rs-dialog .q-table td {
  text-align: left !important;
}

.rs-dialog .q-table td {
  min-width: 9em;
  white-space: normal;
  overflow-wrap: anywhere;
}

.rs-notice-bad {
  border-color: var(--sp-danger-line);
  background: var(--sp-danger-soft);
  color: var(--sp-danger);
}

.rs-review-metrics {
  gap: 28px;
  margin: 6px 0 12px;
}

.rs-review-metrics .workspace-metric-value {
  font-size: 22px;
  font-weight: 500;
}

.rs-review-section {
  margin-bottom: 8px;
  border: 1px solid var(--sp-border);
  border-radius: 10px;
}

.rs-review .run-setup-review-expansion {
  margin-bottom: 8px;
  border: 1px solid var(--sp-border);
  border-radius: 10px;
}

.rs-review-actions {
  justify-content: flex-end;
  gap: 8px;
  margin-top: 14px;
}

/* The dialog scrolls; keep its decision buttons in view at the bottom. */
.rs-dialog .rs-review-actions {
  position: sticky;
  bottom: 0;
  z-index: 2;
  margin-top: 14px;
  padding: 12px 0 16px;
  border-top: 1px solid var(--sp-border);
  background: var(--sp-surface);
}
"""
