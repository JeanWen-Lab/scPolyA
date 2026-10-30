"""Parameter-study workbench styles."""

from __future__ import annotations


STUDIES_CSS = """
.study-page {
  display: flex;
  flex-direction: column;
  gap: 0;
  width: 100%;
  min-width: 0;
  height: 100%;
  min-height: 0;
}

.study-contextbar {
  flex: 0 0 42px;
  align-items: center;
  min-width: 0;
  height: 42px;
  padding: 0 8px 0 0;
  border: 0;
  border-bottom: 1px solid var(--sp-border);
  background: var(--sp-surface);
}

.study-view-tabs {
  align-self: stretch;
  min-height: 40px;
}

.study-view-tabs .q-tab {
  min-height: 40px;
  padding: 0 16px;
  color: var(--sp-muted);
  font-size: var(--sp-font-body);
  font-weight: 600;
  text-transform: none;
}

.study-view-tabs .q-tab__content {
  flex-direction: row;
  flex-wrap: nowrap;
  gap: 8px;
  min-width: 0;
}

.study-view-tabs .q-tab__icon {
  margin: 0;
}

.study-view-tabs .q-tab--active {
  color: var(--sp-accent);
}

.study-view-tabs .q-tab__indicator {
  height: 2px;
  background: var(--sp-accent);
}

.study-workspace-label {
  max-width: 260px;
  overflow: hidden;
  color: var(--sp-muted);
  font-family: var(--sp-font-mono);
  font-size: var(--sp-font-caption);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.study-build-layout,
.study-saved-layout {
  display: grid;
  flex: 1 1 auto;
  width: 100%;
  min-width: 0;
  min-height: 0;
  border: 0;
  background: var(--sp-surface);
  overflow: hidden;
}

.study-build-layout {
  grid-template-columns:
    minmax(560px, 1fr)
    minmax(520px, 1.05fr)
    minmax(304px, 0.62fr);
}

.study-builder-panel,
.study-preview-panel,
.study-build-review,
.study-saved-master,
.study-queue-detail {
  min-width: 0;
  min-height: 0;
  height: 100%;
  border-right: 1px solid var(--sp-border);
  background: var(--sp-surface);
}

.study-builder-panel {
  padding: 16px;
  overflow-x: hidden;
  overflow-y: auto;
}

.study-builder-field {
  grid-template-columns: 74px minmax(0, 1fr);
}

.study-preview-panel {
  overflow: hidden;
}

.study-build-review {
  display: flex;
  flex-direction: column;
  border-right: 0;
  background: var(--sp-surface-subtle);
  overflow: hidden;
}

.study-pane-header,
.study-queue-header {
  flex: 0 0 auto;
  align-items: center;
  min-height: 52px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--sp-border);
}

.study-builder-section-heading {
  align-items: center;
  min-height: 24px;
}

.study-base-context {
  padding: 8px 0;
  border-top: 1px solid var(--sp-divider);
  border-bottom: 1px solid var(--sp-divider);
}

.study-sweep-rows {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}

.study-sweep-header,
.study-sweep-row {
  display: grid;
  grid-template-columns: 28px minmax(0, 1.5fr) minmax(110px, 0.6fr) 32px;
  align-items: stretch;
  gap: 6px;
  min-width: 0;
}

.study-sweep-header {
  min-height: 20px;
  color: var(--sp-muted);
  font-size: var(--sp-font-caption);
  font-weight: 650;
}

.study-sweep-row > .q-checkbox {
  grid-column: 1;
}

.study-sweep-key {
  grid-column: 2;
  min-width: 0;
}

.study-sweep-values {
  grid-column: 3;
  min-width: 0;
}

.study-sweep-row > .q-btn {
  grid-column: 4;
  width: 32px;
  min-width: 32px;
  padding: 0;
}

.study-sweep-key .q-field__native,
.study-sweep-values .q-field__native,
.study-sweep-values .q-field__input {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.product-root .study-sweep-key.q-select.q-field--dense .q-field__control {
  height: 48px !important;
  min-height: 48px !important;
  align-items: stretch;
}

.product-root .study-sweep-key.q-select .q-field__native {
  height: auto !important;
  min-height: 0 !important;
  align-content: center;
  align-items: center;
  flex-wrap: wrap !important;
  overflow: visible !important;
  line-height: 1.25;
  white-space: normal !important;
}

.product-root .study-sweep-key.q-select .q-field__native > span {
  display: block;
  min-width: 0;
  overflow: visible !important;
  overflow-wrap: anywhere;
  text-overflow: clip !important;
  white-space: normal !important;
}

.study-sweep-key .q-select__focus-target {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
}

.product-root .study-sweep-key.q-select .q-field__append {
  height: 48px;
  min-height: 48px;
}

.study-sweep-values .q-field__control,
.study-sweep-row > .q-btn {
  min-height: 48px;
  height: 48px;
}

.study-sweep-key .q-field__native {
  font-size: var(--sp-font-body-sm);
}

.study-sweep-key .q-field__control {
  min-width: 0;
}

.study-builder-footer {
  align-items: center;
  justify-content: space-between;
  width: 100%;
  gap: 8px;
}

.study-metric-strip,
.artifacts-metric-strip,
.reference-metric-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  width: 100%;
  border-top: 1px solid var(--sp-divider);
  border-bottom: 1px solid var(--sp-divider);
}

.study-metric-strip-compact {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.study-variant-table {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  width: 100%;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}

.study-variant-table-header,
.study-variant-table-row {
  display: grid;
  grid-template-columns: minmax(130px, 0.9fr) minmax(150px, 1fr) 84px 36px;
  align-items: center;
  gap: 10px;
  width: 100%;
}

.study-variant-table-header,
.study-queue-table-header {
  min-height: 34px;
  padding: 0 12px;
  border-bottom: 1px solid var(--sp-border);
  background: var(--sp-surface-subtle);
  color: var(--sp-muted);
  font-size: var(--sp-font-caption);
  font-weight: 650;
}

.study-variant-table-body {
  flex: 1 1 auto;
  width: 100%;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
}

.study-variant-table-row {
  min-height: 58px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--sp-divider);
}

.study-variant-table-row:hover,
.study-queue-table-row:hover {
  background: var(--sp-surface-subtle);
}

.study-preview-empty,
.study-saved-empty,
.study-detail-empty {
  align-items: center;
  min-height: 62px;
  padding: 14px;
  color: var(--sp-subtle);
}

.study-preview-empty {
  border-bottom: 1px solid var(--sp-divider);
}

.study-preview-empty .q-icon,
.study-saved-empty .q-icon,
.study-detail-empty .q-icon {
  flex: 0 0 auto;
  font-size: var(--sp-icon-lg);
}

.study-variant-id {
  color: var(--sp-ink-strong);
  font-family: var(--sp-font-mono);
  font-size: var(--sp-font-body-sm);
  font-weight: 650;
}

.study-variant-label,
.study-variant-changes,
.study-saved-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.study-variant-changes {
  min-width: 0;
  overflow: hidden;
  color: var(--sp-ink);
  font-family: var(--sp-font-mono);
  font-size: var(--sp-font-caption);
  line-height: 1.35;
  overflow-wrap: anywhere;
  text-overflow: ellipsis;
  white-space: normal;
}

.study-impact-count {
  color: var(--sp-warning);
  font-size: var(--sp-font-body-sm);
  font-weight: 650;
}

.study-review-header {
  flex: 0 0 auto;
  padding: 14px 14px 10px;
  border-bottom: 1px solid var(--sp-divider);
}

.study-review-body {
  flex: 1 1 auto;
  min-height: 0;
  padding: 12px 14px;
  overflow-x: hidden;
  overflow-y: auto;
}

.study-review-body .study-metric-strip {
  margin-bottom: 12px;
}

.study-review-context {
  padding: 4px 0 12px;
  border-bottom: 1px solid var(--sp-divider);
  margin-bottom: 12px;
}

.study-review-actions {
  display: grid;
  flex: 0 0 auto;
  grid-template-columns: 1fr;
  gap: 8px;
  padding: 12px 14px 14px;
  border-top: 1px solid var(--sp-border);
  background: var(--sp-surface);
}

.study-review-actions .q-btn {
  width: 100%;
}

.study-saved-layout {
  grid-template-columns: minmax(280px, 330px) minmax(0, 1fr);
}

.study-saved-master {
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.study-saved-list {
  flex: 1 1 auto;
  width: 100%;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
}

.study-saved-row {
  min-width: 0;
  padding: 11px 14px;
  border-bottom: 1px solid var(--sp-divider);
  cursor: pointer;
}

.study-saved-row:hover {
  background: var(--sp-surface-subtle);
}

.study-saved-row-active {
  box-shadow: inset 3px 0 0 var(--sp-accent);
  background: var(--sp-surface-selected);
}

.study-saved-name {
  color: var(--sp-ink-strong);
  font-size: var(--sp-font-body);
  font-weight: 650;
}

.study-saved-status,
.study-queue-status {
  color: var(--sp-muted);
  font-size: var(--sp-font-caption);
  font-weight: 650;
  text-transform: uppercase;
}

.study-queue-detail {
  display: flex;
  flex-direction: column;
  border-right: 0;
  overflow: hidden;
}

.study-selected-actions {
  flex: 0 0 auto;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
  min-height: 50px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--sp-border);
}

.study-queue-table {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-height: 0;
  border: 0;
  overflow: hidden;
}

.study-queue-table-header,
.study-queue-table-row {
  display: grid;
  grid-template-columns:
    minmax(220px, 1.2fr)
    minmax(76px, 0.4fr)
    minmax(88px, 0.38fr)
    minmax(86px, 0.4fr)
    minmax(170px, 0.9fr);
  align-items: center;
  gap: 12px;
  padding-right: 14px;
  padding-left: 14px;
}

.study-queue-table-body {
  flex: 1 1 auto;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
}

.study-queue-table-row {
  min-height: 58px;
  min-width: 0;
  padding-top: 8px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--sp-divider);
  color: var(--sp-ink);
  font-size: var(--sp-font-body-sm);
}

.study-command-dialog {
  width: min(900px, calc(100vw - 80px));
  max-width: 900px;
  padding: 0;
  border: 1px solid var(--sp-border);
  border-radius: var(--sp-radius-panel);
  background: var(--sp-surface);
  box-shadow: var(--sp-shadow-dialog);
}

.study-command-dialog-header {
  align-items: center;
  min-height: 54px;
  padding: 10px 12px 10px 16px;
  border-bottom: 1px solid var(--sp-border);
}

.study-command-full {
  max-height: min(520px, calc(100vh - 180px));
  margin: 14px;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.parameter-review-empty-icon {
  color: var(--sp-subtle);
  font-size: var(--sp-icon-2xl);
}
"""
