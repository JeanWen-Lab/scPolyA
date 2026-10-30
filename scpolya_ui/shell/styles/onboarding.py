"""First-run guide."""

ONBOARDING_CSS = """
.ob-dialog {
  width: min(860px, 94vw);
  max-width: 860px !important;
  padding: 0 !important;
  overflow: hidden;
  border-radius: 14px !important;
}

.ob-layout {
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr);
  width: 100%;
  min-height: 460px;
}

.ob-rail {
  padding: 26px 22px;
  border-right: 1px solid var(--sp-border);
  background: var(--sp-surface-muted);
}

.ob-rail-title {
  margin-bottom: 6px;
  color: var(--sp-ink-strong);
  font-family: var(--sp-font-serif);
  font-size: 22px;
}

.ob-rail-detail {
  margin-bottom: 20px;
  color: var(--sp-muted);
  font-size: 12.5px;
  line-height: 1.6;
}

.ob-step {
  align-items: center;
  gap: 10px;
  padding: 7px 0;
  color: var(--sp-subtle);
  font-size: 13px;
  flex-wrap: nowrap;
}

.ob-step-dot {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border: 1.5px solid var(--sp-border-strong);
  border-radius: 50%;
  font-size: 10.5px;
}

.ob-step-on {
  color: var(--sp-ink-strong);
  font-weight: 500;
}

.ob-step-on .ob-step-dot {
  border-color: var(--sp-accent);
  background: var(--sp-accent);
  color: var(--sp-surface);
}

.ob-step-ok .ob-step-dot {
  border-color: var(--sp-good-dot);
  background: var(--sp-good-dot);
  color: #fff;
}

.ob-panel {
  align-items: stretch !important;
  min-width: 0;
  padding: 26px 28px 20px;
}

.ob-title {
  color: var(--sp-ink-strong);
  font-size: 17px;
  font-weight: 600;
}

.ob-detail {
  margin-top: -6px;
  color: var(--sp-muted);
  font-size: 13px;
  line-height: 1.6;
}

.ob-choice-group {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.ob-choice {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  padding: 12px 14px;
  border: 1px solid var(--sp-border);
  border-radius: 10px;
  background: var(--sp-surface);
  cursor: pointer;
}

.ob-choice:hover,
.ob-choice:focus-visible {
  background: var(--sp-control-hover);
}

.ob-choice-on {
  border-color: var(--sp-accent);
  box-shadow: inset 0 0 0 1px var(--sp-accent);
}

.ob-radio {
  flex: none;
  width: 16px;
  height: 16px;
  margin-top: 2px;
  border: 1.5px solid var(--sp-subtle);
  border-radius: 50%;
}

.ob-choice-on .ob-radio {
  border: 5px solid var(--sp-accent);
}

.ob-choice-title-row {
  align-items: center;
  gap: 8px;
}

.ob-choice-title {
  color: var(--sp-ink-strong);
  font-size: 13.5px;
  font-weight: 600;
}

.ob-choice-detail {
  color: var(--sp-muted);
  font-size: 12.5px;
  line-height: 1.55;
}

.ob-hint {
  margin-top: -6px;
  font-size: 12px;
}

.ob-hint-good {
  color: var(--sp-success);
}

.ob-hint-warn {
  color: var(--sp-warning);
}

.ob-checks {
  border: 1px solid var(--sp-border);
  border-radius: 10px;
  padding: 2px 12px;
}

.ob-check {
  display: grid;
  grid-template-columns: 76px 140px minmax(0, 1fr);
  align-items: center;
  gap: 8px;
  min-height: 32px;
  border-bottom: 1px solid var(--sp-divider);
  font-size: 12.5px;
}

.ob-check:last-child {
  border-bottom: 0;
}

.ob-check-path {
  overflow: hidden;
  color: var(--sp-subtle);
  font-family: var(--sp-font-mono);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ob-panel > .df-path-row .df-path-input {
  min-width: 0;
}

.ob-actions {
  align-items: center;
  padding-top: 8px;
}
"""
