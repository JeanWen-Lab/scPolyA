"""Browser runtime helpers shared by product pages."""

from __future__ import annotations


PRODUCT_RUNTIME_HEAD = """
<script src="/_scpolya_vendor/igv.min.js"></script>
<script>
window.scpolyaApplySavedTheme = () => {
  const root = document.querySelector('.product-root');
  if (!root) return;
  const theme = window.localStorage.getItem('scpolya-theme') || 'light';
  root.classList.toggle('product-dark', theme === 'dark');
  document.body.classList.toggle('product-dark', theme === 'dark');
  window.scpolyaSyncThemeControls && window.scpolyaSyncThemeControls();
};
window.scpolyaSetTheme = (theme) => {
  const root = document.querySelector('.product-root');
  if (!root) return;
  const next = theme === 'dark' ? 'dark' : 'light';
  window.localStorage.setItem('scpolya-theme', next);
  root.classList.toggle('product-dark', next === 'dark');
  document.body.classList.toggle('product-dark', next === 'dark');
  window.scpolyaSyncThemeControls && window.scpolyaSyncThemeControls();
};
window.scpolyaToggleTheme = () => {
  const root = document.querySelector('.product-root');
  if (!root) return;
  window.scpolyaSetTheme(root.classList.contains('product-dark') ? 'light' : 'dark');
};
window.scpolyaSyncThemeControls = () => {
  const root = document.querySelector('.product-root');
  if (!root) return;
  const isDark = root.classList.contains('product-dark');
  document.querySelectorAll('.product-theme-toggle .q-icon').forEach((icon) => {
    icon.textContent = isDark ? 'dark_mode' : 'light_mode';
  });
  document.querySelectorAll('.product-theme-toggle').forEach((button) => {
    const labels = window.scpolyaThemeLabels || {};
    button.setAttribute('aria-label', isDark ? (labels.toLight || 'Switch to light theme') : (labels.toDark || 'Switch to dark theme'));
  });
};
window.addEventListener('DOMContentLoaded', window.scpolyaApplySavedTheme);
window.scpolyaToggleHelp = (open) => {
  const next = open === undefined ? !document.body.classList.contains('product-help-open') : !!open;
  document.body.classList.toggle('product-help-open', next);
  const drawer = document.querySelector('.product-help-drawer');
  if (drawer) {
    // Hidden drawer content stays out of the Tab order; opening moves focus into it.
    drawer.inert = !next;
    if (next) setTimeout(() => (drawer.querySelector('button, [tabindex], input') || drawer).focus(), 50);
  }
};
setTimeout(() => {
  const drawer = document.querySelector('.product-help-drawer');
  if (drawer && !document.body.classList.contains('product-help-open')) drawer.inert = true;
}, 0);
document.addEventListener('keydown', (event) => {
  const target = event.target;
  const typing = target && (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName));
  if (event.key === 'Escape') {
    window.scpolyaToggleHelp(false);
  } else if (event.key === '?' && !typing && !event.ctrlKey && !event.metaKey) {
    event.preventDefault();
    window.scpolyaToggleHelp();
  }
});
setTimeout(window.scpolyaApplySavedTheme, 0);
// Keyboard activation of a row often refreshes its list; put focus back on the rebuilt row.
window.scpolyaRememberFocus = (element) => {
  const label = element && element.getAttribute('aria-label');
  if (!label) return;
  window.scpolyaRefocusLabel = label;
  setTimeout(() => { window.scpolyaRefocusLabel = null; }, 2000);
};
new MutationObserver(() => {
  const label = window.scpolyaRefocusLabel;
  if (!label || (document.activeElement && document.activeElement !== document.body)) return;
  const match = [...document.querySelectorAll('[aria-label]')].find((item) => item.getAttribute('aria-label') === label);
  if (match) {
    window.scpolyaRefocusLabel = null;
    match.focus();
  }
}).observe(document.documentElement, { childList: true, subtree: true });
// Pages set window.scpolyaUnsaved while an edit has not been saved; the browser then asks before leaving.
window.addEventListener('beforeunload', (event) => {
  if (window.scpolyaUnsaved) {
    event.preventDefault();
    event.returnValue = '';
  }
});

window.scpolyaProductClearIgv = async () => {
  window.scpolyaProductIgvGeneration = (window.scpolyaProductIgvGeneration || 0) + 1;
  const container = document.getElementById('scpolya-product-igv-viewer');
  if (window.scpolyaProductIgvBrowser) {
    try {
      if (window.igv && typeof window.igv.removeBrowser === 'function') {
        window.igv.removeBrowser(window.scpolyaProductIgvBrowser);
      } else if (typeof window.scpolyaProductIgvBrowser.remove === 'function') {
        await window.scpolyaProductIgvBrowser.remove();
      }
    } catch (error) {
      console.warn('Failed to clear product IGV browser', error);
    }
    window.scpolyaProductIgvBrowser = null;
  }
  document.getElementById('scpolya-product-igv-scope-warning')?.remove();
  if (container) container.innerHTML = '';
};
// IGV reference frames use 0-based, half-open viewport edges, which can be
// fractional after a resize or pan. The reader accepts 1-based inclusive
// integers: round outward so no visible reference base is silently dropped.
window.scpolyaProductIgvReloadLocus = (browser) => {
  // Retain the existing first-view behavior for a multi-locus IGV browser.
  const frame = browser?.referenceFrameList?.[0];
  if (!frame || typeof frame.chr !== 'string' || !/^[^\\s:]+$/.test(frame.chr) || frame.chr.toLowerCase() === 'all') return '';
  if (!Number.isFinite(frame.start) || !Number.isFinite(frame.end) || frame.end <= frame.start) return '';
  const chromosome = browser.genome?.getChromosome?.(frame.chr);
  const chr = chromosome?.name;
  const length = chromosome?.bpLength;
  if (typeof chr !== 'string' || !/^[^\\s:]+$/.test(chr) || chr.toLowerCase() === 'all') return '';
  if (!Number.isSafeInteger(length) || length < 1) return '';
  const start = Math.max(0, frame.start);
  const end = Math.min(length, frame.end);
  if (end <= start) return ''; // No reference bases intersect this view.
  return `${chr}:${Math.floor(start) + 1}-${Math.ceil(end)}`;
};
// Transcript models are a subset for one range; say so when the view leaves it.
window.scpolyaProductWatchTranscriptScope = (browser, config, say) => {
  const track = (config.tracks || []).find((t) => t.scpolyaScope);
  const container = document.getElementById('scpolya-product-igv-viewer');
  if (!track || !container) return;
  const scope = track.scpolyaScope;
  const range = `${scope.chr}:${scope.start.toLocaleString('en-US')}-${scope.end.toLocaleString('en-US')}`;
  const warning = document.createElement('div');
  warning.id = 'scpolya-product-igv-scope-warning';
  warning.className = 'genome-stale-banner genome-transcript-scope-warning';
  warning.setAttribute('role', 'status');
  const text = document.createElement('span');
  text.className = 'genome-stale-message';
  text.textContent = say('transcript_scope', 'Transcript models were loaded for {range} only; this view extends beyond it, so missing transcripts here are not evidence of no annotation. Reloading this view resets the track selection to Core.').replace('{range}', range);
  const reload = document.createElement('button');
  reload.type = 'button';
  reload.className = 'genome-transcript-scope-reload';
  reload.textContent = say('transcript_reload', 'Reload for this view');
  reload.addEventListener('click', () => {
    const locus = window.scpolyaProductIgvReloadLocus(browser);
    if (!locus) {
      const status = document.getElementById('scpolya-product-igv-status');
      if (status) status.textContent = say('transcript_reload_invalid', 'This view has no valid reference interval. Enter an integer locus in the Gene box and load IGV.');
      return;
    }
    const input = document.querySelector('.genome-gene-control input');
    const load = document.querySelector('.genome-load-action');
    if (!locus || !input || !load) return;
    // One-shot UI hint, bound to this exact integer input by the server. A
    // reload builds annotation for the current view rather than re-focusing
    // on a smaller peak cluster inside it. Ordinary Load has no such hint.
    load.dataset.scpolyaReloadLocus = locus;
    input.value = locus;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    load.click();
  });
  warning.append(text, ' ', reload);
  warning.hidden = true;
  container.parentElement.insertBefore(warning, container);
  const check = () => {
    const frames = browser.referenceFrameList || [];
    // igv frames are 0-based half-open; the scope is 1-based inclusive.
    warning.hidden = frames.every((f) => f.chr === scope.chr && f.start >= scope.start - 1 && f.end <= scope.end);
  };
  browser.on('locuschange', check);
  check();
};
window.scpolyaProductLoadIgv = async (config, messages) => {
  const say = (key, fallback) => (messages && messages[key]) || fallback;
  const status = document.getElementById('scpolya-product-igv-status');
  const container = document.getElementById('scpolya-product-igv-viewer');
  if (!container) return;
  if (!window.igv) {
    if (status) status.textContent = say('not_loaded', 'igv.js is not loaded; showing manifest only.');
    return;
  }
  if (!config?.tracks?.length) {
    if (status) status.textContent = say('no_tracks', 'No ready IGV tracks for this selection.');
    return;
  }
  // A new page render invalidates earlier async creation / loading work.
  await window.scpolyaProductClearIgv();
  const generation = window.scpolyaProductIgvGeneration;
  const current = () => generation === window.scpolyaProductIgvGeneration && container.isConnected;
  const preset = document.getElementById('scpolya-track-preset');
  const choices = document.getElementById('scpolya-track-choices');
  const apply = document.getElementById('scpolya-track-apply');
  const all = config.tracks.map((track, index) => ({...track, order: index + 1}));
  let browser;
  let busy = false;
  const loaded = new Map();
  const internalRemovals = new Set();
  const userRemovals = new Set();
  const incomplete = new Set();
  const controls = [];
  const knownKeys = new Set(all.map(t => t.scpolyaKey));
  const reconcile = () => {
    loaded.clear();
    const actual = browser.trackViews.filter(view => knownKeys.has(view.track.config?.scpolyaKey));
    for (const view of actual) if (!incomplete.has(view.track)) loaded.set(view.track.config.scpolyaKey, view.track);
    return actual.length;
  };
  const removeInternal = (track) => {
    internalRemovals.add(track);
    try { browser.removeTrack(track); }
    finally { internalRemovals.delete(track); }
  };
  const showSettled = () => {
    const count = reconcile();
    if (!status) return;
    if (browser.trackViews.some(view => incomplete.has(view.track))) {
      status.textContent = say('failed', 'IGV could not be loaded: {error}').replace('{error}', 'IGV track cleanup failed');
      return;
    }
    // A failed selected track stays missing after an unrelated gear deletion.
    // Derive partial from the selection/ready-view invariant on every update.
    const wanted = selected();
    const partial = wanted.size !== loaded.size || [...wanted].some(key => !loaded.has(key));
    status.textContent = partial
      ? say('partial', 'Loaded {count}; some selected tracks failed. Check the IGV message.').replace('{count}', count)
      : count === 0
        ? say('empty_selection', 'No tracks are loaded. Choose All or Custom to select available tracks.')
        : say('loaded', 'Loaded {count} track(s).').replace('{count}', count);
  };
  const setBusy = (value) => {
    busy = value;
    for (const control of [preset, apply, ...controls]) if (control) control.disabled = value;
  };
  const showCount = (done, total) => {
    if (current() && status) status.textContent = say('loading', 'Loading {count} tracks…').replace('{count}', `${done}/${total}`);
  };
  const selected = () => new Set(controls.filter(c => c.checked).map(c => c.value));
  const syncTracks = async (wanted) => {
    if (busy || !current()) return;
    setBusy(true);
    userRemovals.clear();
    reconcile();
    let done = 0;
    const tracks = all.filter(t => wanted.has(t.scpolyaKey));
    const total = tracks.length;
    showCount(done, total);
    try {
      // A failed cleanup remains an explicit error until a later retry really
      // removes the incomplete view; it must never become a reusable track.
      for (const track of incomplete) {
        if (browser.trackViews.some(view => view.track === track)) removeInternal(track);
        incomplete.delete(track);
      }
      reconcile();
      for (const [key, track] of loaded) {
        if (!wanted.has(key)) removeInternal(track);
      }
      const outcomes = await Promise.allSettled(tracks.map(async (config) => {
        try {
          if (!loaded.has(config.scpolyaKey)) {
            const track = await browser.loadTrack({...config, sync: true});
            if (!track) throw new Error(`Track unavailable: ${config.name}`);
            if (!current()) return;
            // A gear deletion can happen while loadTrack awaits its viewport.
            // Never resurrect a disposed track in our bookkeeping on return.
            if (userRemovals.has(config.scpolyaKey)) {
              if (browser.trackViews.some(v => v.track === track)) removeInternal(track);
              return;
            }
            if (!browser.trackViews.some(v => v.track === track)) {
              throw new Error(`Loaded track has no TrackView: ${config.name}`);
            }
            loaded.set(config.scpolyaKey, track);
          }
        } catch (error) {
          if (!current()) return;
          // IGV can create a TrackView before postInit fails. Remove that
          // incomplete view so retry / Core cannot leave orphan error tracks.
          for (const view of [...browser.trackViews]) {
            if (view.track.config?.scpolyaKey === config.scpolyaKey) {
              incomplete.add(view.track);
              removeInternal(view.track);
              incomplete.delete(view.track);
            }
          }
          if (!userRemovals.has(config.scpolyaKey)) console.error('IGV track could not be loaded', config.name, error);
        } finally { done++; showCount(done, total); }
      }));
      if (!current()) return;
      // Await every task even if cleanup throws; do not release the controls
      // while another task from this synchronization can still add a view.
      const rejected = outcomes.filter(result => result.status === 'rejected');
      if (rejected.length) throw new AggregateError(rejected.map(result => result.reason), 'IGV track cleanup failed');
      browser.reorderTracks();
      // Track promises include the initial viewport render; finish on a paint.
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      if (current()) showSettled();
    } catch (error) {
      if (!current()) return;
      reconcile();
      console.error('Failed to synchronize IGV tracks', error);
      if (status) status.textContent = say('failed', 'IGV could not be loaded: {error}').replace('{error}', error?.message || error);
    } finally { if (current()) setBusy(false); }
  };
  try {
    setBusy(true);
    showCount(0, all.filter(t => t.scpolyaCore).length);
    // No optional track URL enters createBrowser. Loading a preset really
    // downloads only the selected tracks; switches retain existing tracks.
    browser = await window.igv.createBrowser(container, {...config, tracks: []});
    if (!current()) { window.igv.removeBrowser(browser); return; }
    window.scpolyaProductIgvBrowser = browser;
    // The transcript subset remains bounded even if it is deselected later.
    window.scpolyaProductWatchTranscriptScope(browser, config, say);
    const locus = container.querySelector('.igv-search-container input');
    if (locus) {
      locus.placeholder = say('locus_search', 'Locus search');
      locus.setAttribute('aria-label', say('locus_search', 'Locus search'));
    }
    if (choices) {
      choices.replaceChildren();
      for (const track of all) {
        const label = document.createElement('label');
        const input = document.createElement('input');
        input.type = 'checkbox'; input.value = track.scpolyaKey; input.checked = !!track.scpolyaCore;
        controls.push(input);
        input.addEventListener('change', () => { if (preset) preset.value = 'custom'; });
        label.append(input, document.createTextNode(track.name));
        choices.append(label);
      }
    }
    if (preset) {
      preset.value = 'core';
      preset.onchange = () => {
        if (busy) return;
        const custom = document.getElementById('scpolya-track-custom');
        if (custom) custom.open = preset.value === 'custom';
        if (preset.value === 'custom') return;
        const value = preset.value;
        controls.forEach((c, i) => { c.checked = value === 'all' || !!all[i].scpolyaCore; });
        syncTracks(selected());
      };
    }
    if (apply) apply.onclick = () => syncTracks(selected());
    browser.on('trackremoved', (track) => {
      if (!current()) return;
      const key = track.config?.scpolyaKey;
      if (!knownKeys.has(key)) return;
      if (loaded.get(key) === track) loaded.delete(key);
      // Ignore only our own specific removal, never all events while busy.
      if (internalRemovals.has(track)) return;
      userRemovals.add(key);
      const checkbox = controls.find(c => c.value === key);
      if (checkbox) checkbox.checked = false;
      if (preset) preset.value = 'custom';
      if (!busy) showSettled();
    });
    setBusy(false);
    await syncTracks(new Set(all.filter(t => t.scpolyaCore).map(t => t.scpolyaKey)));
  } catch (error) {
    if (!current()) return;
    console.error('Failed to load product IGV browser', error);
    if (status) status.textContent = say('failed', 'IGV could not be loaded: {error}').replace('{error}', error?.message || error);
    if (browser) window.igv.removeBrowser(browser);
    container.innerHTML = '';
    window.scpolyaProductIgvBrowser = null;
    setBusy(false);
  }
};
</script>
"""
