"""Small shared components: status dots, chips, callouts."""

from __future__ import annotations


COMPONENTS_CSS = """
.product-page-run_evidence .product-page-heading, .product-page-analyses .product-page-heading {display: none;}
.product-page-content.product-page-content-analyses {padding: 0; overflow-y: auto; background: var(--sp-bg);}
.analysis-form-grid {display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,320px),1fr));gap:12px;width:100%;}
.analysis-result-table {width:100%;min-width:0;max-width:100%;max-height:420px;overflow:auto;}
.product-page-content.product-page-content-run_evidence {padding: 0; overflow-y: auto; background: var(--sp-bg);}
.re-table .q-table__middle {max-height: 420px; overflow: auto;}
.re-table th {position: sticky; top: 0; z-index: 1; background: var(--sp-surface);}
.re-table td {white-space: nowrap !important;}

.sp-status {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  line-height: 1.3;
  white-space: nowrap;
}

.sp-status > i {
  flex: none;
  width: 7px;
  height: 7px;
  border-radius: 50%;
}

.sp-status-good { color: var(--sp-success); }
.sp-status-good > i { background: var(--sp-good-dot); }
.sp-status-crit { color: var(--sp-danger); }
.sp-status-crit > i { background: var(--sp-crit-dot); }
.sp-status-warn { color: var(--sp-warning); }
.sp-status-warn > i { background: var(--sp-warn-dot); }
.sp-status-run { color: var(--sp-info); }
.sp-status-run > i { background: var(--sp-info); box-shadow: 0 0 0 3px var(--sp-info-soft); }
.sp-status-idle,
.sp-status-plan { color: var(--sp-subtle); }
.sp-status-idle > i { background: var(--sp-disabled); }
.sp-status-plan > i { border: 1.5px solid var(--sp-subtle); background: transparent; }

.sp-ref {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 1px 8px;
  border: 1px dashed var(--sp-border-strong);
  border-radius: 10px;
  background: var(--sp-surface-muted);
  color: var(--sp-muted);
  font-size: 11px;
  font-weight: 400;
  white-space: nowrap;
  cursor: help;
}

.sp-ref::before {
  content: "i";
  font-family: var(--sp-font-serif);
  font-style: italic;
  font-weight: 600;
}

/* Quasar defaults that read as the old theme */
.q-btn.bg-primary { color: #fff; }
body.product-dark .q-btn.bg-primary { color: var(--sp-bg) !important; }
/* Quasar writes --q-primary inline on <body>; portals (dialogs, menus) need their own dark value. */
body.product-dark .q-dialog,
body.product-dark .q-menu { --q-primary: #ecebe6; }
body.product-dark .q-card { background: var(--sp-surface); color: var(--sp-ink); }
body.product-dark .q-menu { background: var(--sp-surface); color: var(--sp-ink); }
body.product-dark .q-field__native,
body.product-dark .q-field__input,
body.product-dark .q-field__prefix,
body.product-dark .q-field__suffix { color: var(--sp-ink); }
body.product-dark .q-field__label,
body.product-dark .q-field__native::placeholder { color: var(--sp-subtle); }
body.product-dark .q-checkbox__bg { border-color: var(--sp-subtle); }
body.product-dark .q-checkbox__svg { color: var(--sp-bg); }
.q-btn--outline { color: var(--sp-ink) !important; }
.q-btn--outline::before { border-color: var(--sp-border) !important; }
.q-btn { border-radius: 8px; letter-spacing: 0; white-space: nowrap; }
.q-btn .q-btn__content { flex-wrap: nowrap; }
.q-field--outlined .q-field__control { border-radius: 8px; }
.q-field--outlined .q-field__control::before { border-color: var(--sp-border); }
.q-tab { letter-spacing: 0; }
.q-tab--inactive { opacity: 1; }

/* ---------- terminal blocks ---------- */
.sp-terminal {
  position: relative;
  width: 100%;
  min-width: 0;
}

.sp-terminal-host {
  width: 100%;
  min-width: 0;
}

.sp-terminal-text {
  box-sizing: border-box;
  width: 100%;
  margin: 0;
  padding: 10px 40px 10px 12px;
  overflow: auto;
  border: 1px solid #23313a;
  border-radius: 8px;
  background: #10191e;
  color: #d9e6e9;
  font-family: var(--sp-font-mono);
  font-size: 11.5px;
  line-height: 1.55;
  white-space: pre-wrap;
  overflow-wrap: break-word;
  word-break: normal;
}

.sp-terminal-copy {
  position: absolute !important;
  top: 4px;
  right: 6px;
  color: #93a7ae !important;
}

.sp-terminal-copy:hover {
  color: #eef5f6 !important;
}
"""
