"""Desktop portrait and constrained-desktop adaptations."""

from __future__ import annotations


RESPONSIVE_CSS = """
@media (min-width: 1500px) {
  .product-page-content {
    padding-right: 0;
    padding-left: 0;
  }

  .runs-page-layout {
    grid-template-columns: minmax(340px, 390px) minmax(0, 1fr);
  }
}

@media (max-width: 1180px) {
  .product-sidebar-footer {
    flex-direction: row !important;
    justify-content: center;
    gap: 2px !important;
  }

  .product-sidebar-footer .product-nav-button {
    flex: 1 1 0;
    height: 40px !important;
    min-height: 40px !important;
  }

  .product-sidebar-footer .product-nav-button .q-btn__content > span:not(.q-icon) {
    display: none !important;
  }

  :root {
    --sp-sidebar-width: 120px;
  }

  .product-sidebar {
    padding-right: 6px;
    padding-left: 6px;
  }

  .product-sidebar-brand {
    justify-content: center;
    padding: 0 4px;
  }

  .product-brand-lockup {
    justify-content: center;
  }

  .product-brand-wordmark {
    font-size: var(--sp-font-heading-sm);
  }

  .product-nav-section-label {
    height: 18px;
    padding: 0 6px;
    overflow: hidden;
    font-size: var(--sp-font-xs);
    line-height: 18px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .product-nav-button {
    height: 58px;
    min-height: 58px !important;
    padding: 6px 4px !important;
  }

  .product-nav-button .q-btn__content {
    flex-direction: column;
    justify-content: center;
    gap: 4px;
  }

  .product-nav-button .q-btn__content > span:not(.q-icon) {
    width: 100%;
    overflow: visible;
    font-size: var(--sp-font-sm);
    line-height: 1.15;
    text-align: center;
    text-overflow: clip;
    white-space: normal;
  }

  .product-nav-active::before {
    top: 9px;
    bottom: 9px;
  }

  .product-page-content {
    padding: 12px 0;
  }

  .workspace-page-grid {
    grid-template-columns: minmax(230px, 0.72fr) minmax(420px, 1.28fr);
  }

  .workspace-detail-stack {
    grid-column: 1 / -1;
    border-top: 1px solid var(--sp-border);
  }

  .runs-page-layout {
    grid-template-columns: minmax(260px, 310px) minmax(0, 1fr);
  }

  .runs-master-detail {
    grid-template-columns: minmax(260px, 300px) minmax(0, 1fr);
  }

  .runs-overview-grid {
    grid-template-columns: minmax(0, 1fr);
    min-height: 0;
  }

  .runs-overview-details {
    border-right: 0;
    border-bottom: 1px solid var(--sp-border);
  }

  .config-inspector {
    grid-template-columns: minmax(0, 1fr);
  }

  .run-setup-body-grid {
    grid-template-columns: minmax(0, 1fr) minmax(300px, 340px);
  }

  .run-setup-layout {
    grid-template-columns: 202px minmax(520px, 1fr);
    grid-template-rows: minmax(0, 1fr) auto;
  }

  .run-setup-commandbar {
    gap: 8px;
  }

  .run-setup-base-control {
    flex: 1 1 auto;
  }

  .run-setup-base-select {
    width: min(38vw, 360px);
    min-width: 220px;
  }

  .run-setup-form-pane {
    border-right: 0;
  }

  .run-setup-review {
    grid-column: 1 / -1;
    display: block;
    padding: 8px 10px;
    border-top: 1px solid var(--sp-border);
  }

  .run-setup-review > .run-setup-review-details {
    display: none;
  }

  .run-setup-launch-policy {
    grid-template-columns: max-content 220px minmax(0, 1fr);
    align-items: center;
    gap: 8px;
    margin-bottom: 8px;
    padding-bottom: 8px;
  }

  .run-setup-continue-notice {
    margin-top: 0;
  }

  .run-setup-review-actions {
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 8px;
    margin: 0;
    padding: 0;
    border: 0;
  }

  .run-setup-primary-action {
    margin-top: 0;
  }

  .study-build-layout {
    grid-template-columns: minmax(300px, 0.8fr) minmax(390px, 1.2fr);
    grid-template-rows: minmax(0, 1fr) auto;
  }

  .study-build-review {
    grid-column: 1 / -1;
    display: block;
    padding: 8px 10px;
    border-top: 1px solid var(--sp-border);
  }

  .study-build-review > .study-review-header,
  .study-build-review > .study-review-body {
    display: none;
  }

  .study-review-actions {
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 8px;
    padding: 0;
    border: 0;
    background: transparent;
  }

  .study-saved-layout {
    grid-template-columns: minmax(245px, 280px) minmax(0, 1fr);
  }

  .study-queue-table-header,
  .study-queue-table-row {
    grid-template-columns:
      minmax(190px, 1.2fr)
      minmax(68px, 0.4fr)
      minmax(76px, 0.36fr)
      minmax(80px, 0.38fr);
  }

  .study-queue-table-header > :nth-child(5),
  .study-queue-table-row > :nth-child(5) {
    display: none;
  }

  .genome-commandbar {
    grid-template-columns:
      max-content
      minmax(190px, 1fr)
      minmax(160px, 190px)
      auto
      34px;
  }

  .genome-main {
    grid-template-columns: minmax(0, 1fr) minmax(250px, 270px);
  }

  .genome-runtime-status {
    max-width: 180px;
  }

  .genome-status-locus,
  .genome-status-reference {
    display: none;
  }

  .artifacts-workbench,
  .reference-workbench {
    grid-template-columns: minmax(260px, 0.72fr) minmax(420px, 1.28fr);
    height: auto;
    max-height: none;
    overflow: visible;
  }

  .artifacts-master-detail {
    grid-template-columns: minmax(250px, 290px) minmax(0, 1fr);
  }

  .reference-summary {
    grid-template-columns: minmax(340px, 0.8fr) minmax(0, 1.2fr);
  }

  .settings-body {
    grid-template-columns: minmax(320px, 0.8fr) minmax(0, 1.2fr);
  }

  .artifacts-master-header,
  .artifacts-master-row {
    grid-template-columns: minmax(140px, 1fr) 58px 64px;
  }

  .artifacts-master-header > :nth-child(2),
  .artifacts-master-row > :nth-child(2) {
    display: none;
  }

  .artifacts-summary-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 8px;
  }

  .artifacts-summary-row .artifacts-metric-strip {
    width: 100%;
    max-width: none;
  }

  .artifacts-status-filter {
    justify-self: end;
    width: min(220px, 100%);
  }

  .artifacts-status-filter .q-btn {
    width: 100%;
  }

  .artifacts-log-panel,
  .reference-diagnostics-panel {
    grid-column: 1 / -1;
    min-height: 420px !important;
    border-top: 1px solid var(--sp-border) !important;
  }

  .run-log-source-select {
    flex: 1 1 auto;
    width: auto;
    min-width: 240px;
  }

  .run-log-invocation-summary {
    grid-template-columns: repeat(2, minmax(140px, 1fr));
  }

  .run-log-summary-item:nth-child(2) {
    border-right: 0;
  }

  .run-log-summary-item:nth-child(-n + 2) {
    border-bottom: 1px solid var(--sp-divider);
  }

  .product-root .runs-doc .run-log-layout {
    grid-template-columns: minmax(180px, 210px) minmax(0, 1fr);
  }

  .product-root .runs-layout {
    grid-template-columns: 248px minmax(0, 1fr);
  }
}

@media (max-width: 980px) {
  .product-topbar {
    padding-right: 8px;
    padding-left: 10px;
  }

  .product-topbar-workspace {
    width: min(40vw, 320px);
    min-width: 160px;
  }

  .product-page-heading {
    padding: 0 14px;
  }

  .product-page-title {
    font-size: var(--sp-font-title-sm);
  }

  .workspace-page-grid,
  .runs-page-layout,
  .run-setup-body-grid,
  .artifacts-workbench,
  .reference-workbench {
    height: auto;
    max-height: none;
  }

  .workspace-page-grid {
    grid-template-columns: minmax(210px, 0.68fr) minmax(360px, 1.32fr);
  }

  .workspace-directory-layout {
    grid-template-columns: minmax(360px, 1.25fr) minmax(280px, 0.75fr);
  }

  .workspace-table-header,
  .workspace-table-row {
    grid-template-columns: minmax(220px, 1fr) minmax(150px, 0.7fr);
  }

  .workspace-table-header > :nth-child(3),
  .workspace-table-row > :nth-child(3) {
    display: none;
  }

  .artifacts-master-detail {
    grid-template-columns: minmax(230px, 270px) minmax(0, 1fr);
  }

  .reference-summary {
    grid-template-columns: minmax(0, 1fr);
  }

  .reference-summary-metrics {
    border-right: 0;
    border-bottom: 1px solid var(--sp-border);
  }

  .settings-body {
    grid-template-columns: minmax(280px, 0.78fr) minmax(0, 1.22fr);
  }

  .runs-page-layout {
    grid-template-columns: minmax(240px, 290px) minmax(0, 1fr);
    min-height: calc(100vh - var(--sp-topbar-height) - var(--sp-page-header-height) - 24px);
  }

  .runs-master-detail {
    grid-template-columns: minmax(240px, 290px) minmax(0, 1fr);
  }

  .runs-toolbar-count {
    display: none;
  }

  .run-setup-body-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .run-setup-commandbar {
    gap: 6px;
  }

  .run-setup-inline-label {
    display: none;
  }

  .run-setup-base-select {
    width: min(43vw, 330px);
    min-width: 190px;
  }

  .run-setup-layout {
    grid-template-columns: 202px minmax(0, 1fr);
  }

  .run-setup-config-stack {
    border-right: 0;
  }

  .run-setup-side-stack {
    border-top: 1px solid var(--sp-border);
  }

  .run-log-sourcebar > .product-small-muted,
  .run-log-inline-label {
    display: none;
  }

  .run-log-source-select {
    min-width: 0;
  }

  .run-log-layout {
    grid-template-columns: minmax(200px, 230px) minmax(0, 1fr);
  }

  .form-grid-tags,
  .form-grid-three {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
"""
