"""Design tokens for the scpolyA workbench theme (warm neutral, ink accent)."""

from __future__ import annotations


TOKENS_CSS = """
:root {
  color-scheme: light;
  --sp-topbar-height: 52px;
  --sp-sidebar-width: 228px;
  --sp-page-header-height: 54px;
  --sp-statusbar-height: 42px;
  --sp-runtime-control-track-max: 660px;

  --sp-bg: #f6f5f1;
  --sp-surface: #ffffff;
  --sp-surface-subtle: #faf9f6;
  --sp-surface-muted: #f1efe9;
  --sp-surface-selected: #f3f6fd;
  --sp-control-bg: #ffffff;
  --sp-control-hover: #f3f1ec;

  --sp-ink: #1d1c19;
  --sp-ink-strong: #12110f;
  --sp-muted: #55524a;
  --sp-subtle: #8f8b80;
  --sp-disabled: #b4b0a5;

  --sp-border: #e6e3da;
  --sp-border-strong: #d4d0c4;
  --sp-divider: #efede6;

  --sp-accent: #1d1c19;
  --sp-accent-hover: #33322e;
  --sp-accent-soft: #ecebe6;
  --sp-accent-ink: #1d1c19;
  --sp-info: #2a63d4;
  --sp-info-soft: #eef3fc;
  --sp-success: #0a7a0a;
  --sp-success-soft: #eaf6ea;
  --sp-warning: #8a5a00;
  --sp-warning-soft: #fff6df;
  --sp-danger: #b02a2a;
  --sp-danger-soft: #fdeeee;
  --sp-warning-line: #f4dfa6;
  --sp-warning-ink: #5e4100;
  --sp-danger-line: #f1c9c4;
  --sp-danger-ink: #7a1f17;
  --sp-info-line: #dfe7f8;

  --sp-sidebar: #f3f1ec;
  --sp-sidebar-raised: #ffffff;
  --sp-sidebar-ink: #1d1c19;
  --sp-sidebar-muted: #8f8b80;
  --sp-sidebar-hover: #ebe8e0;
  --sp-sidebar-active: #ffffff;
  --sp-sidebar-accent: #1d1c19;

  --sp-overlay: rgba(29, 28, 25, 0.32);
  --sp-shadow-menu: 0 12px 32px rgba(29, 28, 25, 0.14);
  --sp-shadow-dialog: 0 30px 80px rgba(29, 28, 25, 0.25);

  --sp-radius-control: 7px;
  --sp-radius-panel: 12px;
  --sp-focus-ring: 0 0 0 2px #ffffff, 0 0 0 4px #2a63d4;

  --sp-font-sans: "IBM Plex Sans", "Noto Sans CJK SC", "PingFang SC", "Microsoft YaHei", "Segoe UI", Roboto, sans-serif;
  --sp-font-mono: "IBM Plex Mono", "Noto Sans Mono CJK SC", "SFMono-Regular", Consolas, monospace;
  --sp-font-2xs: 8px;
  --sp-font-xs: 9px;
  --sp-font-sm: 10px;
  --sp-font-caption: 11px;
  --sp-font-body-sm: 12px;
  --sp-font-body: 13px;
  --sp-font-base: 14px;
  --sp-font-subtitle: 15px;
  --sp-font-section: 16px;
  --sp-font-heading-sm: 17px;
  --sp-font-heading: 18px;
  --sp-font-title-sm: 19px;
  --sp-font-title: 20px;

  --sp-icon-dot: 8px;
  --sp-icon-compact: 16px;
  --sp-icon-xs: 18px;
  --sp-icon-sm: 19px;
  --sp-icon-md: 20px;
  --sp-icon-lg: 22px;
  --sp-icon-xl: 24px;
  --sp-icon-2xl: 28px;
  --sp-icon-display: 30px;

  --sp-font-serif: "Noto Serif", "Noto Serif CJK SC", "Source Han Serif SC", Georgia, serif;
  --sp-good-dot: #0ca30c;
  --sp-warn-dot: #fab219;
  --sp-serious-dot: #ec835a;
  --sp-crit-dot: #d03b3b;
  --sp-agent: #6250d6;
  --sp-agent-soft: #efecfd;
  --sp-agent-border: #ddd7fb;
}

.product-root.product-dark,
body.product-dark {
  color-scheme: dark;
  --sp-bg: #161614;
  --sp-surface: #1e1e1b;
  --sp-surface-subtle: #22221f;
  --sp-surface-muted: #2a2a26;
  --sp-surface-selected: #232a38;
  --sp-control-bg: #1a1a18;
  --sp-control-hover: #2a2a26;

  --sp-ink: #ecebe6;
  --sp-ink-strong: #f7f6f2;
  --sp-muted: #c3c0b6;
  --sp-subtle: #8f8b80;
  --sp-disabled: #5f5c55;

  --sp-border: #34332f;
  --sp-border-strong: #46443f;
  --sp-divider: #2a2926;

  --sp-accent: #ecebe6;
  --sp-accent-hover: #ffffff;
  --sp-accent-soft: #2f2e2a;
  --sp-accent-ink: #ecebe6;
  --sp-info: #7ea6f0;
  --sp-info-soft: #1f2a40;
  --sp-success: #5cc95c;
  --sp-success-soft: #1b2f1b;
  --sp-warning: #f0b341;
  --sp-warning-soft: #3a2e14;
  --sp-danger: #ef7d7d;
  --sp-danger-soft: #3d2020;
  --sp-warning-line: #5a4616;
  --sp-warning-ink: #f0c869;
  --sp-danger-line: #5c2b27;
  --sp-danger-ink: #f6a3a3;
  --sp-info-line: #2c3a58;
  --q-primary: #ecebe6;

  --sp-sidebar: #1a1a18;
  --sp-sidebar-raised: #22221f;
  --sp-sidebar-ink: #ecebe6;
  --sp-sidebar-muted: #8f8b80;
  --sp-sidebar-hover: #262623;
  --sp-sidebar-active: #2a2a26;
  --sp-sidebar-accent: #ecebe6;

  --sp-overlay: rgba(0, 0, 0, 0.64);
  --sp-shadow-menu: 0 12px 32px rgba(0, 0, 0, 0.42);
  --sp-shadow-dialog: 0 22px 56px rgba(0, 0, 0, 0.52);
  --sp-focus-ring: 0 0 0 2px #161614, 0 0 0 4px #7ea6f0;
}
"""
