"""Differential APA page; builds on overview, environment and studies styles."""

from __future__ import annotations


DIFFERENTIAL_CSS = """
.product-page-differential_apa .product-page-heading {
  display: none;
}

.product-page-content.product-page-content-differential_apa {
  padding: 0;
  overflow-y: auto;
  background: var(--sp-bg);
}

.df-intro {
  margin-top: 20px;
}

.df-intro-note {
  max-width: 820px;
}

.df-layout {
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr);
  gap: 24px;
  align-items: start;
  margin-top: 20px;
}

.df-list {
  min-width: 0;
  align-items: stretch !important;
  border: 1px solid var(--sp-border);
  border-radius: 12px;
  background: var(--sp-surface);
  overflow: hidden;
}

.df-item-status-row {
  align-items: center;
  gap: 8px;
  flex-wrap: nowrap;
}

.df-launched {
  color: var(--sp-subtle);
  font-size: 11px;
  white-space: nowrap;
}

.df-table-tools {
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--sp-divider);
}

.df-filter {
  width: 220px;
}

.df-sort {
  width: 240px;
}

.df-readable-switch {
  font-size: 12.5px;
}

.df-table th.df-sorted {
  color: var(--sp-ink-strong);
  text-decoration: underline;
  text-underline-offset: 3px;
}

.df-item > * {
  max-width: 100%;
}

.df-item {
  width: 100%;
  min-width: 0;
  overflow: hidden;
  padding: 10px 14px;
  border-bottom: 1px solid var(--sp-divider);
  border-left: 2px solid transparent;
  cursor: pointer;
}

.df-item:last-child {
  border-bottom: 0;
}

.df-item:hover {
  background: var(--sp-control-hover);
}

.df-item-active,
.df-item-active:hover {
  border-left-color: var(--sp-ink);
  background: var(--sp-surface-subtle);
}

.df-item-top {
  align-items: center;
  gap: 8px;
  flex-wrap: nowrap;
}

.df-item-name {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  color: var(--sp-ink-strong);
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.df-kind {
  flex: none;
  padding: 0 6px;
  border-radius: 4px;
  background: var(--sp-surface-muted);
  color: var(--sp-muted);
  font-size: 10.5px;
}

.df-item-status {
  margin-top: 4px;
  font-size: 11.5px;
}

.df-item-path {
  max-width: 100%;
  direction: rtl;
  text-align: left;
  margin-top: 2px;
  overflow: hidden;
  color: var(--sp-subtle);
  font-family: var(--sp-font-mono);
  font-size: 10.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.df-detail {
  min-width: 0;
  align-items: stretch !important;
}

.df-head {
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 16px;
  flex-wrap: nowrap;
}

/* The title block shrinks and wraps its badge so it never runs under the remove button. */
.df-head > .column:first-child {
  flex: 1 1 0;
  min-width: 0;
}

.df-title-row {
  align-items: center;
  gap: 4px 10px;
  flex-wrap: wrap;
  min-width: 0;
}

.df-title-row .st-detail-title {
  min-width: 0;
  max-width: 100%;
}

.df-chip-warn {
  background: var(--sp-warning-soft) !important;
  color: var(--sp-warning) !important;
}

.df-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 16px;
  align-items: start;
  margin-bottom: 16px;
}

.df-text {
  color: var(--sp-ink);
  font-size: 12.5px;
  line-height: 1.6;
}

.df-code-label {
  margin-top: 6px;
  color: var(--sp-subtle);
  font-size: 11.5px;
}

.df-code-row {
  align-items: flex-start;
  gap: 4px;
  flex-wrap: nowrap;
}

.df-code-wrap {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
}

.df-code-wrap .df-code {
  box-sizing: border-box;
  width: 100% !important;
  max-width: 100% !important;
  overflow-x: auto !important;
  white-space: pre !important;
}

.df-code {
  margin: 0;
  padding: 10px 12px;
  font-size: 11px;
  line-height: 1.5;
  white-space: pre;
  overflow-x: auto;
}

.df-table-card {
  margin-bottom: 16px;
}

.df-table-scroll {
  max-height: 560px;
  overflow: auto;
}

.df-table {
  border-collapse: collapse;
  font-family: var(--sp-font-mono);
  font-size: 11px;
}

.df-table th {
  position: sticky;
  top: 0;
  z-index: 1;
  padding: 7px 10px;
  border-bottom: 1px solid var(--sp-border);
  background: var(--sp-surface-subtle);
  color: var(--sp-muted);
  font-family: var(--sp-font-sans, inherit);
  font-weight: 500;
  text-align: left;
  white-space: nowrap;
}

.df-table td {
  padding: 5px 10px;
  border-bottom: 1px solid var(--sp-divider);
  color: var(--sp-ink);
  white-space: nowrap;
}

.df-table tr:hover td {
  background: var(--sp-control-hover);
}

.df-na {
  color: var(--sp-subtle) !important;
}

.df-exact {
  margin-left: 12px;
}

.df-limits {
  border: 1px solid var(--sp-border);
  border-radius: 12px;
  background: var(--sp-surface);
}

.df-limit {
  padding: 4px 16px 8px;
  color: var(--sp-muted);
  font-size: 12px;
  line-height: 1.55;
}

.df-path-row {
  align-items: center;
  gap: 8px;
  flex-wrap: nowrap;
}

.df-path-input {
  flex: 1 1 auto;
}

.df-approve-dialog {
  width: min(680px, 92vw);
  max-width: 680px !important;
}

.df-approve-summary {
  border: 1px solid var(--sp-border);
  border-radius: 10px;
  padding: 4px 12px;
  background: var(--sp-surface-muted);
}

.df-approve-summary .ov-detail-value {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.df-mono-input input {
  font-family: var(--sp-font-mono);
  font-size: 12px;
}

.df-approve-check {
  font-size: 13px;
}

.df-next-actions {
  margin-top: 6px;
  gap: 8px;
}

.df-launch-link {
  align-items: center;
  gap: 8px;
  padding: 3px 6px;
  border-radius: 6px;
  cursor: pointer;
  color: var(--sp-text);
  font-size: 12px;
}

.df-launch-link:hover,
.df-launch-link:focus-visible {
  background: var(--sp-control-hover);
}

.df-progress {
  margin-bottom: 12px;
}

.df-spinner {
  color: var(--sp-accent);
}

.df-log {
  max-height: 220px;
  overflow: auto !important;
  white-space: pre-wrap !important;
  word-break: break-all;
}

.df-detail > .ov-alert + .df-progress {
  margin-top: 12px;
}

.df-warn-note {
  color: var(--sp-warning) !important;
}

.df-plan-raw {
  max-height: 320px;
  margin: 6px 0 0;
  padding: 10px 12px;
  overflow: auto;
  border: 1px solid var(--sp-border);
  border-radius: 8px;
  background: var(--sp-bg);
  color: var(--sp-ink);
  font-family: var(--sp-font-mono);
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.df-plan-raw:focus-visible {
  outline: 0;
  box-shadow: var(--sp-focus-ring);
}
"""
