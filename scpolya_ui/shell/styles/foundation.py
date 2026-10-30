"""Base typography, controls, tables, and shared UI primitives."""

from __future__ import annotations


FOUNDATION_CSS = """
html,
body,
#app,
.q-layout,
.q-page-container,
.q-page,
.nicegui-content {
  width: 100%;
  min-width: 0;
  min-height: 0;
}

html,
body {
  margin: 0;
  overflow: hidden;
  background: var(--sp-bg);
}

body,
button,
input,
textarea,
select {
  font-family: var(--sp-font-sans);
  letter-spacing: 0 !important;
}

* {
  box-sizing: border-box;
}

.nicegui-content {
  padding: 0 !important;
  align-items: stretch;
}

.product-root {
  width: 100%;
  height: 100vh;
  min-height: 0;
  overflow: hidden;
  background: var(--sp-bg);
  color: var(--sp-ink);
  font-family: var(--sp-font-sans);
  font-size: var(--sp-font-base);
}

.product-root *,
.q-menu *,
.q-dialog * {
  letter-spacing: 0 !important;
}

.product-root a {
  color: var(--sp-info);
}

.product-root .q-focus-helper {
  opacity: 0 !important;
}

.product-root .q-btn {
  min-height: 34px;
  border-radius: var(--sp-radius-control);
  padding: 0 12px;
  color: var(--sp-ink);
  font-size: var(--sp-font-body);
  font-weight: 600;
  line-height: 1;
  box-shadow: none;
}

.product-root .q-btn--round {
  width: 34px;
  min-width: 34px;
  height: 34px;
  min-height: 34px;
  padding: 0;
  border-radius: var(--sp-radius-control);
}

.product-root .q-btn--outline::before {
  border-color: var(--sp-border-strong);
}

.product-root .q-btn--outline:hover {
  background: var(--sp-control-hover);
}

.product-root .q-btn--flat:hover {
  background: var(--sp-control-hover);
}

.product-root .q-btn.bg-teal {
  background: var(--sp-accent) !important;
  color: #ffffff !important;
}

.product-root .q-btn.bg-teal:hover {
  background: var(--sp-accent-hover) !important;
}

.product-root .q-btn.text-red {
  color: var(--sp-danger) !important;
}

.product-root .q-btn:focus-visible,
.product-keyboard-selectable:focus-visible,
.path-row-copy:focus-visible {
  outline: 0;
  box-shadow: var(--sp-focus-ring);
}

/* The borderless workspace select has no focus border of its own; ring its control. */
.product-root .product-workspace-card .q-field--focused .q-field__control {
  box-shadow: var(--sp-focus-ring);
}

.product-skip-link {
  position: absolute;
  left: 12px;
  top: -48px;
  z-index: 3000;
  padding: 8px 12px;
  border-radius: var(--sp-radius-control);
  background: var(--sp-surface);
  color: var(--sp-ink) !important;
  font-weight: 600;
  text-decoration: none;
  box-shadow: var(--sp-focus-ring);
}

.product-skip-link:focus {
  top: 8px;
}

.product-page:focus {
  outline: none;
}

.product-root .q-field {
  min-width: 0;
  color: var(--sp-ink);
  background: transparent;
}

.product-root .q-field__control {
  min-height: 36px;
  height: 36px;
  border-radius: var(--sp-radius-control);
  background: var(--sp-control-bg);
  color: var(--sp-ink);
  padding-left: 10px;
  padding-right: 8px;
}

.product-root .q-field--dense .q-field__control,
.product-root .q-field--dense .q-field__marginal,
.q-dialog .q-field--dense .q-field__control,
.q-dialog .q-field--dense .q-field__marginal {
  height: 36px !important;
  min-height: 36px !important;
}

.product-root .q-textarea .q-field__control {
  height: auto;
  min-height: 72px;
}

.product-root .q-field__native,
.product-root .q-field__input {
  min-width: 0;
  color: var(--sp-ink-strong);
  font-size: var(--sp-font-body);
  font-weight: 450;
  line-height: 1.35;
}

.product-root .q-select .q-field__native {
  flex-wrap: nowrap !important;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.product-root .q-select .q-field__native > span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.product-root .q-field__label {
  color: var(--sp-muted);
  font-size: var(--sp-font-body);
  font-weight: 500;
}

.product-root .q-field__marginal,
.product-root .q-field__append,
.product-root .q-field__prepend {
  height: 36px;
  color: var(--sp-muted);
}

.product-root .q-field--outlined .q-field__control::before {
  border-color: var(--sp-border-strong);
  border-width: 1px;
}

.product-root .q-field--outlined .q-field__control:hover::before {
  border-color: #7f919a;
}

.product-root .q-field--focused .q-field__control::after {
  border-color: var(--sp-info);
  border-width: 2px;
}

.compact-field-row {
  display: grid;
  grid-template-columns: minmax(88px, 118px) minmax(0, 1fr);
  align-items: center;
  gap: 10px;
  width: 100%;
  min-width: 0;
  min-height: 36px;
}

.compact-field-label {
  min-width: 0;
  overflow: hidden;
  color: var(--sp-muted);
  font-size: var(--sp-font-body);
  font-weight: 550;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.compact-field-control {
  min-width: 0;
}

.product-root .q-checkbox {
  min-height: 30px;
  color: var(--sp-ink);
}

.product-root .q-checkbox__label {
  color: var(--sp-ink);
  font-size: var(--sp-font-body);
}

.product-root .q-checkbox__bg {
  border-color: var(--sp-border-strong);
  border-radius: 3px;
}

.product-root .q-checkbox__inner--truthy .q-checkbox__bg {
  border-color: var(--sp-accent);
  background: var(--sp-accent);
}

.q-menu {
  border: 1px solid #cad3d8;
  border-radius: var(--sp-radius-panel);
  box-shadow: var(--sp-shadow-menu);
}

.q-menu .q-item {
  min-height: 34px;
  font-family: var(--sp-font-sans);
  font-size: var(--sp-font-body);
}

.q-menu .q-item--active,
.q-menu .q-item:hover {
  color: #075d58;
  background: #e8f4f3;
}

.product-panel,
.product-panel-muted {
  width: 100%;
  min-width: 0;
  padding: 16px;
  border: 1px solid var(--sp-border);
  border-radius: var(--sp-radius-panel);
  background: var(--sp-surface);
  box-shadow: none;
}

.product-panel-muted {
  background: var(--sp-surface-subtle);
}

.product-section-title {
  color: var(--sp-ink-strong);
  font-size: var(--sp-font-base);
  font-weight: 650;
  line-height: 1.25;
}

.product-small-muted {
  color: var(--sp-muted);
  font-size: var(--sp-font-body-sm);
  line-height: 1.4;
}

.data-column-numeric {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.data-column-ratio-heading {
  text-align: center;
}

.data-column-ratio {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
  align-items: center;
  width: 100%;
  min-width: 0;
  font-variant-numeric: tabular-nums;
}

.data-column-ratio > * {
  min-width: 0;
  line-height: inherit;
  white-space: nowrap;
}

.data-column-ratio-leading {
  text-align: right;
}

.data-column-ratio-separator {
  padding: 0 2px;
  text-align: center;
}

.data-column-ratio-trailing {
  text-align: left;
}

.product-status-ok {
  color: var(--sp-success) !important;
}

.product-status-missing {
  color: var(--sp-danger) !important;
}

.workspace-path {
  min-width: 0;
  color: var(--sp-muted);
  font-family: var(--sp-font-mono);
  font-size: var(--sp-font-caption);
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.workspace-path-compact {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.workspace-metric-row {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  width: 100%;
  border-top: 1px solid var(--sp-divider);
  border-bottom: 1px solid var(--sp-divider);
}

.workspace-metric {
  min-width: 0;
  gap: 2px !important;
  padding: 10px 12px;
  border-right: 1px solid var(--sp-divider);
}

.workspace-metric:last-child {
  border-right: 0;
}

.workspace-metric-value {
  color: var(--sp-ink-strong);
  font-size: var(--sp-font-heading-sm);
  font-weight: 650;
  line-height: 1.1;
}

.compact-meta-row,
.workspace-detail-row,
.reference-kv-row {
  display: grid;
  grid-template-columns: minmax(112px, 0.32fr) minmax(0, 1fr);
  align-items: start;
  gap: 12px;
  width: 100%;
  min-width: 0;
  padding: 7px 0;
  border-bottom: 1px solid var(--sp-divider);
}

.compact-meta-label,
.workspace-detail-key,
.reference-kv-label {
  color: var(--sp-muted);
  font-size: var(--sp-font-body-sm);
  font-weight: 500;
}

.compact-meta-value,
.workspace-detail-value,
.reference-kv-value {
  min-width: 0;
  color: var(--sp-ink);
  font-size: var(--sp-font-body-sm);
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.command-console {
  width: 100%;
  max-width: 100%;
  margin: 0;
  padding: 12px;
  overflow: auto;
  border: 1px solid var(--sp-border);
  border-radius: var(--sp-radius-control);
  background: #10191e;
  color: #d9e6e9;
  font-family: var(--sp-font-mono);
  font-size: var(--sp-font-caption);
  line-height: 1.55;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.product-root .q-table__container,
.product-root .q-table__card,
.product-root .q-table,
.q-dialog .q-table__container,
.q-dialog .q-table__card,
.q-dialog .q-table {
  border-radius: var(--sp-radius-control);
  background: var(--sp-surface);
  color: var(--sp-ink);
  box-shadow: none;
}

.product-root .q-table th,
.q-dialog .q-table th {
  height: 36px;
  padding: 0 12px;
  border-bottom: 1px solid var(--sp-border-strong);
  background: var(--sp-surface-subtle);
  color: var(--sp-muted);
  font-size: var(--sp-font-caption);
  font-weight: 650;
  text-transform: none;
}

.product-root .q-table td,
.q-dialog .q-table td {
  height: 40px;
  padding: 6px 12px;
  border-color: var(--sp-divider);
  color: var(--sp-ink);
  font-size: var(--sp-font-body-sm);
}

.product-root .q-expansion-item {
  border-top: 1px solid var(--sp-divider);
}

.product-root .q-expansion-item__container > .q-item {
  min-height: 40px;
  padding: 0 4px;
  color: var(--sp-ink);
  font-size: var(--sp-font-body-sm);
}

.product-confirm-dialog {
  width: min(500px, calc(100vw - 48px));
  padding: 22px;
  border: 1px solid var(--sp-border);
  border-radius: var(--sp-radius-panel);
  background: var(--sp-surface);
  color: var(--sp-ink);
  box-shadow: var(--sp-shadow-dialog);
}

.product-confirm-heading {
  align-items: flex-start;
  gap: 12px;
}

.product-confirm-icon {
  color: var(--sp-accent);
  font-size: var(--sp-icon-lg);
}

.product-confirm-title {
  color: var(--sp-ink-strong);
  font-size: var(--sp-font-heading);
  font-weight: 650;
}

.product-confirm-message {
  color: var(--sp-muted);
  font-size: var(--sp-font-body);
  line-height: 1.5;
}

.product-confirm-details {
  width: 100%;
  border-top: 1px solid var(--sp-divider);
}

.product-confirm-detail-row {
  display: grid;
  grid-template-columns: 130px minmax(0, 1fr);
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px solid var(--sp-divider);
}

.product-confirm-detail-label {
  color: var(--sp-muted);
  font-size: var(--sp-font-body-sm);
}

.product-confirm-detail-value {
  min-width: 0;
  color: var(--sp-ink);
  font-family: var(--sp-font-mono);
  font-size: var(--sp-font-caption);
  overflow-wrap: anywhere;
}

.product-confirm-actions,
.setup-actions {
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
}

.path-row-list {
  display: flex;
  flex-direction: column;
  width: 100%;
  min-width: 0;
}

.path-row {
  display: grid;
  grid-template-columns: minmax(140px, 0.8fr) minmax(110px, 0.48fr) minmax(0, 1fr) 34px;
  align-items: center;
  gap: 10px;
  min-width: 0;
  min-height: 42px;
  padding: 7px 8px;
  border-bottom: 1px solid var(--sp-divider);
}

.path-row:hover {
  background: var(--sp-surface-subtle);
}

.path-row-meta {
  display: contents;
}

.path-row-name {
  min-width: 0;
  color: var(--sp-ink-strong);
  font-size: var(--sp-font-body-sm);
  font-weight: 550;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.path-row-status {
  min-width: 0;
  overflow: hidden;
  color: var(--sp-muted);
  font-size: var(--sp-font-caption);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.path-row-status-ready {
  color: var(--sp-success);
}

.path-row-status-missing {
  color: var(--sp-danger);
}

.path-row-path,
.path-row-value {
  min-width: 0;
  overflow: hidden;
  color: var(--sp-muted);
  font-family: var(--sp-font-mono);
  font-size: var(--sp-font-sm);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.path-row-path {
  width: 100%;
  text-overflow: ellipsis;
}

.path-row-copy {
  justify-self: end;
  align-self: center;
  width: 28px;
  height: 28px;
  border: 0;
  border-radius: var(--sp-radius-control);
  background: transparent;
  color: var(--sp-muted);
  cursor: pointer;
}

.path-row-copy:hover {
  background: var(--sp-control-hover);
  color: var(--sp-ink);
}
"""
