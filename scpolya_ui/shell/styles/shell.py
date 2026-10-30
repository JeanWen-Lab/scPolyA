"""Desktop product shell, navigation and help drawer."""

from __future__ import annotations


SHELL_CSS = """
.product-shell {
  display: grid;
  grid-template-columns: var(--sp-sidebar-width) minmax(0, 1fr);
  width: 100%;
  height: 100vh;
  min-height: 0;
  overflow: hidden;
}

.product-shell > * {
  min-width: 0;
  min-height: 0;
}

/* ---------- sidebar ---------- */
.product-sidebar {
  position: relative;
  z-index: 40;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  width: var(--sp-sidebar-width);
  height: 100vh;
  min-height: 0;
  padding: 14px 10px 10px;
  overflow: hidden;
  border-right: 1px solid var(--sp-border);
  background: var(--sp-sidebar);
  color: var(--sp-sidebar-ink);
  gap: 0 !important;
}

.product-sidebar-brand {
  flex: 0 0 auto;
  align-items: center;
  gap: 9px;
  padding: 4px 8px 14px;
  flex-wrap: nowrap;
}

.product-brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 6px;
  background: var(--sp-ink);
  color: var(--sp-surface);
  font-family: var(--sp-font-serif);
  font-size: 12px;
  font-weight: 600;
  line-height: 1;
}

.product-brand-wordmark {
  color: var(--sp-sidebar-ink);
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 0.1px;
  line-height: 1;
}

.product-workspace-card {
  flex: 0 0 auto;
  gap: 0 !important;
  margin: 0 2px 10px;
  padding: 6px 10px 4px;
  border: 1px solid var(--sp-border);
  border-radius: 9px;
  background: var(--sp-sidebar-raised);
}

.product-workspace-card-label {
  color: var(--sp-subtle);
  font-size: 10.5px;
  line-height: 14px;
}

.product-workspace-card-empty {
  padding: 4px 0 6px;
  color: var(--sp-muted);
  font-size: var(--sp-font-body-sm);
}

.product-workspace-select .q-field__control {
  height: 26px;
  min-height: 26px !important;
  padding: 0;
  background: transparent;
}

.product-workspace-select .q-field__control::before,
.product-workspace-select .q-field__control::after {
  display: none;
}

.product-workspace-select .q-field__native,
.product-workspace-select .q-field__input {
  min-height: 26px;
  padding: 0;
  overflow: hidden;
  color: var(--sp-ink-strong);
  font-size: var(--sp-font-body);
  font-weight: 500;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.product-workspace-select .q-field__append,
.product-workspace-select .q-field__marginal {
  height: 26px;
  color: var(--sp-subtle);
}

.product-sidebar-nav {
  flex: 1 1 auto;
  gap: 0 !important;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
  scrollbar-width: thin;
}

.product-nav-group {
  width: 100%;
  gap: 1px !important;
  margin-bottom: 6px;
}

.product-nav-section-label {
  padding: 10px 10px 4px;
  color: var(--sp-subtle);
  font-size: 11px;
  font-weight: 400;
  letter-spacing: 0.04em;
  line-height: 14px;
}

.product-nav-button {
  justify-content: flex-start;
  width: 100%;
  height: 32px;
  min-height: 32px !important;
  padding: 0 10px !important;
  border-radius: 7px !important;
  color: var(--sp-muted) !important;
  background: transparent !important;
  font-size: var(--sp-font-body) !important;
  font-weight: 400 !important;
}

.product-nav-button::before {
  display: none;
}

.product-nav-button .q-btn__content {
  justify-content: flex-start;
  flex-wrap: nowrap;
  width: 100%;
  gap: 9px;
}

.product-nav-button .q-icon {
  flex: 0 0 18px;
  width: 18px;
  margin: 0 !important;
  color: inherit;
  font-size: 17px;
  opacity: 0.8;
}

.product-nav-button .q-btn__content > span:not(.q-icon) {
  min-width: 0;
  overflow: hidden;
  color: inherit;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.product-nav-button:hover {
  background: var(--sp-sidebar-hover) !important;
  color: var(--sp-ink) !important;
}

.product-nav-active {
  color: var(--sp-ink-strong) !important;
  background: var(--sp-sidebar-active) !important;
  box-shadow: 0 0 0 1px var(--sp-border);
  font-weight: 500 !important;
}

.product-nav-active .q-icon {
  opacity: 1;
}

.product-sidebar-footer {
  flex: 0 0 auto;
  gap: 1px !important;
  padding-top: 8px;
  border-top: 1px solid var(--sp-border);
}

/* ---------- main column ---------- */
.product-main {
  display: grid;
  grid-template-rows: var(--sp-topbar-height) minmax(0, 1fr);
  width: 100%;
  height: 100vh;
  min-width: 0;
  min-height: 0;
  padding: 0;
  overflow: hidden;
  background: var(--sp-bg);
  gap: 0 !important;
}

.product-topbar {
  position: relative;
  z-index: 35;
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  height: var(--sp-topbar-height);
  min-height: var(--sp-topbar-height);
  gap: 12px;
  padding: 0 16px 0 24px;
  border-bottom: 1px solid var(--sp-border);
  background: var(--sp-bg);
  flex-wrap: nowrap;
}

.product-topbar-context {
  flex: 1 1 auto;
  align-items: center;
  min-width: 0;
  gap: 7px;
  flex-wrap: nowrap;
  font-size: 12.5px;
}

.product-crumb {
  min-width: 0;
  overflow: hidden;
  color: var(--sp-subtle);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.product-crumb-link {
  cursor: pointer;
}

.product-crumb-link:hover {
  color: var(--sp-ink);
  text-decoration: underline;
}

.product-crumb-sep {
  color: var(--sp-disabled);
}

.product-crumb-current {
  color: var(--sp-ink);
  font-weight: 500;
  white-space: nowrap;
}

.product-topbar-controls {
  flex: 0 0 auto;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
  flex-wrap: nowrap;
}

.product-locale-toggle {
  border: 1px solid var(--sp-border);
  border-radius: 8px;
  overflow: hidden;
  background: var(--sp-surface);
}

.product-locale-toggle .q-btn {
  min-height: 28px !important;
  padding: 0 10px !important;
  border-radius: 0 !important;
  color: var(--sp-muted) !important;
  background: transparent !important;
  font-size: 12px !important;
}

.product-locale-toggle .q-btn.q-btn--active,
.product-locale-toggle .q-btn[aria-pressed="true"] {
  color: var(--sp-surface) !important;
  background: var(--sp-ink) !important;
}

.product-topbar-icon {
  width: 32px !important;
  min-width: 32px !important;
  height: 32px !important;
  min-height: 32px !important;
  padding: 0 !important;
  border-radius: 8px !important;
  color: var(--sp-muted) !important;
  background: transparent !important;
}

.product-topbar-icon:hover {
  background: var(--sp-control-hover) !important;
}

.product-topbar-icon .q-icon {
  font-size: var(--sp-icon-xs);
}

.product-page {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  margin: 0;
  padding: 0;
  overflow: hidden;
}

.product-page-heading {
  flex: 0 0 auto;
  width: 100%;
  min-width: 0;
  margin: 0;
  padding: 22px 28px 14px;
  gap: 5px !important;
  background: transparent;
}

.product-page-title {
  min-width: 0;
  overflow: hidden;
  color: var(--sp-ink-strong);
  font-family: var(--sp-font-serif);
  font-size: 24px;
  font-weight: 600;
  letter-spacing: -0.2px;
  line-height: 1.2;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.product-page-subtitle {
  max-width: 860px;
  color: var(--sp-muted);
  font-size: var(--sp-font-body);
  line-height: 1.5;
}

.product-page > :not(.product-page-heading) {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
}

/* ---------- help drawer ---------- */
.product-help-scrim {
  position: fixed;
  inset: 0;
  z-index: 3000;
  display: none;
  background: var(--sp-overlay);
}

.product-help-drawer {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  z-index: 3001;
  display: flex;
  flex-direction: column;
  width: 400px;
  border-left: 1px solid var(--sp-border);
  background: var(--sp-surface);
  box-shadow: -20px 0 50px rgba(0, 0, 0, 0.08);
  transform: translateX(105%);
  transition: transform 0.18s ease;
}

body.product-help-open .product-help-scrim {
  display: block;
}

body.product-help-open .product-help-drawer {
  transform: none;
}

.product-help-header {
  align-items: center;
  padding: 14px 14px 12px 20px;
  border-bottom: 1px solid var(--sp-border);
  flex-wrap: nowrap;
}

.product-help-title {
  flex: 1 1 auto;
  font-family: var(--sp-font-serif);
  font-size: 16px;
  font-weight: 600;
}

.product-help-body {
  flex: 1 1 auto;
  gap: 0 !important;
  padding: 14px 20px 28px;
  overflow-y: auto;
}

.product-help-intro {
  padding: 10px 12px;
  border: 1px solid var(--sp-info-line);
  border-radius: 10px;
  background: var(--sp-info-soft);
  color: var(--sp-ink);
  font-size: 12.5px;
  line-height: 1.55;
}

.product-help-term {
  gap: 3px !important;
  padding-top: 14px;
}

.product-help-term-title {
  font-weight: 600;
}

.product-help-term-detail {
  color: var(--sp-muted);
  font-size: 12.5px;
  line-height: 1.6;
}

.product-help-section {
  margin: 22px 0 8px;
  font-weight: 600;
}

.product-help-shortcut {
  align-items: center;
  gap: 10px;
  padding: 4px 0;
  font-size: 12.5px;
}

.product-kbd {
  min-width: 26px;
  padding: 0 6px;
  border: 1px solid var(--sp-border);
  border-radius: 4px;
  background: var(--sp-surface-muted);
  color: var(--sp-muted);
  font-family: var(--sp-font-mono);
  font-size: 11px;
  text-align: center;
}
"""
