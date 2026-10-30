"""Genome browser and IGV-specific workbench styles."""

from __future__ import annotations


GENOME_CSS = """
.product-page-igv_compare .product-page-heading {
  display: none;
}

.product-page-content.product-page-content-igv_compare {
  gap: 0;
  padding: 0;
  overflow: hidden;
  background: var(--sp-bg);
}

.genome-workbench {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  width: 100%;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  background: var(--sp-bg);
  color: var(--sp-ink);
}

/* ---------- command bar ---------- */
.genome-commandbar {
  display: grid;
  flex: 0 0 auto;
  grid-template-columns:
    max-content
    minmax(240px, 360px)
    minmax(200px, 260px)
    max-content
    34px
    minmax(0, 1fr);
  align-items: center;
  gap: 8px;
  width: 100%;
  min-width: 0;
  padding: 12px 20px;
  border-bottom: 1px solid var(--sp-border);
  background: var(--sp-bg);
}

.genome-compare-controls {
  width: 100%;
  min-width: 0;
}

.genome-commandbar .q-field__control {
  min-height: 34px !important;
  height: 34px;
  background: var(--sp-surface);
}

.genome-commandbar .q-field__native,
.genome-commandbar .q-field__input {
  min-height: 34px;
  overflow: hidden;
  color: var(--sp-ink-strong);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.genome-commandbar .q-field__marginal {
  height: 34px;
  color: var(--sp-subtle);
}

.genome-source-toggle {
  display: inline-flex;
  min-width: 0;
  height: 34px;
  padding: 2px;
  border: 1px solid var(--sp-border);
  border-radius: 8px;
  background: var(--sp-surface);
  box-shadow: none !important;
}

.genome-source-toggle .q-btn {
  min-width: 0;
  min-height: 28px !important;
  padding: 0 12px !important;
  border-radius: 6px !important;
  color: var(--sp-muted) !important;
  background: transparent !important;
  font-size: 12.5px !important;
  font-weight: 400 !important;
  white-space: nowrap;
}

.genome-source-toggle .q-btn[aria-pressed="true"],
.genome-source-toggle .q-btn.bg-primary {
  color: var(--sp-surface) !important;
  background: var(--sp-ink) !important;
  font-weight: 500 !important;
}

.genome-context-control,
.genome-gene-control {
  width: 100%;
  min-width: 0;
}

.genome-gene-field {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.genome-inline-label {
  color: var(--sp-subtle);
  font-size: 12.5px;
  white-space: nowrap;
}

.genome-load-action .q-icon {
  font-size: 17px;
}

.genome-commandbar > .q-btn:last-child {
  width: 34px;
  min-width: 34px;
  padding: 0;
  color: var(--sp-subtle) !important;
}

/* ---------- banners ---------- */
.genome-prerequisite-banner,
.genome-stale-banner {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 8px;
  min-width: 0;
  min-height: 32px;
  padding: 6px 20px;
  border-bottom: 1px solid var(--sp-warning-line);
  background: var(--sp-warning-soft);
  color: var(--sp-warning-ink);
}

.genome-stale-banner.q-hidden,
.genome-transcript-scope-warning[hidden] {
  display: none;
}

.genome-transcript-scope-reload {
  flex: 0 0 auto;
  padding: 3px 10px;
  border: 1px solid var(--sp-warning-line);
  border-radius: 6px;
  background: var(--sp-surface, #fff);
  color: var(--sp-warning-ink);
  font: inherit;
  font-size: 12.5px;
  cursor: pointer;
}

.genome-stale-banner .q-icon,
.genome-prerequisite-icon {
  flex: 0 0 auto;
  font-size: 16px;
}

.genome-stale-message,
.genome-prerequisite-message {
  min-width: 0;
  font-size: 12.5px;
  line-height: 1.4;
  overflow-wrap: anywhere;
}

/* ---------- viewer ---------- */
.genome-main {
  display: grid;
  flex: 1 1 auto;
  grid-template-columns: minmax(0, 1fr) minmax(300px, 340px);
  width: 100%;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}

.genome-viewer {
  display: flex;
  flex-direction: column;
  width: 100%;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  border-right: 1px solid var(--sp-border);
  background: var(--sp-surface);
}

.genome-viewer-meta {
  flex: 0 0 38px;
  align-items: center;
  gap: 10px;
  min-width: 0;
  height: 38px;
  padding: 0 16px;
  border-bottom: 1px solid var(--sp-divider);
  background: var(--sp-surface);
  flex-wrap: nowrap;
}

.genome-viewer-gene {
  color: var(--sp-ink-strong);
  font-family: var(--sp-font-serif);
  font-size: 15px;
  font-weight: 600;
}

.genome-viewer-locus {
  min-width: 0;
  overflow: hidden;
  color: var(--sp-subtle);
  font-family: var(--sp-font-mono);
  font-size: 11.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.genome-viewer-error,
.genome-viewer-warning {
  min-width: 0;
  max-width: 62%;
  overflow: hidden;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.genome-viewer-error {
  color: var(--sp-danger);
}

.genome-viewer-warning {
  color: var(--sp-warning);
}

.genome-viewer-canvas {
  position: relative;
  display: flex;
  flex: 1 1 auto;
  width: 100%;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  background: var(--sp-surface);
}

.genome-viewer-canvas-empty {
  align-items: center;
  justify-content: center;
  background:
    repeating-linear-gradient(
      90deg,
      transparent 0,
      transparent 79px,
      var(--sp-divider) 79px,
      var(--sp-divider) 80px
    ),
    var(--sp-surface-subtle);
}

.genome-igv-wrapper {
  flex: 1 1 auto;
  width: 100%;
  min-width: 0;
  min-height: 0;
  overflow: auto;
  background: inherit;
}

.igv-viewer,
.genome-igv-viewer {
  width: 100%;
  min-width: 0;
  min-height: 100%;
}

.genome-viewer-canvas-ready .genome-igv-viewer {
  min-height: 560px;
}

.genome-empty-state {
  position: absolute;
  inset: 0;
  align-items: center;
  justify-content: center;
  max-width: 430px;
  margin: auto;
  padding: 32px;
  color: var(--sp-muted);
  text-align: center;
}

.genome-empty-state .q-icon {
  color: var(--sp-disabled);
  font-size: 34px;
}

.genome-empty-state .product-section-title {
  color: var(--sp-ink-strong);
  font-family: var(--sp-font-serif);
  font-size: 16px;
  font-weight: 600;
}

.genome-empty-state .product-small-muted {
  color: var(--sp-muted);
}

/* ---------- inspector ---------- */
.genome-inspector {
  display: flex;
  flex-direction: column;
  width: 100%;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  background: var(--sp-surface-subtle);
}

.genome-inspector-tabs {
  flex: 0 0 40px;
  min-height: 40px;
  padding: 0 14px;
  border-bottom: 1px solid var(--sp-border);
  background: var(--sp-surface-subtle);
}

.genome-inspector-tabs .q-tabs__content {
  gap: 18px;
}

.genome-compare-hint {
  max-width: 360px;
  color: var(--sp-warning-ink);
  font-size: 11.5px;
  line-height: 1.4;
}

.genome-missing-list {
  padding: 10px 16px 12px;
  border-bottom: 1px solid var(--sp-divider);
}

.genome-missing-title {
  color: var(--sp-muted);
  font-size: 12px;
  font-weight: 600;
}

.genome-missing-item {
  color: var(--sp-subtle);
  font-size: 11.5px;
  line-height: 1.45;
}

.genome-inspector-empty {
  padding: 14px 16px;
}

.genome-inspector-tabs .q-tab {
  min-height: 40px;
  padding: 0;
  color: var(--sp-muted);
  font-size: 12.5px;
  font-weight: 400;
  text-transform: none;
}

.genome-inspector-tabs .q-tab--active {
  color: var(--sp-ink-strong);
  font-weight: 500;
}

.genome-inspector-tabs .q-tab__indicator {
  height: 2px;
  background: var(--sp-ink);
}

.genome-inspector-panels,
.genome-inspector-panels .q-panel,
.genome-inspector-panels .q-tab-panel {
  min-height: 0;
  background: var(--sp-surface-subtle);
  color: var(--sp-ink);
}

.genome-inspector-panels {
  flex: 1 1 auto;
  overflow: hidden;
}

.genome-inspector-panels .q-panel,
.genome-inspector-panels .q-tab-panel {
  height: 100%;
}

.genome-inspector-panels .q-tab-panel {
  padding: 0;
  overflow-x: hidden;
  overflow-y: auto;
}

.genome-inspector-summary {
  padding: 14px 16px 12px;
  border-bottom: 1px solid var(--sp-divider);
}

.genome-inspector-summary .product-section-title {
  color: var(--sp-ink-strong);
  font-size: 13px;
  font-weight: 600;
}

.genome-inspector .product-small-muted {
  color: var(--sp-subtle);
}

.genome-inspector .workspace-path {
  color: var(--sp-subtle);
  font-family: var(--sp-font-mono);
  font-size: 11px;
}

.genome-inspector-note {
  display: block;
  padding: 12px 16px;
  border-bottom: 1px solid var(--sp-divider);
  line-height: 1.5;
}

.genome-compare-select .q-field__prefix {
  display: flex;
  align-items: center;
  height: 34px;
  padding-right: 8px;
  color: var(--sp-subtle);
  font-family: var(--sp-font-mono);
  font-size: 11.5px;
  font-weight: 600;
}

.genome-variant-list {
  width: 100%;
}

.genome-variant-row {
  width: 100%;
  min-width: 0;
  padding: 9px 16px;
  border-bottom: 1px solid var(--sp-divider);
}

.genome-variant-row:hover {
  background: var(--sp-control-hover);
}

.genome-variant-row .study-variant-id {
  color: var(--sp-ink-strong);
  font-family: var(--sp-font-mono);
  font-size: 12px;
  font-weight: 600;
}

.genome-ready-count {
  color: var(--sp-good-dot);
  font-size: 11.5px;
}

.genome-variant-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.genome-selection-details {
  width: 100%;
}

.genome-detail-row {
  display: grid;
  grid-template-columns: 92px minmax(0, 1fr);
  gap: 10px;
  min-width: 0;
  padding: 8px 16px;
  border-bottom: 1px solid var(--sp-divider);
}

.genome-detail-label {
  color: var(--sp-subtle);
  font-size: 12px;
}

.genome-detail-value {
  min-width: 0;
  overflow-wrap: anywhere;
  color: var(--sp-ink);
  font-size: 12.5px;
}

.genome-inspector .path-table {
  border: 0;
}

.genome-inspector .path-row {
  grid-template-columns: minmax(110px, 0.85fr) minmax(64px, 0.42fr) minmax(0, 1fr) 28px;
  border-color: var(--sp-divider);
}

.genome-inspector .path-row:hover {
  background: var(--sp-control-hover);
}

.genome-inspector .path-row-name {
  color: var(--sp-ink);
}

.genome-inspector .path-row-status,
.genome-inspector .path-row-path {
  color: var(--sp-subtle);
}

.genome-inspector .path-row-status-ready {
  color: var(--sp-good-dot);
}

.genome-inspector .path-row-status-missing {
  color: var(--sp-danger);
}

.genome-manifest-expansion {
  border-top: 1px solid var(--sp-border);
  color: var(--sp-ink);
}

/* ---------- status bar ---------- */
.genome-statusbar {
  display: flex;
  flex: 0 0 32px;
  align-items: center;
  gap: 8px;
  width: 100%;
  min-width: 0;
  height: 32px;
  padding: 0 16px;
  border-top: 1px solid var(--sp-border);
  background: var(--sp-bg);
  color: var(--sp-muted);
}

.genome-status-dot {
  flex: 0 0 auto;
  color: var(--sp-good-dot);
  font-size: 9px;
}

.genome-status-idle {
  color: var(--sp-subtle);
}

.genome-status-partial {
  color: #d39a1e;
}

.genome-status-tracks-partial .genome-status-value {
  color: var(--sp-warning-ink);
}

.genome-status-reference-missing,
.genome-status-tracks-missing,
.genome-status-locus-missing,
.genome-status-error {
  color: var(--sp-danger);
}

.genome-runtime-status {
  min-width: 0;
  max-width: 420px;
  overflow: hidden;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.genome-status-item {
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
  padding-left: 10px;
  border-left: 1px solid var(--sp-divider);
  font-size: 11.5px;
}

.genome-status-label {
  color: var(--sp-subtle);
}

.genome-status-value {
  min-width: 0;
  max-width: 180px;
  overflow: hidden;
  color: var(--sp-ink);
  font-family: var(--sp-font-mono);
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* igv.js draws its own light UI; keep it light in both themes. */
.genome-igv-viewer,
.genome-igv-viewer .igv-container,
.genome-igv-viewer .igv-root-div,
.genome-igv-viewer .igv-navbar,
.genome-igv-viewer .igv-column-container,
.genome-igv-viewer .igv-track-container,
.genome-igv-viewer .igv-viewport-div,
.genome-igv-viewer .igv-track-label,
.genome-igv-viewer .igv-trackgear-container,
.genome-igv-viewer .igv-search-container {
  color-scheme: light;
  background-color: #ffffff !important;
  color: #1d1c19 !important;
}

.genome-igv-viewer input,
.genome-igv-viewer select,
.genome-igv-viewer button {
  color-scheme: light;
  background-color: #ffffff !important;
  color: #1d1c19 !important;
}

/* Keep inner navigation available when tall transcript / optional tracks scroll. */
.genome-igv-viewer .igv-navbar {
  position: sticky;
  top: 0;
  z-index: 20;
}
.genome-track-controls { display: flex; flex-direction: column; gap: 8px; padding: 12px 16px; border-bottom: 1px solid var(--sp-divider); flex-shrink: 0; }
.genome-track-controls select, .genome-track-controls button {
  padding: 8px; border: 1px solid var(--sp-divider); border-radius: 6px;
  color: var(--sp-ink); background: var(--sp-surface);
}
#scpolya-track-choices { display: flex; flex-direction: column; gap: 8px; max-height: 220px; overflow: auto; }
#scpolya-track-choices label { display: flex; align-items: flex-start; gap: 8px; overflow-wrap: anywhere; font-size: 12px; }
#scpolya-track-choices input { margin-top: 3px; flex-shrink: 0; }
"""
