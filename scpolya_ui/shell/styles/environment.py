"""Environment & references page; builds on the overview card styles."""

from __future__ import annotations


ENVIRONMENT_CSS = """
.product-page-settings .product-page-heading {
  display: none;
}

.product-page-content.product-page-content-settings {
  padding: 0;
  overflow-y: auto;
  background: var(--sp-bg);
}

.env-page {
  width: 100%;
  max-width: 1360px;
  padding: 26px 34px 56px;
}

.env-subtitle {
  margin-top: 4px;
  color: var(--sp-muted);
  font-size: var(--sp-font-body);
}

.env-empty {
  padding: 24px 0;
}

.env-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(0, 1fr);
  gap: 16px;
  align-items: start;
  margin-bottom: 16px;
}

.env-file,
.env-tool,
.env-healthy-row {
  align-items: flex-start;
  gap: 10px;
  padding: 8px 0;
  border-bottom: 1px solid var(--sp-divider);
  flex-wrap: nowrap;
}

.env-tool,
.env-healthy-row {
  align-items: center;
  padding: 6px 0;
}

.env-healthy-row:last-child {
  border-bottom: 0;
}

.env-file-dot {
  flex: none;
  padding-top: 5px;
}

.env-tool .env-file-dot,
.env-healthy-row .env-file-dot {
  padding-top: 0;
}

.env-file-dot > div {
  display: none;
}

.env-file-text {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
}

.env-file-name {
  align-items: baseline;
  gap: 8px;
  color: var(--sp-ink);
  font-size: 12.5px;
  flex-wrap: nowrap;
}

.env-key {
  color: var(--sp-subtle);
  font-family: var(--sp-font-mono);
  font-size: 10.5px;
}

.env-tag {
  padding: 0 6px;
  border-radius: 4px;
  background: var(--sp-info-soft);
  color: var(--sp-info);
  font-size: 10.5px;
}

.env-file-path {
  max-width: 100%;
  margin-top: 2px;
  overflow: hidden;
  color: var(--sp-muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.env-file-missing {
  color: var(--sp-danger);
  font-size: 11.5px;
}

.env-note {
  margin-top: 10px;
  color: var(--sp-subtle);
  font-size: 11.5px;
  line-height: 1.5;
}

.env-tool-name {
  flex: 0 0 132px;
  color: var(--sp-ink);
  font-family: var(--sp-font-mono);
  font-size: 12px;
}

.env-tool-path {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  color: var(--sp-muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.env-healthy {
  margin-top: 2px;
}

.env-healthy-text {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  color: var(--sp-muted);
  font-size: 12.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.env-detail-expansion {
  margin: 2px -8px -4px;
  font-size: 12px;
}

.env-detail-expansion .q-item {
  min-height: 26px;
  padding: 0 8px;
}

.env-ref-table .ov-tr {
  grid-template-columns: minmax(0, 1.2fr) 90px 110px minmax(0, 2fr);
}
"""
