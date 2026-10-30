"""Runs page: flush run list and document-style run detail."""

from __future__ import annotations


RUNS_CSS = """
.product-page-runs .product-page-heading {
  display: none;
}

.product-page-content.product-page-content-runs {
  padding: 0;
  overflow: hidden;
  background: var(--sp-bg);
}

.runs-layout {
  display: grid;
  grid-template-columns: 300px minmax(0, 1fr);
  flex: 1 1 auto;
  width: 100%;
  height: 100%;
  min-height: 0;
}

/* ---------- list ---------- */
.runs-list {
  display: flex;
  flex-direction: column;
  min-height: 0;
  border-right: 1px solid var(--sp-border);
  background: var(--sp-surface-subtle);
}

.runs-list-head {
  flex: 0 0 auto;
  gap: 8px !important;
  padding: 14px 14px 10px;
  border-bottom: 1px solid var(--sp-divider);
}

.runs-list-search .q-field__control {
  height: 34px;
  min-height: 34px !important;
  background: var(--sp-surface);
}

.runs-filter-chips {
  gap: 5px;
  flex-wrap: wrap;
}

.runs-chip {
  min-height: 24px !important;
  padding: 0 8px !important;
  border-radius: 6px !important;
  background: var(--sp-surface-muted) !important;
  color: var(--sp-muted) !important;
  font-size: 11.5px !important;
  font-weight: 400 !important;
}

.runs-chip-active {
  background: var(--sp-ink) !important;
  color: var(--sp-surface) !important;
}

.runs-list-scroll {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: 6px 0;
}

.runs-list-row {
  padding: 10px 16px 10px 14px;
  border-left: 2px solid transparent;
  cursor: pointer;
}

.runs-list-row:hover {
  background: var(--sp-control-hover);
}

.runs-list-row-active,
.runs-list-row-active:hover {
  border-left-color: var(--sp-ink);
  background: var(--sp-surface);
}

.runs-list-row-top {
  align-items: center;
  gap: 8px;
  flex-wrap: nowrap;
}

.runs-list-name {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  color: var(--sp-ink-strong);
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.runs-list-status {
  flex: none;
}

.runs-list-meta {
  margin-top: 3px;
  overflow: hidden;
  color: var(--sp-subtle);
  font-size: 11.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.runs-list-empty {
  padding: 28px 18px;
}

.runs-empty-title {
  color: var(--sp-ink);
  font-family: var(--sp-font-serif);
  font-size: 15px;
  font-weight: 600;
}

.runs-list-foot {
  flex: 0 0 auto;
  align-items: center;
  gap: 4px;
  padding: 10px 12px;
  border-top: 1px solid var(--sp-divider);
  flex-wrap: nowrap;
}

.runs-new-button {
  flex: 1 1 auto;
}

.runs-icon-button {
  color: var(--sp-muted) !important;
}

/* ---------- document ---------- */
.runs-doc {
  min-width: 0;
  min-height: 0;
  padding: 24px 34px 48px;
  overflow-y: auto;
}

.runs-doc-empty {
  align-items: flex-start;
  padding: 60px 0;
  color: var(--sp-subtle);
}

.runs-doc-empty .q-icon {
  font-size: 30px;
}

.runs-doc-header {
  align-items: flex-start;
  gap: 16px;
  flex-wrap: nowrap;
}

.runs-doc-heading {
  flex: 1 1 auto;
  min-width: 0;
}

.runs-doc-heading > * {
  max-width: 100%;
}

.runs-doc-eyebrow {
  margin-bottom: 3px;
  color: var(--sp-subtle);
  font-size: 11.5px;
}

.runs-doc-title-row {
  align-items: center;
  gap: 12px;
  flex-wrap: nowrap;
  min-width: 0;
}

.runs-doc-title {
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

.runs-doc-status {
  flex: none;
  font-size: 12.5px;
}

.runs-doc-chips {
  align-items: center;
  gap: 6px;
  margin-top: 9px;
  flex-wrap: wrap;
}

.runs-chip-static {
  padding: 2px 8px;
  border-radius: 6px;
  background: var(--sp-surface-muted);
  color: var(--sp-muted);
  font-size: 11.5px;
  white-space: nowrap;
}

.runs-chip-mono {
  font-family: var(--sp-font-mono);
  font-size: 11px;
}

.runs-attempt-select {
  width: 250px;
}

.runs-attempt-select .q-field__control {
  height: 24px;
  min-height: 24px !important;
  padding: 0 4px 0 8px;
  border-radius: 6px;
  background: var(--sp-surface-muted);
}

.runs-attempt-select .q-field__control::before,
.runs-attempt-select .q-field__control::after {
  display: none;
}

.runs-attempt-select .q-field__native {
  min-height: 24px;
  padding: 0;
  color: var(--sp-muted);
  font-size: 11.5px;
}

.runs-attempt-select .q-field__marginal {
  height: 24px;
}

.runs-doc-actions {
  flex: none;
  gap: 8px;
  padding-top: 16px;
  flex-wrap: nowrap;
}

.runs-action {
  background: var(--sp-surface) !important;
}

.runs-callout {
  align-items: flex-start;
  gap: 10px;
  margin-top: 16px;
  padding: 11px 14px;
  border: 1px solid;
  border-radius: 10px;
  font-size: 12.5px;
  line-height: 1.55;
  flex-wrap: nowrap;
}

.runs-callout-warn {
  border-color: var(--sp-warning-line);
  background: var(--sp-warning-soft);
  color: var(--sp-warning-ink);
}

.runs-callout .q-icon {
  margin-top: 1px;
  font-size: 18px;
}

.runs-callout-title {
  font-weight: 600;
}

.runs-stages {
  display: grid;
  gap: 4px;
  margin: 22px 0 2px;
}

.runs-stage {
  min-width: 0;
}

.runs-stage-bar {
  width: 100%;
  height: 6px;
  border-radius: 3px;
  background: var(--sp-border);
}

.runs-stage-good { background: var(--sp-good-dot); }

.runs-stage-name {
  margin-top: 6px;
  overflow: hidden;
  color: var(--sp-muted);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.runs-stage-count {
  color: var(--sp-subtle);
  font-family: var(--sp-font-mono);
  font-size: 10.5px;
}

.runs-doc-tabs {
  position: sticky;
  top: -24px;
  z-index: 4;
  margin-top: 18px;
  padding-top: 6px;
  border-bottom: 1px solid var(--sp-border);
  background: var(--sp-bg);
}

/* Tables inside the run document stick below the tabs. */
.runs-doc {
  --sp-sticky-offset: 21px;
}

/* Logs get a fixed-height pane: the file list and the log scroll independently. */
.runs-doc .run-log-layout {
  height: max(420px, calc(100vh - 250px));
  border: 1px solid var(--sp-border);
  border-radius: 10px;
  overflow: hidden;
}

.runs-doc-tabs .q-tabs__content {
  gap: 22px;
}

.runs-doc-tabs .q-tab {
  min-height: 38px;
  padding: 0;
  color: var(--sp-muted);
  font-size: 13px;
  font-weight: 400;
}

.runs-doc-tabs .q-tab--active {
  color: var(--sp-ink-strong);
  font-weight: 500;
}

.runs-doc-tabs .q-tab__indicator {
  height: 2px;
  background: var(--sp-ink);
}

.runs-doc-panels,
.runs-doc-panels .q-tab-panel {
  padding: 0 !important;
  background: transparent;
}

.runs-doc-panels .q-tab-panel {
  padding-top: 18px !important;
}

.runs-overview {
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(0, 1fr);
  gap: 36px;
  align-items: start;
}

.runs-section-title {
  align-items: baseline;
  gap: 8px;
  margin-bottom: 6px;
  color: var(--sp-muted);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.02em;
  flex-wrap: nowrap;
}

.runs-section-spaced {
  margin-top: 22px;
}

.runs-section-count {
  color: var(--sp-subtle);
  font-weight: 400;
}

.runs-line,
.runs-location {
  align-items: center;
  gap: 10px;
  min-height: 34px;
  padding: 5px 0;
  border-bottom: 1px solid var(--sp-divider);
  font-size: 12.5px;
  flex-wrap: nowrap;
}

.runs-line-status {
  flex: 0 0 74px;
}

.runs-line-label {
  min-width: 0;
  overflow: hidden;
  color: var(--sp-ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.runs-muted-line {
  padding: 6px 0;
  color: var(--sp-subtle);
  font-size: 12.5px;
}

.runs-location-key {
  flex: 0 0 96px;
  color: var(--sp-subtle);
}

.runs-location-value {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  color: var(--sp-ink);
  font-family: var(--sp-font-mono);
  font-size: 11.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.runs-detail-value {
  flex: 1 1 auto;
  min-width: 0;
  color: var(--sp-ink);
  overflow-wrap: anywhere;
}

.runs-copy {
  flex: none;
  color: var(--sp-subtle) !important;
}

.runs-command-expansion {
  margin-top: 14px;
  border: 1px solid var(--sp-border);
  border-radius: 10px;
  background: var(--sp-surface);
}

.runs-overview .runs-process-panel {
  margin-top: 22px;
}

/* ---------- artifacts tab ---------- */
.runs-art-head {
  align-items: center;
  gap: 8px;
}

.runs-art-table {
  border: 1px solid var(--sp-border);
  border-radius: 10px;
  overflow: clip;
  background: var(--sp-surface);
}

.runs-art-table .ov-tr {
  grid-template-columns: minmax(0, 1.5fr) 88px 72px minmax(0, 1.6fr) 64px;
}

.runs-detail-link {
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 3px;
}

.runs-art-name > * {
  max-width: 100%;
}

/* Names differ only at the end ("… BED", "… GTF"), so let them wrap instead of truncating. */
.runs-art-name .ov-run-name,
.runs-line-label {
  overflow-wrap: anywhere;
  white-space: normal;
}

.runs-art-file {
  overflow: hidden;
  direction: rtl;
  text-align: left;
  color: var(--sp-subtle);
  font-family: var(--sp-font-mono);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.runs-art-size {
  font-variant-numeric: tabular-nums;
}

.runs-art-dir {
  direction: rtl;
  text-align: left;
  font-family: var(--sp-font-mono);
  font-size: 11px;
}

.runs-art-actions {
  justify-content: flex-end;
  gap: 0;
  flex-wrap: nowrap;
}

.runs-attempt-origin {
  color: var(--sp-subtle);
  font-size: 12px;
}
"""
