"""Artifacts, logs, reference, and diagnostics styles."""

from __future__ import annotations


OPERATIONS_CSS = """
.product-page-content-artifacts {
  overflow: hidden;
}

.artifacts-workbench-v2 {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-height: 0;
  gap: 0;
}

.artifacts-toolbar-search {
  flex: 0 1 320px;
  width: min(28vw, 320px);
  min-width: 190px;
}

.artifacts-master-detail {
  display: grid;
  flex: 1 1 auto;
  grid-template-columns: minmax(300px, 340px) minmax(0, 1fr);
  width: 100%;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  border: 0;
  background: var(--sp-surface);
}

.artifacts-master,
.artifacts-detail {
  min-width: 0;
  min-height: 0;
}

.artifacts-master {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border-right: 1px solid var(--sp-border);
}

.artifacts-master-header,
.artifacts-master-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 62px 44px 58px;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.artifacts-master-header {
  flex: 0 0 36px;
  height: 36px;
  padding: 0 8px;
  border-bottom: 1px solid var(--sp-border-strong);
  background: var(--sp-surface-subtle);
  color: var(--sp-muted);
  font-size: var(--sp-font-sm);
  font-weight: 650;
}

.artifacts-master-scroll {
  flex: 1 1 auto;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
}

.artifacts-master-row {
  min-height: 64px;
  padding: 8px;
  border-bottom: 1px solid var(--sp-divider);
  cursor: pointer;
}

.artifacts-master-row:hover {
  background: var(--sp-surface-subtle);
}

.artifacts-master-row-active {
  box-shadow: inset 3px 0 0 var(--sp-accent);
  background: var(--sp-surface-selected);
}

.artifacts-detail {
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.artifacts-detail-header {
  flex: 0 0 auto;
  align-items: flex-start;
  gap: 10px;
  min-height: 76px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--sp-divider);
}

.artifacts-detail-heading {
  flex: 0 1 720px;
  width: min(720px, 100%);
  max-width: 720px;
}

.artifacts-detail-heading .runs-detail-title {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  width: 100%;
  max-width: 720px;
  max-height: calc(2 * 1.2em);
  overflow: hidden;
  overflow-wrap: anywhere;
  text-overflow: clip;
  white-space: normal;
}

.artifacts-master-primary {
  min-width: 0;
}

.artifacts-master-attempt-count {
  text-align: center;
  font-variant-numeric: tabular-nums;
}

.artifacts-attempt-select {
  flex: 0 0 190px;
  width: 190px;
}

.artifacts-detail-meta {
  min-width: 0;
  flex-wrap: wrap;
  row-gap: 2px;
}

.artifacts-detail-note {
  min-width: 180px;
  max-width: min(42vw, 560px);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.artifacts-tab-content {
  min-height: 100%;
  padding: 18px;
}

.artifacts-summary-row {
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: nowrap;
}

.artifacts-summary-row .artifacts-metric-strip {
  flex: 1 1 auto;
  max-width: 520px;
}

.artifacts-path-table {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  border: 1px solid var(--sp-border);
}

.run-log-browser {
  display: flex;
  height: 100%;
  min-height: 100%;
  flex-direction: column;
  gap: 0;
  overflow: hidden;
  background: var(--sp-surface);
}

.run-log-sourcebar {
  flex: 0 0 52px;
  align-items: center;
  min-height: 52px;
  gap: 10px;
  padding: 8px 14px;
  border-bottom: 1px solid var(--sp-border);
  flex-wrap: nowrap;
}

.run-log-inline-label {
  flex: 0 0 auto;
  color: var(--sp-muted);
  font-size: var(--sp-font-body);
  font-weight: 600;
}

.run-log-source-select {
  flex: 0 1 720px;
  width: min(54vw, 720px);
  min-width: 380px;
}

.run-log-sourcebar > .product-small-muted {
  flex: 0 0 auto;
  white-space: nowrap;
}

.run-log-live-toggle {
  flex: 0 0 auto;
  color: var(--sp-muted);
  font-size: var(--sp-font-body-sm);
}

.run-log-live-toggle .q-toggle__label {
  font-size: var(--sp-font-body-sm);
  font-weight: 600;
}

.run-log-refresh-button {
  flex: 0 0 34px;
  width: 34px;
  min-width: 34px !important;
  height: 34px;
  min-height: 34px !important;
}

.run-log-contract-warning,
.run-log-invocation-error {
  flex: 0 0 auto;
  align-items: center;
  min-height: 36px;
  gap: 8px;
  padding: 7px 14px;
  border-bottom: 1px solid var(--sp-border);
}

.run-log-contract-warning {
  color: var(--sp-warning);
  background: var(--sp-warning-soft);
}

.run-log-invocation-error {
  color: var(--sp-danger);
  background: var(--sp-danger-soft);
}

.run-log-invocation-summary {
  display: grid;
  flex: 0 0 auto;
  grid-template-columns: repeat(4, minmax(140px, 1fr));
  min-width: 0;
  border-bottom: 1px solid var(--sp-border);
}

.run-log-summary-item {
  min-width: 0;
  padding: 8px 14px;
  border-right: 1px solid var(--sp-divider);
}

.run-log-summary-item:last-child {
  border-right: 0;
}

.run-log-summary-label {
  color: var(--sp-muted);
  font-size: var(--sp-font-sm);
  font-weight: 600;
}

.run-log-summary-value {
  min-width: 0;
  overflow: hidden;
  color: var(--sp-ink-strong);
  font-size: var(--sp-font-body);
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.run-log-summary-title {
  color: var(--sp-ink-strong);
  font-size: var(--sp-font-body);
  font-weight: 650;
}

.run-log-status-success {
  color: var(--sp-success) !important;
}

.run-log-status-danger {
  color: var(--sp-danger) !important;
}

.run-log-status-info {
  color: var(--sp-info) !important;
}

.run-log-invocation-details {
  flex: 0 0 auto;
  border-bottom: 1px solid var(--sp-border);
}

.run-log-invocation-details > .q-expansion-item__container > .q-item {
  min-height: 36px;
  padding: 0 14px;
  color: var(--sp-muted);
  font-size: var(--sp-font-body-sm);
}

.run-log-invocation-details .q-expansion-item__content {
  padding: 8px 14px 12px;
  background: var(--sp-surface-subtle);
}

.run-log-detail-grid {
  display: grid;
  grid-template-columns: minmax(90px, 0.25fr) minmax(240px, 1fr);
  gap: 12px;
  margin-bottom: 8px;
}

.run-log-detail-item {
  min-width: 0;
}

.run-log-detail-value {
  overflow: hidden;
  color: var(--sp-ink);
  font-family: var(--sp-font-mono);
  font-size: var(--sp-font-caption);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.run-log-command {
  max-height: 160px;
  margin: 0;
  padding: 10px 12px;
  overflow: auto;
  border: 1px solid var(--sp-border);
  background: var(--sp-control-bg);
  color: var(--sp-ink);
  font-family: var(--sp-font-mono);
  font-size: var(--sp-font-caption);
  line-height: 1.5;
  white-space: pre-wrap;
}

.run-log-compatibility-summary {
  flex: 0 0 56px;
  align-items: center;
  min-height: 56px;
  gap: 10px;
  padding: 8px 14px;
  border-bottom: 1px solid var(--sp-border);
  background: var(--sp-surface-subtle);
}

.run-log-layout {
  display: grid;
  flex: 1 1 auto;
  grid-template-columns: minmax(260px, 320px) minmax(0, 1fr);
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}

.run-log-tree {
  min-width: 0;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
  border-right: 1px solid var(--sp-border);
  background: var(--sp-surface-subtle);
}

.run-log-group {
  border-bottom: 1px solid var(--sp-border);
}

.run-log-group-header {
  display: grid;
  grid-template-columns: 20px minmax(0, 1fr) max-content;
  align-items: center;
  min-height: 34px;
  gap: 6px;
  padding: 0 10px;
  color: var(--sp-muted);
  font-size: var(--sp-font-body-sm);
  font-weight: 650;
}

.run-log-group-header .q-icon {
  font-size: var(--sp-icon-compact);
}

.run-log-group-count {
  color: var(--sp-subtle);
  font-family: var(--sp-font-mono);
  font-size: var(--sp-font-sm);
}

.run-log-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) max-content;
  align-items: center;
  min-height: 34px;
  gap: 8px;
  padding: 4px 10px 4px 30px;
  border-top: 1px solid var(--sp-divider);
  cursor: pointer;
}

.run-log-row:hover {
  background: var(--sp-control-hover);
}

.run-log-row-active {
  box-shadow: inset 3px 0 0 var(--sp-accent);
  background: var(--sp-surface-selected);
}

.run-log-row-name {
  min-width: 0;
  overflow: hidden;
  color: var(--sp-ink);
  font-family: var(--sp-font-mono);
  font-size: var(--sp-font-caption);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.run-log-row-size {
  color: var(--sp-subtle);
  font-family: var(--sp-font-mono);
  font-size: var(--sp-font-sm);
}

.run-log-tool-group {
  border-top: 1px solid var(--sp-divider);
}

.run-log-tool-group > .q-expansion-item__container > .q-item {
  min-height: 34px;
  padding: 0 10px 0 26px;
  color: var(--sp-ink);
  font-family: var(--sp-font-mono);
  font-size: var(--sp-font-caption);
}

.run-log-tool-group .q-expansion-item__content {
  padding: 0;
}

.run-log-tool-group .run-log-row {
  padding-left: 42px;
}

.run-log-viewer {
  display: flex;
  min-width: 0;
  min-height: 0;
  flex-direction: column;
  overflow: hidden;
  background: #10191e;
}

.run-log-viewer-header {
  display: flex;
  flex: 0 0 54px;
  align-items: center;
  min-width: 0;
  min-height: 54px;
  gap: 10px;
  padding: 8px 14px;
  border-bottom: 1px solid #304047;
  background: #17242a;
}

.run-log-viewer-title {
  min-width: 0;
  overflow: hidden;
  color: #eef5f6;
  font-family: var(--sp-font-mono);
  font-size: var(--sp-font-body-sm);
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.run-log-viewer-header .product-small-muted {
  color: #93a7ae;
}

.run-log-console-host {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  overflow: auto;
}

.run-log-console {
  width: 100%;
  min-width: 100%;
  min-height: 100%;
  margin: 0;
  padding: 14px 16px;
  color: #d9e6e9;
  background: #10191e;
  font-family: var(--sp-font-mono);
  font-size: var(--sp-font-caption);
  line-height: 1.5;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.run-log-empty,
.run-log-console-nowrap {
  width: max-content;
  overflow-wrap: normal;
  white-space: pre;
}

.run-log-wrap-toggle {
  flex: none;
  color: #93a7ae;
  font-size: 12px;
}

.run-log-wrap-toggle .q-toggle__track {
  background: #93a7ae;
  opacity: 0.45;
}

.run-log-wrap-toggle .q-toggle__inner--truthy .q-toggle__track {
  background: #d9e6e9;
  opacity: 0.6;
}

.run-log-wrap-toggle .q-toggle__thumb::after {
  background: #d9e6e9;
}

.run-log-truncated {
  align-items: center;
  gap: 8px;
  padding: 4px 16px;
  border-bottom: 1px solid #22333a;
  background: #142026;
  color: #93a7ae;
  font-size: 12px;
}

.run-log-truncated-button {
  color: #d9e6e9 !important;
}

.run-log-copy {
  flex: none;
  color: #c4d3d7 !important;
}

.run-log-copy:hover {
  color: #d9e6e9;
}

.run-log-row-name {
  direction: rtl;
  text-align: left;
}

.run-log-viewer-empty {
  align-items: center;
  justify-content: center;
  min-height: 240px;
  color: var(--sp-muted);
}

.product-page-content-settings {
  overflow: hidden;
}

.settings-workbench {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-height: 0;
  gap: 0;
}

.settings-toolbar {
  min-height: 50px;
}

.reference-summary {
  display: grid;
  flex: 0 0 auto;
  grid-template-columns: minmax(420px, 0.9fr) minmax(0, 1.1fr);
  min-width: 0;
  border: 0;
  border-bottom: 1px solid var(--sp-border);
  background: var(--sp-surface);
}

.reference-summary-ready {
  box-shadow: inset 3px 0 0 var(--sp-success);
}

.reference-summary-missing,
.reference-summary-error {
  box-shadow: inset 3px 0 0 var(--sp-danger);
}

.reference-summary-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  min-width: 0;
  border-right: 1px solid var(--sp-border);
}

.reference-summary-metric {
  min-width: 0;
  justify-content: center;
  padding: 12px 16px;
  border-right: 1px solid var(--sp-divider);
}

.reference-summary-metric:last-child {
  border-right: 0;
}

.reference-summary-value {
  min-width: 0;
  overflow: hidden;
  color: var(--sp-ink-strong);
  font-size: var(--sp-font-subtitle);
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.reference-summary-paths {
  min-width: 0;
  padding: 8px 14px;
}

.reference-summary-paths .reference-kv-row {
  padding: 5px 0;
}

.settings-body {
  display: grid;
  flex: 1 1 auto;
  grid-template-columns: minmax(380px, 0.9fr) minmax(420px, 1.1fr);
  width: 100%;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  border: 0;
  background: var(--sp-surface);
}

.settings-inventory,
.settings-diagnostics {
  min-width: 0;
  min-height: 0;
  padding: 16px;
  overflow-y: auto;
}

.settings-inventory {
  border-right: 1px solid var(--sp-border);
}

.settings-inventory-table {
  border: 0;
}

.settings-inventory-table .path-row {
  grid-template-columns: minmax(150px, 0.78fr) minmax(128px, 0.58fr) minmax(0, 1fr) 30px;
}

.settings-inventory-table .path-row-status {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.settings-reference-inventory-summary,
.settings-scope-detail {
  min-width: 0;
}

.settings-scope-detail {
  margin-top: -4px;
  line-height: 1.35;
}

.settings-issue-list {
  flex: 0 0 auto;
  min-height: 0;
  border: 0;
}

.settings-diagnostics .reference-diagnostic-list.settings-issue-list {
  flex: 0 0 auto;
  height: auto;
  max-height: none;
  overflow: visible;
  border: 0;
}

.settings-no-issues {
  align-items: center;
  gap: 8px;
  min-height: 48px;
  padding: 10px 12px;
  border: 1px solid var(--sp-divider);
  background: var(--sp-surface-subtle);
}

.settings-no-issues-flat {
  min-height: 42px;
  border: 0;
  border-top: 1px solid var(--sp-divider);
  border-bottom: 1px solid var(--sp-divider);
}

.settings-runtime-heading {
  margin-top: 8px;
  padding-top: 10px;
  border-top: 1px solid var(--sp-border);
}

.settings-healthy-expansion {
  border-bottom: 1px solid var(--sp-divider);
}

.settings-healthy-expansion .q-expansion-item__content {
  padding: 0 0 12px;
}

.artifacts-workbench,
.reference-workbench {
  display: grid;
  width: 100%;
  min-width: 0;
  min-height: 620px;
  height: 100%;
  max-height: 100%;
  overflow: hidden;
  border: 1px solid var(--sp-border);
  background: var(--sp-surface);
}

.artifacts-workbench {
  grid-template-columns: minmax(280px, 340px) minmax(400px, 1fr) minmax(330px, 0.85fr);
}

.reference-workbench {
  grid-template-columns: minmax(300px, 0.75fr) minmax(380px, 1fr) minmax(360px, 1fr);
}

.artifacts-workbench > *,
.reference-workbench > * {
  min-width: 0;
  min-height: 0;
  height: 100%;
  max-height: 100%;
  padding: 16px;
  overflow: hidden;
  border: 0;
  border-right: 1px solid var(--sp-border);
  border-radius: 0;
}

.artifacts-workbench > *:last-child,
.reference-workbench > *:last-child {
  border-right: 0;
}

.artifacts-run-list,
.artifacts-path-scroll,
.reference-diagnostic-list {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  border: 1px solid var(--sp-border);
}

.artifacts-run-list {
  display: flex;
  flex-direction: column;
}

.artifacts-metric-strip,
.reference-metric-strip {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.artifacts-status-filter {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  width: 216px;
  min-height: 34px;
  border: 1px solid var(--sp-border-strong);
  border-radius: var(--sp-radius-control);
  overflow: hidden;
}

.artifacts-status-filter .q-btn {
  width: 100%;
  min-width: 0;
  border-radius: 0 !important;
  border-right: 1px solid var(--sp-border);
}

.artifacts-status-filter .q-btn:last-child {
  border-right: 0;
}

.artifacts-status-filter .q-btn[aria-pressed="true"] {
  background: var(--sp-surface-selected) !important;
  color: var(--sp-accent-ink) !important;
}

.artifacts-log-panel {
  display: flex;
  flex-direction: column;
}

.artifacts-log-viewer {
  flex: 1 1 auto;
  min-height: 0;
}

.artifacts-log-viewer .q-field__control,
.artifacts-log-viewer .q-field__native {
  height: 100%;
  min-height: 360px;
}

.artifacts-log-viewer textarea {
  min-height: 340px !important;
  color: #d9e6e9 !important;
  background: #10191e;
  font-family: var(--sp-font-mono);
  font-size: var(--sp-font-caption);
  line-height: 1.5;
}

.reference-current-panel,
.reference-inventory-panel,
.reference-diagnostics-panel {
  display: flex;
  flex-direction: column;
}

.reference-cache-box {
  padding: 8px 0;
  border-top: 1px solid var(--sp-divider);
}

.reference-diagnostic-list {
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.reference-diagnostic-row {
  padding: 10px 12px;
  border-bottom: 1px solid var(--sp-divider);
}

.reference-diagnostic-row:last-child {
  border-bottom: 0;
}

.reference-diagnostic-level {
  color: var(--sp-muted);
  font-family: var(--sp-font-mono);
  font-size: var(--sp-font-sm);
  font-weight: 650;
}

.reference-diagnostic-ok .reference-diagnostic-level {
  color: var(--sp-success);
}

.reference-diagnostic-warning .reference-diagnostic-level {
  color: var(--sp-warning);
}

.reference-diagnostic-error .reference-diagnostic-level {
  color: var(--sp-danger);
}

.reference-diagnostic-message {
  flex: 1 1 auto;
  min-width: 0;
  color: var(--sp-ink);
  font-size: var(--sp-font-body-sm);
  line-height: 1.4;
}

.reference-diagnostic-detail {
  margin: 4px 0 0;
  color: var(--sp-muted);
  font-family: var(--sp-font-mono);
  font-size: var(--sp-font-sm);
  line-height: 1.45;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.reference-diagnostic-technical {
  margin-top: 5px;
  border-top: 1px solid var(--sp-divider);
}

.reference-diagnostic-technical .q-expansion-item__content {
  padding: 0 0 2px;
}
"""
