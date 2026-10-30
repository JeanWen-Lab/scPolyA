"""Site scoring page; builds on overview and environment styles."""

from __future__ import annotations


SITE_SCORES_CSS = """
.product-page-site_scores .product-page-heading {
  display: none;
}

.product-page-content.product-page-content-site_scores {
  padding: 0;
  overflow-y: auto;
  background: var(--sp-bg);
}

.sc-title-row {
  align-items: center;
  gap: 10px;
  flex-wrap: nowrap;
}

.sc-toolbar {
  align-items: center;
  gap: 8px;
  margin: 16px 0 14px;
  flex-wrap: nowrap;
}

.sc-toolbar-label {
  color: var(--sp-subtle);
  font-size: 12.5px;
  white-space: nowrap;
}

.sc-toolbar-gap {
  margin-left: 10px;
}

.sc-source-select {
  width: 380px;
}

.sc-result-select {
  width: 300px;
}

.sc-source-select .q-field__control,
.sc-result-select .q-field__control,
.sc-search .q-field__control {
  min-height: 32px !important;
  height: 32px;
  background: var(--sp-surface);
}

.sc-source-select .q-field__native,
.sc-result-select .q-field__native,
.sc-search .q-field__native {
  min-height: 32px;
  padding: 0;
}

.sc-source-select .q-field__marginal,
.sc-result-select .q-field__marginal,
.sc-search .q-field__marginal {
  height: 32px;
}

.sc-scorer-chip {
  padding: 3px 9px;
  border: 1px solid var(--sp-border);
  border-radius: 6px;
  background: var(--sp-surface);
  color: var(--sp-ink);
  font-size: 12px;
  font-weight: 500;
}

.sc-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr);
  gap: 16px;
  align-items: start;
  margin-bottom: 16px;
}

.sc-hist-wrap svg {
  display: block;
  width: 100%;
  height: auto;
}

.sc-hist text {
  fill: var(--sp-subtle);
  font-family: var(--sp-font-mono);
  font-size: 10px;
}

.sc-axis {
  stroke: var(--sp-border-strong);
  stroke-width: 1;
}

.sc-bar {
  fill: #2a63d4;
}

.sc-bin:hover .sc-bar {
  fill: #1d4aa8;
}

.sc-table .ov-tr {
  grid-template-columns:
    minmax(0, 1.3fr)
    minmax(0, 1.1fr)
    minmax(150px, 1fr)
    170px
    130px
    minmax(0, 1fr);
}

.sc-sub {
  overflow: hidden;
  color: var(--sp-subtle);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sc-td-score {
  align-items: center;
  gap: 8px;
  flex-wrap: nowrap;
}

.sc-score {
  flex: none;
  width: 64px;
  color: var(--sp-ink-strong);
  font-weight: 500;
}

.sc-meter {
  flex: 1 1 auto;
  max-width: 96px;
  height: 6px;
  border-radius: 3px;
  background: var(--sp-surface-muted);
  overflow: hidden;
}

.sc-meter-fill {
  height: 100%;
  border-radius: 3px;
  background: #2a63d4;
}

.sc-na {
  color: var(--sp-subtle);
}

.sc-td-seeds,
.sc-td-locate {
  color: var(--sp-muted);
}

.sc-reason {
  font-size: 12px;
}

.sc-no-rows {
  padding: 18px 16px;
  color: var(--sp-subtle);
  font-size: 12.5px;
}

.sc-search {
  width: 200px;
}

.sc-check {
  margin-left: 6px;
  color: var(--sp-muted);
  font-size: 12px;
}

.sc-pager {
  align-items: center;
  gap: 4px;
  padding: 6px 10px 6px 16px;
  border-top: 1px solid var(--sp-divider);
  flex-wrap: nowrap;
}

.sc-empty {
  align-items: flex-start;
  max-width: 640px;
  padding: 28px 0;
}

.sc-empty-icon {
  color: var(--sp-disabled);
  font-size: 30px;
}

.sc-empty-title {
  margin-top: 6px;
  color: var(--sp-ink-strong);
  font-family: var(--sp-font-serif);
  font-size: 17px;
  font-weight: 600;
}

.sc-empty-detail {
  color: var(--sp-muted);
  font-size: 13px;
  line-height: 1.6;
}

.sc-empty-action {
  margin-top: 14px;
}

.sc-alert {
  margin-top: 8px;
}

/* The sites table scrolls on its own so its header and the pager stay in view. */
.sc-table {
  max-height: max(360px, calc(100vh - 300px));
  overflow-y: auto;
}
"""
