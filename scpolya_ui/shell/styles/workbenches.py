"""Workspace, runs, and configuration workbench styles."""

from __future__ import annotations


WORKBENCHES_CSS = """
.product-page-content {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: 12px;
  width: 100%;
  min-width: 0;
  min-height: 0;
  padding: 14px 0 20px;
  overflow: auto;
  background: var(--sp-surface);
}

.workbench-toolbar {
  flex: 0 0 auto;
  align-items: center;
  min-height: 46px;
  gap: 8px;
  padding: 6px 10px;
  border: 0;
  border-bottom: 1px solid var(--sp-border);
  background: var(--sp-surface);
  flex-wrap: nowrap;
}

.workbench-toolbar-title {
  color: var(--sp-ink-strong);
  font-size: var(--sp-font-base);
  font-weight: 650;
  line-height: 1.2;
}

.workbench-toolbar-spacer {
  flex: 1 1 auto;
  min-width: 8px;
}

.workspace-workbench {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-height: 0;
  gap: 0;
}

.workspace-directory-layout {
  display: grid;
  flex: 1 1 auto;
  grid-template-columns: minmax(520px, 1.45fr) minmax(330px, 0.75fr);
  width: 100%;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  border: 0;
  background: var(--sp-surface);
}

.workspace-directory-table,
.workspace-inspector {
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
}

.workspace-directory-table {
  border-right: 1px solid var(--sp-border);
}

.workspace-inspector {
  padding: 16px;
}

.workspace-mapping-expansion {
  margin-top: 4px;
  border-bottom: 1px solid var(--sp-divider);
}

.workspace-mapping-expansion .q-expansion-item__content {
  padding: 8px 2px 14px;
}

.workspace-add-dialog {
  width: min(620px, calc(100vw - 48px));
  gap: 12px !important;
}

.workspace-add-dialog .compact-field-row {
  grid-template-columns: 138px minmax(0, 1fr);
}

.workspace-path-control {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.workspace-path-input {
  min-width: 0;
}

.workspace-path-picker {
  width: min(760px, calc(100vw - 48px));
  max-width: none !important;
  max-height: min(720px, calc(100vh - 64px));
  gap: 0 !important;
  padding: 0;
  overflow: hidden;
  border: 1px solid var(--sp-border);
  border-radius: var(--sp-radius-panel);
  background: var(--sp-surface);
  color: var(--sp-ink);
  box-shadow: var(--sp-shadow-dialog);
}

.workspace-path-picker .q-btn {
  min-height: 34px;
  border-radius: var(--sp-radius-control);
  color: var(--sp-ink);
  font-size: var(--sp-font-body);
  font-weight: 600;
  box-shadow: none;
}

.workspace-path-picker .q-checkbox__label,
.workspace-path-picker .q-field__native,
.workspace-path-picker .q-field__input {
  color: var(--sp-ink);
  font-size: var(--sp-font-body);
}

.workspace-path-picker-header {
  flex: 0 0 auto;
  align-items: flex-start;
  gap: 12px;
  padding: 18px 20px 14px;
  border-bottom: 1px solid var(--sp-border);
}

.workspace-path-picker-icon {
  color: var(--sp-accent);
  font-size: var(--sp-icon-lg);
}

.workspace-path-picker-title {
  color: var(--sp-ink-strong);
  font-size: var(--sp-font-heading);
  font-weight: 650;
}

.workspace-path-picker-subtitle {
  color: var(--sp-muted);
  font-size: var(--sp-font-body-sm);
}

.workspace-path-picker-body {
  min-height: 0;
  gap: 0 !important;
}

.workspace-path-picker-toolbar {
  flex: 0 0 auto;
  align-items: center;
  gap: 4px;
  min-height: 44px;
  padding: 5px 12px;
  border-bottom: 1px solid var(--sp-divider);
  flex-wrap: nowrap;
}

.workspace-path-picker-location-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 36px;
  flex: 0 0 auto;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
}

.workspace-path-picker-location {
  min-width: 0;
}

.workspace-path-picker-error {
  flex: 0 0 auto;
  align-items: center;
  gap: 8px;
  padding: 6px 12px 10px;
}

.workspace-path-picker-list {
  flex: 1 1 auto;
  min-height: 220px;
  max-height: min(430px, calc(100vh - 330px));
  overflow-y: auto;
  border-top: 1px solid var(--sp-divider);
  border-bottom: 1px solid var(--sp-divider);
}

.workspace-path-picker-row {
  display: grid;
  grid-template-columns: 24px minmax(0, 1fr) 24px;
  align-items: center;
  gap: 10px;
  min-height: 50px;
  padding: 7px 14px;
  border-bottom: 1px solid var(--sp-divider);
  cursor: pointer;
}

.workspace-path-picker-row:last-child {
  border-bottom: 0;
}

.workspace-path-picker-row:hover {
  background: var(--sp-surface-subtle);
}

.workspace-path-picker-entry-icon {
  color: var(--sp-muted);
  font-size: var(--sp-icon-md);
}

.workspace-path-picker-row-state_db .workspace-path-picker-entry-icon,
.workspace-path-picker-row-state_db .workspace-path-picker-entry-action {
  color: var(--sp-accent);
}

.workspace-path-picker-entry-copy {
  min-width: 0;
}

.workspace-path-picker-entry-name {
  min-width: 0;
  overflow: hidden;
  color: var(--sp-ink-strong);
  font-size: var(--sp-font-body);
  font-weight: 550;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.workspace-path-picker-entry-kind,
.workspace-path-picker-empty {
  color: var(--sp-muted);
  font-size: var(--sp-font-caption);
}

.workspace-path-picker-empty {
  display: block;
  padding: 20px 14px;
}

.workspace-path-picker-entry-action {
  color: var(--sp-subtle);
  font-size: var(--sp-icon-xs);
}

.workspace-path-picker-actions {
  flex: 0 0 auto;
  align-items: center;
  gap: 8px;
  padding: 12px;
  flex-wrap: nowrap;
}

.workspace-path-picker-current {
  max-width: 330px;
}

.workspace-page-grid {
  display: grid;
  grid-template-columns: minmax(250px, 0.7fr) minmax(440px, 1.35fr) minmax(300px, 0.85fr);
  gap: 0;
  width: 100%;
  min-width: 0;
  min-height: 620px;
  border: 1px solid var(--sp-border);
  background: var(--sp-surface);
}

.workspace-page-grid > * {
  min-width: 0;
  min-height: 0;
  padding: 16px;
  border-right: 1px solid var(--sp-border);
}

.workspace-page-grid > *:last-child {
  border-right: 0;
}

.workspace-tools-stack,
.workspace-detail-stack {
  align-content: flex-start;
}

.workspace-page-grid .product-panel,
.workspace-page-grid .product-panel-muted {
  padding: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
}

.workspace-page-grid .product-panel + .product-panel,
.workspace-page-grid .product-panel + .product-panel-muted,
.workspace-page-grid .product-panel-muted + .product-panel {
  padding-top: 16px;
  border-top: 1px solid var(--sp-border);
}

.workspace-register-panel .q-field {
  width: 100%;
}

.workspace-register-submit {
  align-self: flex-start;
}

.workspace-summary-title {
  min-width: 0;
  color: var(--sp-ink-strong);
  font-size: var(--sp-font-section);
  font-weight: 650;
  line-height: 1.25;
  overflow-wrap: anywhere;
}

.workspace-status-pill {
  color: var(--sp-muted);
  font-size: var(--sp-font-caption);
  font-weight: 600;
  white-space: nowrap;
}

.workspace-table {
  width: 100%;
  min-width: 0;
  border: 0;
}

.workspace-table-header,
.workspace-table-row {
  display: grid;
  grid-template-columns: minmax(240px, 1.5fr) minmax(190px, 0.85fr) minmax(120px, 0.6fr);
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.workspace-table-header {
  min-height: 36px;
  padding: 0 12px;
  border-bottom: 1px solid var(--sp-border-strong);
  background: var(--sp-surface-subtle);
  color: var(--sp-muted);
  font-size: var(--sp-font-caption);
  font-weight: 650;
}

.workspace-table-row {
  min-height: 58px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--sp-divider);
  cursor: pointer;
}

.workspace-table-row:last-child {
  border-bottom: 0;
}

.workspace-table-row:hover {
  background: var(--sp-surface-subtle);
}

.workspace-table-row-active {
  box-shadow: inset 3px 0 0 var(--sp-accent);
  background: var(--sp-surface-selected);
}

.workspace-table-primary,
.workspace-table-title-line {
  min-width: 0;
}

.workspace-table-title {
  min-width: 0;
  overflow: hidden;
  color: var(--sp-ink-strong);
  font-size: var(--sp-font-body);
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.workspace-status-text,
.workspace-status-inline {
  flex: 0 0 auto;
  font-size: var(--sp-font-sm);
  font-weight: 600;
}

.workspace-empty-state {
  align-items: flex-start;
  justify-content: center;
  min-height: 180px;
  padding: 20px;
  border: 1px dashed var(--sp-border-strong);
}

.workspace-mapping-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 8px;
}

.workspace-mapping-submit {
  justify-self: start;
}

.workspace-mapping-list {
  max-height: 260px;
  overflow-y: auto;
}

.workspace-mapping-row {
  padding-bottom: 8px;
  border-bottom: 1px solid var(--sp-divider);
}

.product-page-content-runs,
.product-page-content-workspaces {
  overflow: hidden;
}

.runs-workbench {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-height: 0;
  gap: 0;
}

.runs-toolbar-search {
  flex: 0 1 300px;
  width: min(26vw, 300px);
  min-width: 190px;
}

.runs-toolbar-filter {
  flex: 0 0 150px;
  width: 150px;
}

.runs-toolbar-count {
  color: var(--sp-muted);
  font-size: var(--sp-font-caption);
  white-space: nowrap;
}

.runs-master-detail {
  display: grid;
  flex: 1 1 auto;
  grid-template-columns: minmax(360px, 410px) minmax(0, 1fr);
  width: 100%;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  border: 0;
  background: var(--sp-surface);
}

.runs-master-detail.runs-master-detail-collapsed {
  grid-template-columns: 0 minmax(0, 1fr);
}

.runs-master-detail-collapsed .runs-master {
  display: none;
}

.runs-master-detail-collapsed .runs-detail {
  grid-column: 2;
}

.runs-master,
.runs-detail {
  min-width: 0;
  min-height: 0;
}

.runs-master {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border-right: 1px solid var(--sp-border);
}

.runs-master-header {
  display: flex;
  flex: 0 0 36px;
  align-items: center;
  height: 36px;
  padding: 0 10px;
  border-bottom: 1px solid var(--sp-border-strong);
  background: var(--sp-surface-subtle);
  color: var(--sp-muted);
  font-size: var(--sp-font-sm);
  font-weight: 650;
}

.runs-rail-toggle {
  flex: 0 0 30px;
  width: 30px;
  min-width: 30px !important;
  height: 30px;
  min-height: 30px !important;
  color: var(--sp-muted) !important;
}

.runs-master-scroll {
  flex: 1 1 auto;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
}

.runs-master-row {
  display: block;
  min-width: 0;
  min-height: 66px;
  padding: 9px 10px;
  border-bottom: 1px solid var(--sp-divider);
  cursor: pointer;
}

.runs-master-row:hover {
  background: var(--sp-surface-subtle);
}

.runs-master-row-active {
  box-shadow: inset 3px 0 0 var(--sp-accent);
  background: var(--sp-surface-selected);
}

.runs-master-primary {
  width: 100%;
  min-width: 0;
}

.runs-master-row-heading {
  display: grid;
  grid-template-columns: minmax(0, 1fr) max-content;
  align-items: start;
  gap: 10px;
  min-width: 0;
}

.runs-master-row-heading .runs-status-text {
  padding-top: 1px;
}

.runs-master-row-meta {
  align-items: center;
  justify-content: space-between;
  min-width: 0;
  gap: 10px;
  margin-top: 5px;
  flex-wrap: nowrap;
}

.runs-master-name {
  min-width: 0;
  overflow: hidden;
  color: var(--sp-ink-strong);
  font-size: var(--sp-font-body-sm);
  font-weight: 600;
  line-height: 1.3;
  overflow-wrap: anywhere;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.runs-master-id {
  min-width: 0;
  overflow: hidden;
  color: var(--sp-muted);
  font-family: var(--sp-font-mono);
  font-size: var(--sp-font-sm);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.runs-master-hash {
  flex: 0 0 auto;
  color: var(--sp-muted);
  font-family: var(--sp-font-mono);
  font-size: var(--sp-font-sm);
  line-height: 1.25;
}

.runs-master-time {
  min-width: 0;
  overflow: hidden;
  color: var(--sp-muted);
  font-family: var(--sp-font-mono);
  font-size: var(--sp-font-sm);
  line-height: 1.25;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.runs-master-summary {
  margin-top: 3px;
  color: var(--sp-subtle);
  font-size: var(--sp-font-sm);
  line-height: 1.25;
}

.runs-master-attempt-count {
  text-align: center;
}

.runs-master-cell,
.runs-status-text {
  min-width: 0;
  overflow: hidden;
  color: var(--sp-muted);
  font-size: var(--sp-font-sm);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.runs-master-artifacts {
  font-family: var(--sp-font-mono);
}

.runs-status-text {
  font-weight: 600;
}

.runs-status-success {
  color: var(--sp-success) !important;
}

.runs-status-danger {
  color: var(--sp-danger) !important;
}

.runs-status-info {
  color: var(--sp-info) !important;
}

.runs-status-neutral {
  color: var(--sp-muted) !important;
}

.runs-detail {
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.runs-detail-header {
  flex: 0 0 64px;
  align-items: center;
  min-height: 64px;
  gap: 10px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--sp-divider);
}

.runs-detail-status-icon {
  flex: 0 0 auto;
  font-size: var(--sp-icon-xl);
}

.runs-detail-title {
  min-width: 0;
  overflow: hidden;
  color: var(--sp-ink-strong);
  font-size: var(--sp-font-heading);
  font-weight: 650;
  line-height: 1.2;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.runs-attempt-select {
  flex: 0 1 320px;
  width: 320px;
  min-width: 220px;
}

.runs-attempt-table {
  border-top: 1px solid var(--sp-border-strong);
}

.runs-attempt-header,
.runs-attempt-row {
  display: grid;
  grid-template-columns: minmax(190px, 1fr) 86px 122px 72px;
  align-items: center;
  gap: 10px;
  min-width: 0;
  padding: 0 10px;
}

.runs-attempt-header {
  height: 36px;
  border-bottom: 1px solid var(--sp-border-strong);
  background: var(--sp-surface-subtle);
  color: var(--sp-muted);
  font-size: var(--sp-font-sm);
  font-weight: 650;
}

.runs-attempt-row {
  min-height: 54px;
  border-bottom: 1px solid var(--sp-divider);
  cursor: pointer;
}

.runs-attempt-row:hover {
  background: var(--sp-surface-subtle);
}

.runs-attempt-row-active {
  box-shadow: inset 3px 0 0 var(--sp-accent);
  background: var(--sp-surface-selected);
}

.runs-attempt-primary {
  min-width: 0;
}

.runs-attempt-name {
  color: var(--sp-ink-strong);
  font-size: var(--sp-font-body-sm);
  font-weight: 600;
}

.runs-attempt-updated {
  overflow: hidden;
  color: var(--sp-muted);
  font-family: var(--sp-font-mono);
  font-size: var(--sp-font-sm);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.runs-detail-empty {
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--sp-subtle);
}

.workbench-tabs {
  flex: 0 0 40px;
  min-height: 40px;
  padding: 0 8px;
  border-bottom: 1px solid var(--sp-border);
  background: var(--sp-surface);
  color: var(--sp-muted);
}

.workbench-tabs .q-tab {
  min-height: 40px;
  padding: 0 14px;
  color: var(--sp-muted);
  font-size: var(--sp-font-body-sm);
  font-weight: 550;
}

.workbench-tabs .q-tab--active {
  color: var(--sp-accent-ink);
}

.workbench-tabs .q-tab__indicator {
  height: 2px;
  background: var(--sp-accent) !important;
}

.workbench-tab-panels {
  flex: 1 1 auto;
  min-height: 0;
  overflow: hidden;
  border-radius: 0 !important;
  background: var(--sp-surface);
  color: var(--sp-ink);
}

.workbench-tab-panels .q-panel,
.workbench-tab-panels .q-tab-panel {
  width: 100%;
  height: 100%;
  min-height: 0;
}

.workbench-tab-panels .q-tab-panel {
  padding: 0;
  overflow-y: auto;
}

.runs-overview-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(280px, 0.72fr);
  align-content: start;
  width: 100%;
  min-height: 100%;
}

.runs-overview-details,
.runs-readiness {
  min-width: 0;
  padding: 18px;
}

.runs-overview-details {
  border-right: 1px solid var(--sp-border);
}

.runs-overview-heading {
  align-items: center;
  min-height: 30px;
  margin-bottom: 8px;
  gap: 8px;
}

.runs-overview-heading .product-section-title {
  margin: 0;
}

.runs-continue-button {
  flex: 0 0 auto;
}

.runs-overview-details > .product-section-title,
.runs-readiness > .product-section-title {
  margin-bottom: 8px;
}

.runs-meta-row {
  grid-template-columns: minmax(104px, 0.32fr) minmax(0, 1fr);
}

.runs-command-expansion {
  margin-top: 8px;
}

.runs-process-panel {
  width: 100%;
  min-width: 0;
  margin-top: 14px;
  padding-top: 10px;
  border-top: 1px solid var(--sp-divider);
}

.runs-process-heading {
  align-items: center;
  min-height: 30px;
  gap: 8px;
}

.runs-process-heading .product-section-title {
  margin: 0;
}

.runs-process-refresh {
  flex: 0 0 28px;
  width: 28px;
  min-width: 28px !important;
  height: 28px;
  min-height: 28px !important;
  color: var(--sp-muted) !important;
}

.runs-process-table {
  width: 100%;
  min-width: 0;
  margin-top: 4px;
  border-top: 1px solid var(--sp-border-strong);
}

.runs-process-header,
.runs-process-row {
  display: grid;
  grid-template-columns: 92px 62px 62px 78px minmax(0, 1fr);
  align-items: center;
  gap: 8px;
  min-width: 0;
  padding: 0 8px;
}

.runs-process-header {
  min-height: 30px;
  border-bottom: 1px solid var(--sp-border-strong);
  background: var(--sp-surface-subtle);
  color: var(--sp-muted);
  font-size: var(--sp-font-sm);
  font-weight: 650;
}

.runs-process-row {
  min-height: 34px;
  border-bottom: 1px solid var(--sp-divider);
  color: var(--sp-ink);
  font-size: var(--sp-font-sm);
}

.runs-process-role,
.runs-process-state,
.runs-process-command {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.runs-process-role {
  color: var(--sp-ink-strong);
  font-weight: 600;
}

.runs-process-pid {
  color: var(--sp-ink);
  font-family: var(--sp-font-mono);
}

.runs-process-state {
  color: var(--sp-muted);
}

.runs-process-command {
  font-family: var(--sp-font-mono);
}

.runs-command-expansion .q-expansion-item__content {
  padding: 8px 0 0;
}

.runs-readiness-summary {
  color: var(--sp-ink-strong);
  font-size: var(--sp-font-body);
  font-weight: 600;
}

.runs-readiness-progress {
  height: 5px;
  border-radius: 0;
}

.runs-readiness-list {
  display: flex;
  flex-direction: column;
  width: 100%;
  margin-top: 4px;
  border-top: 1px solid var(--sp-divider);
}

.runs-readiness-row {
  display: grid;
  grid-template-columns: 20px minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
  min-height: 34px;
  border-bottom: 1px solid var(--sp-divider);
  font-size: var(--sp-font-caption);
}

.runs-readiness-row .q-icon {
  font-size: var(--sp-icon-compact);
}

.runs-readiness-name {
  min-width: 0;
  overflow: hidden;
  color: var(--sp-ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.runs-tab-content {
  min-height: 100%;
  padding: 18px;
}

.runs-artifact-table {
  min-height: 0;
  overflow: auto;
  border: 1px solid var(--sp-border);
}

.product-page-content-run_setup {
  overflow: hidden;
}

.run-setup-workbench {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-height: 0;
  gap: 0;
}

.run-setup-commandbar {
  flex: 0 0 50px;
  align-items: center;
  min-height: 50px;
  gap: 14px;
  padding: 6px 10px;
  border: 0;
  border-bottom: 1px solid var(--sp-border);
  background: var(--sp-surface);
  flex-wrap: nowrap;
}

.run-setup-base-control {
  align-items: center;
  min-width: 0;
  gap: 8px;
  flex-wrap: nowrap;
}

.run-setup-base-control {
  flex: 0 1 520px;
}

.run-setup-note-control {
  flex: 0 1 360px;
  align-items: center;
  min-width: 220px;
  gap: 8px;
  flex-wrap: nowrap;
}

.run-setup-note-input {
  flex: 1 1 auto;
  width: 260px;
  min-width: 0;
}

.run-setup-inline-label {
  flex: 0 0 auto;
  color: var(--sp-muted);
  font-size: var(--sp-font-body);
  font-weight: 550;
}

.run-setup-base-select {
  flex: 1 1 auto;
  width: min(36vw, 430px);
  min-width: 240px;
}

.run-setup-warning {
  flex: 0 0 auto;
  align-items: center;
  gap: 8px;
  min-height: 38px;
  padding: 7px 10px;
  border: 1px solid var(--sp-danger);
  background: var(--sp-danger-soft);
}

.run-setup-layout {
  display: grid;
  flex: 1 1 auto;
  grid-template-columns:
    210px
    minmax(560px, 1fr)
    minmax(480px, min(32vw, 1080px));
  width: 100%;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  border: 0;
  background: var(--sp-surface);
}

.run-setup-section-nav,
.run-setup-form-pane,
.run-setup-review {
  min-width: 0;
  min-height: 0;
}

.run-setup-section-nav {
  padding: 10px 8px;
  overflow-y: auto;
  border-right: 1px solid var(--sp-border);
  background: var(--sp-surface-subtle);
}

.run-setup-section-button {
  justify-content: flex-start;
  width: 100%;
  min-height: 40px !important;
  margin-bottom: 3px;
  padding: 0 10px !important;
  color: var(--sp-ink) !important;
  background: transparent !important;
  font-weight: 500 !important;
}

.run-setup-section-button .q-btn__content {
  justify-content: flex-start;
  width: 100%;
  gap: 10px;
  flex-wrap: nowrap;
}

.run-setup-section-button .q-btn__content > span:not(.q-icon) {
  min-width: 0;
  overflow: hidden;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.run-setup-section-button .q-icon {
  flex: 0 0 19px;
  width: 19px;
  color: var(--sp-muted);
  font-size: var(--sp-icon-xs);
}

.run-setup-section-active {
  box-shadow: inset 3px 0 0 var(--sp-accent);
  background: var(--sp-surface-selected) !important;
  color: var(--sp-accent-ink) !important;
  font-weight: 620 !important;
}

.run-setup-section-active .q-icon {
  color: var(--sp-accent);
}

.run-setup-form-pane {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border-right: 1px solid var(--sp-border);
}

.run-setup-form-header {
  flex: 0 0 52px;
  align-items: center;
  min-height: 52px;
  gap: 8px;
  padding: 0 18px;
  border-bottom: 1px solid var(--sp-border);
}

.run-setup-form-title {
  color: var(--sp-ink-strong);
  font-size: var(--sp-font-heading-sm);
  font-weight: 650;
}

.run-setup-form-scroll {
  flex: 1 1 auto;
  min-height: 0;
  padding: 4px 18px 24px;
  overflow-y: auto;
}

.run-setup-form-scroll .structured-form {
  display: block;
  border-top: 0;
}

.run-setup-form-scroll .form-section {
  padding: 12px 0;
  border-bottom: 0;
}

.run-setup-form-scroll .form-section-title {
  display: none;
}

.form-grid-workbench {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 620px), 1fr));
  width: 100%;
  max-width: 1800px;
  gap: 10px 18px;
  align-items: start;
}

.form-grid-workbench .form-control-long {
  grid-column: 1 / -1;
}

.run-setup-review {
  display: flex;
  flex-direction: column;
  padding: 14px 16px;
  overflow: hidden;
  background: var(--sp-surface);
}

.run-setup-review-title {
  color: var(--sp-ink-strong);
  font-size: var(--sp-font-heading-sm);
  font-weight: 650;
}

.run-setup-launch-policy {
  display: grid;
  flex: 0 0 auto;
  grid-template-columns: minmax(0, 1fr);
  gap: 6px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--sp-border);
}

.run-setup-launch-mode-label {
  color: var(--sp-muted);
  font-size: var(--sp-font-sm);
  font-weight: 650;
}

.run-setup-launch-mode {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  width: 100%;
  min-height: 34px;
  overflow: hidden;
  border: 1px solid var(--sp-border-strong);
  border-radius: var(--sp-radius-control);
}

.run-setup-launch-mode .q-btn {
  flex: 1 1 0;
  width: 100%;
  min-width: 0;
  min-height: 32px;
  border-right: 1px solid var(--sp-border);
  border-radius: 0 !important;
  color: var(--sp-muted);
  font-size: var(--sp-font-body-sm);
}

.run-setup-launch-mode .q-btn:last-child {
  border-right: 0;
}

.run-setup-launch-mode .q-btn[aria-pressed="true"],
.run-setup-launch-mode .q-btn.bg-primary {
  background: var(--sp-surface-selected) !important;
  color: var(--sp-accent-ink) !important;
}

.run-setup-continue-notice {
  display: grid;
  grid-template-columns: 20px minmax(0, 1fr);
  align-items: start;
  gap: 7px;
  margin-top: 2px;
  padding: 7px 8px;
  border-left: 3px solid var(--sp-warning);
  background: var(--sp-warning-soft);
  color: var(--sp-ink);
  font-size: var(--sp-font-sm);
  line-height: 1.35;
}

.run-setup-continue-notice .q-icon {
  color: var(--sp-warning);
  font-size: var(--sp-icon-compact);
}

.run-setup-continue-invalid {
  border-left-color: var(--sp-danger);
  background: var(--sp-danger-soft);
}

.run-setup-continue-invalid .q-icon {
  color: var(--sp-danger);
}

.run-setup-review-actions {
  display: grid;
  flex: 0 0 auto;
  grid-template-columns: 1fr;
  gap: 7px;
  margin-top: 16px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--sp-border);
}

.run-setup-review-actions .q-btn {
  width: 100%;
}

.run-setup-primary-action {
  margin-top: 2px;
}

.run-setup-review-scroll {
  flex: 1 1 auto;
  min-height: 0;
  padding-top: 6px;
  padding-right: 2px;
  overflow-x: hidden;
  overflow-y: auto;
}

.run-setup-review-expansion {
  border-bottom: 1px solid var(--sp-divider);
}

.run-setup-review-expansion .q-expansion-item__content {
  padding: 6px 0 12px;
}

.run-setup-review-expansion .q-table__middle {
  max-height: 240px;
  overflow-x: auto;
}

.run-setup-state-diagnostics {
  flex: 0 0 auto;
  width: 100%;
  padding: 8px 12px;
  border-bottom: 1px solid var(--sp-border);
  background: var(--sp-warning-soft);
}

.run-setup-state-heading {
  min-width: 0;
  min-height: 24px;
}

.run-setup-state-icon {
  flex: 0 0 auto;
  color: var(--sp-warning);
  font-size: var(--sp-icon-compact);
}

.run-setup-state-title {
  color: var(--sp-ink-strong);
  font-size: var(--sp-font-body);
  font-weight: 650;
}

.run-setup-state-list {
  display: grid;
  gap: 4px;
  margin: 4px 0 0 26px;
}

.run-setup-state-row {
  min-width: 0;
}

.run-setup-state-message {
  color: var(--sp-ink);
  font-size: var(--sp-font-body-sm);
  line-height: 1.35;
}

.run-setup-state-technical {
  width: calc(100% - 26px);
  max-width: calc(100% - 26px);
  margin: 4px 0 0 26px;
  border-top: 1px solid color-mix(in srgb, var(--sp-warning) 35%, transparent);
}

.run-setup-state-technical-text {
  margin: 0;
  color: var(--sp-muted);
  font-family: var(--sp-font-mono);
  font-size: var(--sp-font-sm);
  line-height: 1.4;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.run-setup-effective-diff-table .q-table,
.run-setup-preview-diff-table .q-table,
.run-setup-effective-keys-table .q-table {
  table-layout: fixed;
}

.run-setup-effective-diff-table .q-table th,
.run-setup-effective-diff-table .q-table td,
.run-setup-preview-diff-table .q-table th,
.run-setup-preview-diff-table .q-table td,
.run-setup-effective-keys-table .q-table th,
.run-setup-effective-keys-table .q-table td {
  min-width: 0;
  overflow-wrap: anywhere;
  white-space: normal;
}

.run-setup-effective-diff-table .q-table th:nth-child(1),
.run-setup-effective-diff-table .q-table td:nth-child(1) {
  width: 28%;
}

.run-setup-effective-diff-table .q-table th:nth-child(2),
.run-setup-effective-diff-table .q-table td:nth-child(2),
.run-setup-effective-diff-table .q-table th:nth-child(3),
.run-setup-effective-diff-table .q-table td:nth-child(3) {
  width: 25%;
}

.run-setup-effective-diff-table .q-table th:nth-child(4),
.run-setup-effective-diff-table .q-table td:nth-child(4) {
  width: 22%;
  text-align: center;
}

.run-setup-preview-diff-table .q-table th:nth-child(1),
.run-setup-preview-diff-table .q-table td:nth-child(1) {
  width: 32%;
}

.run-setup-preview-diff-table .q-table th:nth-child(2),
.run-setup-preview-diff-table .q-table td:nth-child(2),
.run-setup-preview-diff-table .q-table th:nth-child(3),
.run-setup-preview-diff-table .q-table td:nth-child(3) {
  width: 34%;
}

.run-setup-effective-keys-table .q-table th:nth-child(1),
.run-setup-effective-keys-table .q-table td:nth-child(1) {
  width: 72%;
}

.run-setup-effective-keys-table .q-table th:nth-child(2),
.run-setup-effective-keys-table .q-table td:nth-child(2) {
  width: 28%;
  text-align: center;
}

.run-setup-review-expansion .command-console {
  max-height: 250px;
}

.run-setup-preview-metrics {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  border-top: 1px solid var(--sp-divider);
  border-bottom: 1px solid var(--sp-divider);
}

.run-setup-result {
  margin: 8px 0;
  padding: 10px;
  border: 1px solid var(--sp-border);
  background: var(--sp-surface);
}

.runs-page-layout {
  display: grid;
  grid-template-columns: minmax(300px, 360px) minmax(0, 1fr);
  width: 100%;
  min-width: 0;
  min-height: 620px;
  height: 100%;
  max-height: 100%;
  overflow: hidden;
  border: 1px solid var(--sp-border);
  background: var(--sp-surface);
}

.runs-page-layout.runs-rail-closed {
  grid-template-columns: 52px minmax(0, 1fr);
}

.runs-page-layout > * {
  min-width: 0;
  min-height: 0;
}

.runs-rail {
  height: 100%;
  max-height: 100%;
  padding: 0;
  overflow: hidden;
  border: 0;
  border-right: 1px solid var(--sp-border);
  border-radius: 0;
}

.runs-rail-header {
  flex: 0 0 auto;
  align-items: center;
  justify-content: space-between;
  padding: 12px 12px 8px;
}

.runs-rail-actions {
  flex: 0 0 auto;
  gap: 6px;
  padding: 0 12px;
}

.runs-rail-actions .q-btn {
  flex: 1 1 0;
}

.runs-rail-filters {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(120px, 0.65fr);
  gap: 6px;
  padding: 0 12px 10px;
  border-bottom: 1px solid var(--sp-border);
}

.runs-rail-count {
  color: var(--sp-muted);
  font-size: var(--sp-font-caption);
}

.runs-rail-body {
  flex: 1 1 auto;
  min-height: 0;
  padding: 0 !important;
  overflow-y: auto;
}

.runs-list-column {
  gap: 0 !important;
}

.workspace-list-row,
.artifacts-run-row {
  width: 100%;
  min-width: 0;
  padding: 10px 12px;
  border-bottom: 1px solid var(--sp-divider);
  border-radius: 0;
  cursor: pointer;
}

.workspace-list-row:hover,
.artifacts-run-row:hover {
  background: var(--sp-surface-subtle);
}

.workspace-list-row-active,
.artifacts-run-row-active {
  box-shadow: inset 3px 0 0 var(--sp-accent);
  background: var(--sp-surface-selected);
}

.runs-rail-collapsed {
  overflow: hidden;
}

.runs-rail-collapsed .runs-rail-body,
.runs-rail-collapsed .runs-rail-title,
.runs-rail-collapsed .runs-rail-actions,
.runs-rail-collapsed .runs-rail-filters {
  display: none;
}

.runs-main-stack {
  height: 100%;
  max-height: 100%;
  gap: 0 !important;
  overflow-y: auto;
  background: var(--sp-surface);
}

.runs-main-stack > .product-panel {
  padding: 18px 22px;
  border: 0;
  border-bottom: 1px solid var(--sp-border);
  border-radius: 0;
}

.runs-main-stack > .product-panel:last-child {
  border-bottom: 0;
}

.runs-summary-panel {
  flex: 0 0 auto;
}

.runs-artifacts-panel {
  flex: 0 0 auto;
}

.runs-config-panel {
  flex: 1 0 auto;
}

.runs-command-console {
  max-height: 126px;
}

.runs-artifact-scroll {
  max-height: 340px;
  overflow-y: auto;
  border: 1px solid var(--sp-border);
}

.run-setup-base-panel {
  flex: 0 0 auto;
  padding: 10px 14px;
  border-radius: 0;
}

.run-setup-base-selector {
  flex: 1 1 440px;
  max-width: 680px;
}

.run-setup-body-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(330px, 390px);
  align-items: start;
  gap: 0;
  width: 100%;
  min-width: 0;
  border: 1px solid var(--sp-border);
  background: var(--sp-surface);
}

.run-setup-config-stack,
.run-setup-side-stack {
  min-width: 0;
  gap: 0 !important;
}

.run-setup-config-stack {
  border-right: 1px solid var(--sp-border);
}

.run-setup-body-grid .product-panel {
  padding: 16px;
  border: 0;
  border-radius: 0;
}

.run-setup-lower-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 0;
}

.run-setup-lower-grid > * + * {
  border-top: 1px solid var(--sp-border) !important;
}

.run-setup-action-grid,
.study-action-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.run-setup-action-grid .q-btn,
.study-action-grid .q-btn {
  width: 100%;
}

.run-setup-start-action,
.study-start-new-action {
  grid-column: 1 / -1;
}

.run-setup-dirty-indicator,
.study-builder-dirty-indicator {
  color: var(--sp-warning);
  font-size: var(--sp-font-caption);
  font-weight: 600;
}

.structured-form {
  display: grid;
  grid-template-columns: repeat(12, minmax(0, 1fr));
  gap: 0;
  width: 100%;
  border-top: 1px solid var(--sp-border);
}

.form-section {
  min-width: 0;
  padding: 16px 0;
  border-bottom: 1px solid var(--sp-divider);
}

.form-section-full {
  grid-column: span 12;
}

.form-section-half {
  grid-column: span 6;
}

.form-section-wide {
  grid-column: span 8;
}

.form-section-narrow {
  grid-column: span 4;
}

.form-section-tags {
  grid-column: span 7;
}

.form-section-filtering {
  grid-column: span 5;
}

.form-section-title,
.config-inspector-title {
  margin-bottom: 10px;
  color: var(--sp-ink-strong);
  font-size: var(--sp-font-body-sm);
  font-weight: 650;
}

.form-grid-main,
.form-grid-paths,
.form-grid-tags,
.form-grid-filtering,
.form-grid-balanced,
.form-grid-compact,
.form-grid-three {
  display: grid;
  gap: 12px;
}

.form-grid-main {
  grid-template-columns: minmax(160px, 1fr) minmax(160px, 0.7fr);
}

.form-grid-paths {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.form-grid-tags,
.form-grid-three {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.form-grid-filtering,
.form-grid-balanced,
.form-grid-compact {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.form-control {
  display: grid;
  grid-template-columns: minmax(116px, 142px) minmax(0, 1fr);
  align-items: center;
  gap: 12px;
  min-width: 0;
  min-height: 36px;
}

.form-control-long {
  grid-column: 1 / -1;
}

.form-control-label {
  min-width: 0;
  overflow: hidden;
  color: var(--sp-muted);
  font-size: var(--sp-font-body);
  font-weight: 550;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.form-control-label-stack,
.config-inspector-key-stack {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 1px;
}

.form-control-technical-key,
.config-inspector-technical-key {
  min-width: 0;
  overflow: hidden;
  color: var(--sp-subtle);
  font-family: var(--sp-font-mono);
  font-size: var(--sp-font-sm);
  line-height: 1.2;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.runtime-control-groups {
  display: flex;
  min-width: 0;
  flex-direction: column;
}

.peak-calling-control-groups {
  display: flex;
  min-width: 0;
  flex-direction: column;
}

.runtime-control-group {
  min-width: 0;
  padding: 12px 0;
  border-top: 1px solid var(--sp-divider);
}

.peak-calling-control-group {
  min-width: 0;
  padding: 12px 0;
  border-top: 1px solid var(--sp-divider);
}

.runtime-control-group:first-child {
  padding-top: 0;
  border-top: 0;
}

.peak-calling-control-group:first-child {
  padding-top: 0;
  border-top: 0;
}

.runtime-control-group-title {
  margin-bottom: 8px;
  color: var(--sp-ink-strong);
  font-size: var(--sp-font-body-sm);
  font-weight: 650;
}

.peak-calling-control-group-title {
  margin-bottom: 8px;
  color: var(--sp-ink-strong);
  font-size: var(--sp-font-body-sm);
  font-weight: 650;
}

.runtime-control-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, var(--sp-runtime-control-track-max)));
  gap: 10px 24px;
  min-width: 0;
}

.peak-calling-control-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, var(--sp-runtime-control-track-max)));
  gap: 8px 24px;
  min-width: 0;
}

.peak-calling-control-group-shared .peak-calling-control-grid {
  grid-template-columns: minmax(0, var(--sp-runtime-control-track-max));
}

.peak-calling-control-grid .form-control {
  min-height: 46px;
}

.peak-calling-control-grid .form-control-label {
  overflow: visible;
  text-overflow: clip;
  white-space: normal;
}

.runtime-control-grid .form-control {
  grid-template-columns: minmax(156px, 1fr) minmax(88px, 112px);
  min-height: 46px;
  gap: 10px;
}

.runtime-control-grid .form-control-long {
  grid-column: 1 / -1;
  grid-template-columns: minmax(156px, 220px) minmax(0, 1fr);
}

.runtime-control-grid .form-control-label {
  overflow: visible;
  text-overflow: clip;
  white-space: normal;
}

.form-control-value {
  min-width: 0;
}

.form-control-toggle {
  min-height: 36px;
}

.form-control-checkbox {
  justify-self: start;
}

.config-json-editor textarea,
.command-preview textarea {
  font-family: var(--sp-font-mono);
  font-size: var(--sp-font-caption);
}

.config-inspector {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 24px;
  width: 100%;
}

.config-inspector-section {
  min-width: 0;
  padding: 10px 0;
  border-top: 1px solid var(--sp-divider);
}

.config-inspector-rows {
  display: flex;
  flex-direction: column;
}

.config-inspector-row {
  display: grid;
  grid-template-columns: minmax(120px, 0.8fr) minmax(0, 1.2fr);
  gap: 12px;
  min-width: 0;
  padding: 6px 0;
  border-bottom: 1px solid var(--sp-divider);
}

.config-inspector-key {
  color: var(--sp-muted);
  font-size: var(--sp-font-caption);
}

.config-inspector-value {
  min-width: 0;
  color: var(--sp-ink);
  font-family: var(--sp-font-mono);
  font-size: var(--sp-font-caption);
  overflow-wrap: anywhere;
}

.config-inspector-bool-on {
  color: var(--sp-success);
}

.config-inspector-bool-off {
  color: var(--sp-muted);
}

.config-inspector-alerts,
.config-full-expansion {
  grid-column: 1 / -1;
}

.config-full-expansion .q-expansion-item__content {
  padding: 0 8px 12px;
}

/* The read-only full form lives in a narrow tab: one section per row, fields in even columns. */
.config-full-expansion .form-section {
  grid-column: 1 / -1;
}

.config-full-expansion [class*="form-grid-"],
.config-full-expansion .peak-calling-control-grid,
.config-full-expansion .runtime-control-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 340px), 1fr)) !important;
  gap: 8px 20px;
}

.config-full-expansion .form-control {
  grid-template-columns: minmax(140px, 1fr) minmax(120px, 1fr);
}

.config-diff-empty {
  min-height: 72px;
  align-content: center;
}
"""
