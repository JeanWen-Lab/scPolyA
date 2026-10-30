"""Agent notes panel."""

NOTES_CSS = """
.product-topbar-notes {
  position: relative;
  min-height: 30px !important;
  padding: 0 10px !important;
  border: 1px solid var(--sp-border) !important;
  border-radius: 8px !important;
  color: var(--sp-ink) !important;
  font-size: 12.5px !important;
}

.product-topbar-notes .q-icon {
  font-size: 16px !important;
}

.q-dialog__inner--right:has(> .nt-panel) {
  padding: 0 !important;
}

.q-dialog__inner--right > .nt-panel {
  height: 100vh;
  max-height: 100vh !important;
}

.nt-badge {
  background: var(--sp-danger) !important;
  font-size: 10px !important;
}

.nt-panel {
  width: 460px;
  max-width: 92vw !important;
  height: 100%;
  padding: 18px 18px 24px !important;
  overflow-y: auto;
  border-radius: 0 !important;
  background: var(--sp-surface) !important;
  gap: 12px;
}

.nt-head {
  align-items: flex-start;
  flex-wrap: nowrap;
}

.nt-title {
  color: var(--sp-ink-strong);
  font-family: var(--sp-font-serif);
  font-size: 20px;
}

.nt-subtitle,
.nt-note,
.nt-empty {
  color: var(--sp-subtle);
  font-size: 12px;
  line-height: 1.5;
}

.nt-subtitle {
  max-width: 340px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.nt-composer {
  padding: 10px;
  border: 1px solid var(--sp-border);
  border-radius: 10px;
  background: var(--sp-surface-muted);
}

.nt-composer-actions {
  align-items: center;
  flex-wrap: nowrap;
}

.nt-anchor-select {
  min-width: 0;
  max-width: 260px;
  font-size: 12px;
}

.nt-card {
  padding: 12px 12px 10px;
  border: 1px solid var(--sp-border);
  border-radius: 10px;
  background: var(--sp-surface);
}

.nt-card-attention {
  border-color: var(--sp-accent);
  box-shadow: inset 2px 0 0 var(--sp-accent);
}

.nt-card-resolved {
  opacity: 0.7;
}

.nt-card-top,
.nt-card-foot {
  align-items: center;
  gap: 8px;
  flex-wrap: nowrap;
}

.nt-author {
  align-items: center;
  gap: 6px;
  flex-wrap: nowrap;
  min-width: 0;
}

.nt-avatar {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  font-size: 10.5px;
  font-weight: 600;
}

.nt-avatar-agent {
  background: var(--sp-accent-soft);
  color: var(--sp-accent-ink);
  box-shadow: inset 0 0 0 1px var(--sp-border-strong);
}

.nt-avatar-user {
  background: var(--sp-accent);
  color: var(--sp-surface);
}

.nt-author-name {
  color: var(--sp-ink-strong);
  font-size: 12.5px;
  font-weight: 600;
}

.nt-role,
.nt-time,
.nt-read {
  color: var(--sp-subtle);
  font-size: 11px;
}

.nt-type {
  align-items: center;
  gap: 3px;
  color: var(--sp-muted);
  font-size: 11.5px;
  flex-wrap: nowrap;
}

.nt-anchor {
  align-items: center;
  gap: 5px;
  max-width: 100%;
  color: var(--sp-muted);
  font-size: 11.5px;
  flex-wrap: nowrap;
}

.nt-anchor-link {
  cursor: pointer;
}

.nt-anchor-link:hover .nt-anchor-ref {
  color: var(--sp-ink-strong);
  text-decoration: underline;
}

.nt-anchor-kind {
  flex: none;
}

.nt-anchor-ref {
  min-width: 0;
  overflow: hidden;
  font-family: var(--sp-font-mono);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.nt-body {
  color: var(--sp-ink);
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.nt-evidence {
  padding: 4px 8px;
  border-radius: 8px;
  background: var(--sp-surface-muted);
}

.nt-evidence-row {
  align-items: center;
  gap: 8px;
  min-height: 22px;
  flex-wrap: nowrap;
  font-size: 11.5px;
}

.nt-evidence-name {
  min-width: 0;
  overflow: hidden;
  font-family: var(--sp-font-mono);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.nt-evidence-state {
  flex: none;
  margin-left: auto;
}

.nt-evidence-match { color: var(--sp-success); }
.nt-evidence-changed,
.nt-evidence-missing { color: var(--sp-danger); }
.nt-evidence-unchecked { color: var(--sp-subtle); }

.nt-reply {
  margin-top: 4px;
  padding: 6px 0 0 10px;
  border-left: 2px solid var(--sp-divider);
}

.nt-choice {
  align-self: flex-start;
  margin: 2px 0;
  padding: 1px 8px;
  border-radius: 6px;
  background: var(--sp-accent-soft);
  color: var(--sp-accent-ink);
  font-size: 12px;
}

.nt-options {
  gap: 6px;
  margin-top: 4px;
}

.nt-reply-box {
  align-items: center;
  gap: 4px;
  flex-wrap: nowrap;
}

.nt-reply-input {
  flex: 1 1 auto;
  min-width: 0;
}
"""
