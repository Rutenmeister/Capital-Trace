const DASHBOARD_CHECK_INTERVAL_MS = 5 * 60 * 1000;

const state = {
  records: [],
  filtered: [],
  metadata: {},
  payloadSignature: '',
  lastDashboardCheck: null,
};

const els = {
  refreshButton: document.querySelector('#refreshButton'),
  syncStatus: document.querySelector('#syncStatus'),
  exportButton: document.querySelector('#exportButton'),
  searchInput: document.querySelector('#searchInput'),
  typeFilter: document.querySelector('#typeFilter'),
  sourceFilter: document.querySelector('#sourceFilter'),
  filingTypeFilter: document.querySelector('#filingTypeFilter'),
  timeWindowFilter: document.querySelector('#timeWindowFilter'),
  displayModeFilter: document.querySelector('#displayModeFilter'),
  actionFilter: document.querySelector('#actionFilter'),
  focusFilter: document.querySelector('#focusFilter'),
  minScore: document.querySelector('#minScore'),
  minScoreValue: document.querySelector('#minScoreValue'),
  watchlistOnly: document.querySelector('#watchlistOnly'),
  hideLowSignal: document.querySelector('#hideLowSignal'),
  evidenceQueue: document.querySelector('#evidenceQueue'),
  visibleRecordStack: document.querySelector('#visibleRecordStack'),
  recordsTable: document.querySelector('#recordsTable'),
  visibleCount: document.querySelector('#visibleCount'),
  tableCount: document.querySelector('#tableCount'),
  statTotal: document.querySelector('#statTotal'),
  statResearchNow: document.querySelector('#statResearchNow'),
  statWatchlist: document.querySelector('#statWatchlist'),
  statRefresh: document.querySelector('#statRefresh'),
  briefTitle: document.querySelector('#briefTitle'),
  briefText: document.querySelector('#briefText'),
  readoutResearch: document.querySelector('#readoutResearch'),
  readoutWatch: document.querySelector('#readoutWatch'),
  readoutContext: document.querySelector('#readoutContext'),
  readoutHidden: document.querySelector('#readoutHidden'),
  lastSecCheck: document.querySelector('#lastSecCheck'),
  nextSecCheck: document.querySelector('#nextSecCheck'),
  dataMode: document.querySelector('#dataMode'),
  sourceCoverage: document.querySelector('#sourceCoverage'),
  laneHealth: document.querySelector('#laneHealth'),
  traceBrief: document.querySelector('#traceBrief'),
  template: document.querySelector('#recordCardTemplate'),
};

async function loadRecords(options = {}) {
  const { manual = false, silent = false } = options;
  let payload = null;
  let source = 'embedded sample';

  if (manual) setSyncStatus('Checking latest saved records...', 'checking');

  // Local-first fallback: browsers often block fetch() for JSON when index.html is
  // opened directly from the file system. The embedded JS payload makes the
  // prototype work by double-clicking index.html, while keeping JSON support for
  // future hosted/scheduled refresh versions.
  if (window.CAPITAL_TRACE_PAYLOAD) {
    payload = window.CAPITAL_TRACE_PAYLOAD;
  }

  // When hosted over http(s), prefer the JSON file so future refresh jobs can
  // replace data/capital_trace.json without rebuilding the page bundle.
  if (location.protocol !== 'file:') {
    try {
      const url = `data/capital_trace.json?cache=${Date.now()}`;
      const response = await fetch(url, { cache: 'no-store' });
      if (response.ok) {
        payload = await response.json();
        source = 'hosted JSON';
      }
    } catch (error) {
      console.warn('Using embedded data fallback because JSON fetch failed.', error);
    }
  }

  state.lastDashboardCheck = new Date();

  if (!payload) {
    els.evidenceQueue.innerHTML = `<p class="empty-state">Capital Trace could not load records. Check that <code>data/capital_trace_data.js</code> exists.</p>`;
    setSyncStatus('Unable to load Capital Trace records.', 'error');
    return;
  }

  try {
    const prepared = preparePayload(payload);
    const normalizedRecords = prepared.records.map(normalizeCapitalTraceRecord).map(recalibrateRecord);
    const nextSignature = payloadSignature({ metadata: prepared.metadata, records: normalizedRecords });
    const previousSignature = state.payloadSignature;
    const previousIds = new Set(state.records.map((record) => record.record_id || record.id));

    state.metadata = normalizeMetadata(prepared.metadata, source);
    state.records = normalizedRecords.sort((a, b) => b.score - a.score);
    state.payloadSignature = nextSignature;

    populateTypeFilter();
    populateSourceFilter();
    populateFilingTypeFilter();
    applyFilters();
    updateBrief();

    if (previousSignature && previousSignature !== nextSignature) {
      const newCount = state.records.filter((record) => !previousIds.has(record.record_id || record.id)).length;
      setSyncStatus(newCount > 0 ? `Updated: ${newCount} new record${newCount === 1 ? '' : 's'} added to the Evidence Queue.` : 'Updated: record file changed; queue refreshed.', 'updated');
    } else if (manual) {
      setSyncStatus(`No new records since the last saved data check. Dashboard checked at ${formatClock(state.lastDashboardCheck)}.`, 'clean');
    } else if (!silent) {
      setSyncStatus(`Loaded ${source}. Dashboard auto-checks the saved record file every 5 minutes when hosted.`, 'clean');
    }
  } catch (error) {
    console.error('Capital Trace record load failed:', error);
    els.evidenceQueue.innerHTML = `<p class="empty-state">Capital Trace found the data file, but could not read its record format. ${escapeHtml(error.message || String(error))}</p>`;
    els.visibleRecordStack.innerHTML = '';
    setSyncStatus('Data file found, but record format could not be read. Upload the v0.5a compatibility patch.', 'error');
  }
}

function preparePayload(payload) {
  // Compatibility layer: accepts all Capital Trace data shapes used so far:
  // 1) [records]
  // 2) { metadata, records }
  // 3) { schema_version, generated_at, records }
  // 4) { data: { metadata, records } }
  const raw = payload && payload.data && Array.isArray(payload.data.records) ? payload.data : payload;
  const records = Array.isArray(raw) ? raw : Array.isArray(raw.records) ? raw.records : [];
  if (!Array.isArray(records)) throw new Error('records is not an array');

  const metadata = Array.isArray(raw) ? {} : {
    ...(raw.metadata || {}),
    product: raw.product || (raw.metadata && raw.metadata.product) || 'Capital Trace',
    schema_version: raw.schema_version || (raw.metadata && raw.metadata.schema_version),
    last_refreshed: raw.last_refreshed || raw.generated_at || (raw.metadata && raw.metadata.last_refreshed),
    last_data_update: raw.last_data_update || raw.generated_at || (raw.metadata && raw.metadata.last_data_update),
    last_sec_check: raw.last_sec_check || raw.generated_at || (raw.metadata && raw.metadata.last_sec_check),
    next_scheduled_check: raw.next_scheduled_check || (raw.metadata && raw.metadata.next_scheduled_check),
    data_mode: raw.data_mode || (raw.metadata && raw.metadata.data_mode),
    source_pipeline: raw.source_pipeline || (raw.metadata && raw.metadata.source_pipeline),
    refresh_frequency: raw.refresh_frequency || (raw.metadata && raw.metadata.refresh_frequency),
    source_groups: raw.source_groups || raw.coverage_lanes || (raw.metadata && (raw.metadata.source_groups || raw.metadata.coverage_lanes)),
    methodology_version: raw.methodology_version || (raw.metadata && raw.metadata.methodology_version),
    lane_diagnostics: raw.lane_diagnostics || (raw.metadata && raw.metadata.lane_diagnostics),
    lookback_days: raw.lookback_days || (raw.metadata && raw.metadata.lookback_days),
    supported_forms: raw.supported_forms || (raw.metadata && raw.metadata.supported_forms),
    counts_by_lane: raw.counts_by_lane || (raw.metadata && raw.metadata.counts_by_lane),
    counts_by_form: raw.counts_by_form || (raw.metadata && raw.metadata.counts_by_form),
  };

  return { metadata, records };
}

function normalizeMetadata(metadata, source) {
  const lastRefreshed = metadata.last_refreshed || metadata.last_data_update || new Date().toISOString();
  const lastSecCheck = metadata.last_sec_check || metadata.last_refreshed || null;
  return {
    product: metadata.product || 'Capital Trace',
    schema_version: metadata.schema_version || '0.2',
    data_mode: metadata.data_mode || (source === 'hosted JSON' ? 'hosted' : 'sample'),
    source_pipeline: metadata.source_pipeline || 'sample-normalized-records',
    refresh_frequency: metadata.refresh_frequency || 'hourly-ready',
    last_refreshed: lastRefreshed,
    last_data_update: metadata.last_data_update || lastRefreshed,
    last_sec_check: lastSecCheck,
    next_scheduled_check: metadata.next_scheduled_check || estimateNextHourlyCheck(lastSecCheck || lastRefreshed),
    source_groups: metadata.source_groups || ['SEC Insider Ownership', 'SEC Ownership Thresholds'],
    methodology_version: metadata.methodology_version || '0.8',
    lane_diagnostics: metadata.lane_diagnostics || {},
    lookback_days: metadata.lookback_days || 60,
    supported_forms: metadata.supported_forms || [],
    counts_by_lane: metadata.counts_by_lane || {},
    counts_by_form: metadata.counts_by_form || {},
  };
}

function normalizeCapitalTraceRecord(record = {}) {
  const rawSourceForm = record.source_form || record.filing_type || record.form_type || record.form || record.source_type || record.source || '-';
  const sourceType = record.source_type || rawSourceForm || '-';
  const filingType = normalizeFilingType(rawSourceForm || sourceType);
  const recordId = record.record_id || record.id || record.accession_number || [sourceType, filingType, record.ticker, record.filer, record.filed_date, record.event_type].join('|');
  const eventDate = record.event_date || record.transaction_date || record.period_end || record.filed_date || '';

  return {
    id: record.id || recordId,
    record_id: recordId,
    ticker: record.ticker || '-',
    company: record.company || '-',
    source_group: record.source_group || inferSourceGroup(sourceType),
    source_type: sourceType,
    source_form: rawSourceForm || sourceType,
    filing_type: filingType,
    record_type: record.record_type || 'Capital Record',
    event_type: record.event_type || 'Public Record Event',
    entity_type: record.entity_type || inferEntityType(record),
    filer: record.filer || record.insider_name || record.institution || '-',
    role: record.role || record.insider_title || record.filer_role || '-',
    owner_type: record.owner_type || record.ownership_type || '-',
    filed_date: record.filed_date || record.filing_date || '',
    event_date: eventDate,
    transaction_date: record.transaction_date || eventDate,
    period_end: record.period_end || '',
    accession_number: record.accession_number || '',
    score: Number(record.score || 0),
    evidence_grade: record.evidence_grade || 'C',
    freshness: record.freshness || 'Unclassified',
    actionability: record.actionability || 'Context Only',
    watchlist_match: Boolean(record.watchlist_match),
    rank_reasons: Array.isArray(record.rank_reasons) ? record.rank_reasons : [],
    does_not_prove: Array.isArray(record.does_not_prove) ? record.does_not_prove : [],
    caveat: record.caveat || 'Source record requires additional review before use.',
    source_url: record.source_url || '#',
    transaction_code: record.transaction_code || '',
    transaction_value: Number(record.transaction_value || 0),
    shares: Number(record.shares || 0),
    price: Number(record.price || 0)
  };
}


function normalizeFilingType(value = '') {
  const raw = String(value || '').trim();
  if (!raw || raw === '-') return 'Unknown';
  const upper = raw.toUpperCase()
    .replace(/^SEC\s+/, '')
    .replace(/^SCHEDULE\s+/, 'SC ')
    .replace(/^FORM\s+/, 'FORM ')
    .replace(/\s+/g, ' ')
    .trim();
  if (upper === '4' || upper === 'FORM 4') return 'Form 4';
  if (upper === '4/A' || upper === 'FORM 4/A') return 'Form 4/A';
  if (upper.includes('13D/A')) return 'SC 13D/A';
  if (upper.includes('13G/A')) return 'SC 13G/A';
  if (upper.includes('13D')) return 'SC 13D';
  if (upper.includes('13G')) return 'SC 13G';
  if (upper.includes('13F-HR')) return '13F-HR';
  return raw.replace(/^SEC\s+/i, '').replace(/^Schedule\s+/i, 'SC ');
}

function countBy(records, getter) {
  const counts = new Map();
  for (const record of records) {
    const value = getter(record) || 'Unknown';
    counts.set(value, (counts.get(value) || 0) + 1);
  }
  return counts;
}

function optionLabelWithCount(label, count) {
  return `${label} (${count})`;
}

function roleShort(role = '') {
  const r = String(role).toLowerCase();
  if (r.includes('chief executive') || r.includes('ceo') || r.includes('president')) return 'CEO';
  if (r.includes('chief financial') || r.includes('cfo')) return 'CFO';
  if (r.includes('director')) return 'Director';
  if (r.includes('10%')) return '10% Owner';
  return 'Insider';
}

function isPurchase(record) {
  return String(record.transaction_code || '').toUpperCase() === 'P' || String(record.event_type || '').toLowerCase().includes('purchase');
}

function isSale(record) {
  return String(record.transaction_code || '').toUpperCase() === 'S' || String(record.event_type || '').toLowerCase().includes('sale');
}

function isOwnership(record) {
  const group = String(record.source_group || '').toLowerCase();
  const type = String(record.record_type || '').toLowerCase();
  const source = String(record.source_type || '').toLowerCase();
  return group.includes('ownership threshold') || type.includes('ownership threshold') || source.includes('13d') || source.includes('13g');
}

function isAdministrative(record) {
  const code = String(record.transaction_code || '').toUpperCase();
  const event = String(record.event_type || '').toLowerCase();
  return ['M','A','G'].includes(code) || event.includes('option') || event.includes('award') || event.includes('grant');
}

function recalibrateRecord(record) {
  const next = { ...record };
  const originalScore = Number(next.score || 0);
  let adjustedScore = originalScore;
  const role = roleShort(next.role);

  if (isOwnership(next)) {
    // Do not force ownership records into insider purchase/sale logic.
    adjustedScore = originalScore;
  } else if (isPurchase(next)) {
    if (!/purchase/i.test(next.event_type)) next.event_type = `${role} Open-Market Purchase`;
    else if (!/^(CEO|CFO|Director|10% Owner)/.test(next.event_type)) next.event_type = `${role} ${next.event_type}`;
    adjustedScore = Math.min(100, originalScore + (role === 'CEO' || role === 'CFO' ? 4 : 0));
  } else if (isSale(next)) {
    next.event_type = role === 'Insider' ? 'Insider Sale' : `${role} Sale`;
    adjustedScore = Math.min(originalScore, next.transaction_value >= 1000000 ? 72 : 64);
  } else if (isAdministrative(next)) {
    adjustedScore = Math.min(originalScore, 44);
  }

  next.score = Math.max(0, Math.min(100, Math.round(adjustedScore)));

  if (isOwnership(next) && next.score >= 84 && ['A','B'].includes(String(next.evidence_grade).charAt(0).toUpperCase())) {
    next.actionability = 'Research Now';
  } else if (isOwnership(next) && next.score >= 62) {
    next.actionability = 'Watch';
  } else if (isPurchase(next) && next.score >= 84 && ['A','B'].includes(String(next.evidence_grade).charAt(0).toUpperCase())) {
    next.actionability = 'Research Now';
  } else if (isPurchase(next) && next.score >= 62) {
    next.actionability = 'Watch';
  } else if (isSale(next)) {
    next.actionability = next.score >= 68 ? 'Watch' : 'Context Only';
  } else if (isAdministrative(next)) {
    next.actionability = next.score >= 40 ? 'Context Only' : 'Low Signal';
  } else if (next.score >= 78 && ['A','B'].includes(String(next.evidence_grade).charAt(0).toUpperCase())) {
    next.actionability = 'Watch';
  } else if (next.score >= 50) {
    next.actionability = 'Context Only';
  } else {
    next.actionability = 'Low Signal';
  }

  if (isSale(next) && !next.rank_reasons.some(r => /sale/i.test(r))) {
    next.rank_reasons.unshift('Sale disclosed; treated as context unless unusually strong');
  }
  if (isAdministrative(next) && !next.rank_reasons.some(r => /administrative|compensation|option/i.test(r))) {
    next.rank_reasons.unshift('Administrative or compensation-related filing; lower signal value');
  }
  return next;
}

function inferSourceGroup(sourceType = '') {
  const s = String(sourceType).toLowerCase();
  if (s.includes('form 4')) return 'SEC Insider Ownership';
  if (s.includes('13f')) return 'SEC Institutional Holdings';
  if (s.includes('13d') || s.includes('13g')) return 'SEC Ownership Threshold';
  if (s.includes('congress') || s.includes('house') || s.includes('senate')) return 'Public Official Disclosure';
  return 'Public Capital Record';
}

function inferEntityType(record = {}) {
  const sourceType = String(record.source_type || record.source_form || '').toLowerCase();
  if (sourceType.includes('form 4')) return 'insider';
  if (sourceType.includes('13f')) return 'institution';
  if (sourceType.includes('13d') || sourceType.includes('13g')) return 'beneficial owner';
  return 'public record entity';
}

function payloadSignature(payload) {
  const ids = (payload.records || [])
    .map((record) => `${record.record_id || record.id}|${record.score}|${record.actionability}|${record.filed_date}`)
    .sort()
    .join('~');
  return `${payload.metadata.last_refreshed || payload.metadata.last_data_update || ''}::${ids}`;
}

function setSyncStatus(message, mode = 'clean') {
  if (!els.syncStatus) return;
  els.syncStatus.textContent = message;
  els.syncStatus.className = `sync-status sync-${mode}`;
}

function estimateNextHourlyCheck(value) {
  const date = new Date(value || Date.now());
  if (Number.isNaN(date.getTime())) return '';
  date.setHours(date.getHours() + 1, 0, 0, 0);
  return date.toISOString();
}

function formatClock(value) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return '-';
  return new Intl.DateTimeFormat('en-US', { hour: 'numeric', minute: '2-digit' }).format(date);
}

function populateTypeFilter() {
  const current = els.typeFilter.value;
  const counts = countBy(state.records, (record) => record.record_type);
  const types = [...counts.keys()].sort();
  els.typeFilter.innerHTML = '<option value="all">All record types</option>';
  for (const type of types) {
    const option = document.createElement('option');
    option.value = type;
    option.textContent = optionLabelWithCount(type, counts.get(type));
    els.typeFilter.appendChild(option);
  }
  els.typeFilter.value = types.includes(current) ? current : 'all';
}

function populateSourceFilter() {
  if (!els.sourceFilter) return;
  const current = els.sourceFilter.value;
  const counts = countBy(state.records, (record) => record.source_group || record.source_type || 'Unknown');
  const groups = [...counts.keys()].sort();
  els.sourceFilter.innerHTML = '<option value="all">All source lanes</option>';
  for (const group of groups) {
    const option = document.createElement('option');
    option.value = group;
    option.textContent = optionLabelWithCount(group, counts.get(group));
    els.sourceFilter.appendChild(option);
  }
  els.sourceFilter.value = groups.includes(current) ? current : 'all';
}

function populateFilingTypeFilter() {
  if (!els.filingTypeFilter) return;
  const current = els.filingTypeFilter.value;
  const counts = countBy(state.records, (record) => record.filing_type || normalizeFilingType(record.source_form || record.source_type));
  const supported = Array.isArray(state.metadata.supported_forms)
    ? state.metadata.supported_forms.map(normalizeFilingType)
    : [];
  const allTypes = new Set([...supported, ...counts.keys()]);
  const filingTypes = [...allTypes].filter(Boolean).sort((a, b) => {
    const order = ['Form 4', 'Form 4/A', 'SC 13D', 'SC 13D/A', 'SC 13G', 'SC 13G/A', '13F-HR', 'Unknown'];
    const ai = order.indexOf(a);
    const bi = order.indexOf(b);
    if (ai !== -1 || bi !== -1) return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi);
    return a.localeCompare(b);
  });
  els.filingTypeFilter.innerHTML = '<option value="all">All filing types</option>';
  for (const filingType of filingTypes) {
    const option = document.createElement('option');
    option.value = filingType;
    option.textContent = optionLabelWithCount(filingType, counts.get(filingType) || 0);
    els.filingTypeFilter.appendChild(option);
  }
  els.filingTypeFilter.value = filingTypes.includes(current) ? current : 'all';
}

function recordWithinWindow(record, selectedTimeWindow) {
  if (!selectedTimeWindow || selectedTimeWindow === 'all') return true;
  const days = Number(selectedTimeWindow);
  if (!Number.isFinite(days) || days <= 0) return true;
  const rawDate = record.filed_date || record.event_date || record.transaction_date || record.period_end;
  const date = new Date(rawDate);
  if (Number.isNaN(date.getTime())) return true;
  const cutoff = Date.now() - days * 24 * 60 * 60 * 1000;
  return date.getTime() >= cutoff;
}

function selectedHighlightFilingType() {
  const selectedFilingType = els.filingTypeFilter ? els.filingTypeFilter.value : 'all';
  const displayMode = els.displayModeFilter ? els.displayModeFilter.value : 'filter';
  return displayMode === 'highlight' && selectedFilingType !== 'all' ? selectedFilingType : null;
}

function recordMatchesHighlight(record) {
  const selected = selectedHighlightFilingType();
  if (!selected) return false;
  return (record.filing_type || normalizeFilingType(record.source_form || record.source_type)) === selected;
}

function applyFilters() {
  const query = els.searchInput.value.trim().toLowerCase();
  const selectedType = els.typeFilter.value;
  const selectedSource = els.sourceFilter ? els.sourceFilter.value : 'all';
  const selectedFilingType = els.filingTypeFilter ? els.filingTypeFilter.value : 'all';
  const selectedTimeWindow = els.timeWindowFilter ? els.timeWindowFilter.value : 'all';
  const displayMode = els.displayModeFilter ? els.displayModeFilter.value : 'filter';
  const selectedAction = els.actionFilter.value;
  const selectedFocus = els.focusFilter ? els.focusFilter.value : 'all';
  const minimumScore = Number(els.minScore.value || 0);
  const watchlistOnly = els.watchlistOnly.checked;
  const hideLowSignal = els.hideLowSignal.checked;
  els.minScoreValue.textContent = `${minimumScore}+`;

  state.filtered = state.records.filter((record) => {
    const haystack = [
      record.ticker,
      record.company,
      record.filer,
      record.event_type,
      record.record_type,
      record.source_form,
      record.source_type,
      record.source_group,
      record.entity_type,
      record.actionability,
      record.evidence_grade,
      record.freshness,
    ].join(' ').toLowerCase();

    if (query && !haystack.includes(query)) return false;
    if (selectedType !== 'all' && record.record_type !== selectedType) return false;
    if (selectedSource !== 'all' && (record.source_group || record.source_type) !== selectedSource) return false;
    if (selectedFilingType !== 'all' && displayMode === 'filter' && (record.filing_type || normalizeFilingType(record.source_form || record.source_type)) !== selectedFilingType) return false;
    if (!recordWithinWindow(record, selectedTimeWindow)) return false;
    if (selectedAction !== 'all' && record.actionability !== selectedAction) return false;
    if (Number(record.score || 0) < minimumScore) return false;
    if (watchlistOnly && !record.watchlist_match) return false;
    if (hideLowSignal && record.actionability === 'Low Signal') return false;
    if (selectedFocus === 'purchases' && !isPurchase(record)) return false;
    if (selectedFocus === 'ownership' && !isOwnership(record)) return false;
    return true;
  });

  if (selectedFocus === 'top10') {
    state.filtered = state.filtered.slice().sort((a, b) => b.score - a.score).slice(0, 10);
  }

  renderAll();
}

function updateBrief() {
  const total = state.records.length;
  const researchNow = state.records.filter((r) => r.actionability === 'Research Now').length;
  const watch = state.records.filter((r) => r.actionability === 'Watch').length;
  const context = state.records.filter((r) => r.actionability === 'Context Only').length;
  const lowSignal = state.records.filter((r) => r.actionability === 'Low Signal').length;
  const watchlist = state.records.filter((r) => r.watchlist_match).length;
  const purchases = state.records.filter(isPurchase).length;
  const sales = state.records.filter(isSale).length;
  const ownership = state.records.filter(isOwnership).length;
  const filingTypeCount = new Set(state.records.map((record) => record.filing_type || normalizeFilingType(record.source_form || record.source_type))).size;
  const refreshed = state.metadata.last_refreshed || 'Sample data';
  const top = state.records[0];
  const topLane = top ? top.event_type : '-';

  els.statTotal.textContent = total;
  els.statResearchNow.textContent = researchNow;
  els.statWatchlist.textContent = watchlist;
  els.statRefresh.textContent = formatDate(refreshed, true);
  els.readoutResearch.textContent = researchNow;
  els.readoutWatch.textContent = watch;
  els.readoutContext.textContent = context;
  els.readoutHidden.textContent = lowSignal;
  els.briefTitle.textContent = `${researchNow} high-signal record${researchNow === 1 ? '' : 's'}`;
  els.briefText.textContent = `${state.metadata.data_mode === 'sample' ? 'Current sample' : 'Current data file'} contains ${total} public records. Current mix: ${purchases} purchase records, ${sales} sale records, ${ownership} ownership records, ${watchlist} watchlist matches. Strongest lane: ${topLane}. Low-signal records can stay hidden by default.`;

  if (els.lastSecCheck) els.lastSecCheck.textContent = formatDate(state.metadata.last_sec_check, true);
  if (els.nextSecCheck) els.nextSecCheck.textContent = formatDate(state.metadata.next_scheduled_check, true);
  if (els.dataMode) els.dataMode.textContent = state.metadata.data_mode || '-';
  if (els.sourceCoverage) els.sourceCoverage.textContent = `${(state.metadata.source_groups || []).length} lanes / ${filingTypeCount} forms`;
  renderLaneHealth();
  renderTraceBrief();
}

function laneStatusLabel(status) {
  return {
    ok: 'Live',
    checked_no_records: 'Checked, no records',
    partial: 'Partial',
    failed: 'Failed',
    disabled: 'Disabled',
    stale: 'Stale',
    running: 'Running',
  }[status] || String(status || 'Unknown');
}

function renderLaneHealth() {
  if (!els.laneHealth) return;
  const diagnostics = state.metadata.lane_diagnostics || {};
  const entries = Object.entries(diagnostics);
  if (!entries.length) {
    els.laneHealth.innerHTML = '<p class="empty-mini">No lane diagnostics found in the current data file.</p>';
    return;
  }
  els.laneHealth.innerHTML = entries.map(([key, diag]) => {
    const status = diag.status || 'unknown';
    const forms = Array.isArray(diag.forms_checked) ? diag.forms_checked.join(', ') : '-';
    const errors = Array.isArray(diag.errors) ? diag.errors.length : 0;
    return `<div class="lane-health-row lane-status-${escapeHtml(status)}">
      <div><strong>${escapeHtml(key.replaceAll('_', ' '))}</strong><small>${escapeHtml(forms)}</small></div>
      <div><span>${escapeHtml(laneStatusLabel(status))}</span><small>${Number(diag.records_added || 0)} records · ${Number(diag.companies_checked || 0)} companies · ${Number(diag.lookback_days || state.metadata.lookback_days || 0)}d</small></div>
      ${errors ? `<p class="lane-error">${errors} error${errors === 1 ? '' : 's'} logged. Review workflow output.</p>` : ''}
      ${diag.note ? `<p class="lane-note">${escapeHtml(diag.note)}</p>` : ''}
    </div>`;
  }).join('');
}

function renderTraceBrief() {
  if (!els.traceBrief) return;
  const countsByForm = state.metadata.counts_by_form && Object.keys(state.metadata.counts_by_form).length
    ? state.metadata.counts_by_form
    : Object.fromEntries(countBy(state.records, (record) => record.filing_type || normalizeFilingType(record.source_form || record.source_type)));
  const formText = Object.entries(countsByForm)
    .sort((a, b) => String(a[0]).localeCompare(String(b[0])))
    .map(([form, count]) => `${escapeHtml(form)}: ${Number(count)}`)
    .join(' · ') || 'No filing counts available';
  els.traceBrief.innerHTML = `<p><strong>${state.records.length}</strong> records loaded across <strong>${(state.metadata.source_groups || []).length}</strong> visible source lane${(state.metadata.source_groups || []).length === 1 ? '' : 's'}.</p>
    <p>Lookback: <strong>${escapeHtml(state.metadata.lookback_days || 'unknown')}</strong> days · Data mode: <strong>${escapeHtml(state.metadata.data_mode || '-')}</strong></p>
    <p>${formText}</p>
    <p class="trace-note">Current coverage is SEC watchlist only, not the full SEC universe.</p>`;
}

function renderAll() {
  const queue = state.filtered.slice(0, 10);

  renderCards(els.evidenceQueue, queue, 'No evidence-queue records match the current filters.', true);
  renderCards(els.visibleRecordStack, state.filtered, 'No visible records match the current filters.', false);
  renderTable();

  const countText = `${state.filtered.length} visible of ${state.records.length} records`;
  els.visibleCount.textContent = countText;
  els.tableCount.textContent = countText;
}

function activeEmptyStateMessage(defaultMessage) {
  const selectedFilingType = els.filingTypeFilter ? els.filingTypeFilter.value : 'all';
  const selectedSource = els.sourceFilter ? els.sourceFilter.value : 'all';
  const selectedTimeWindow = els.timeWindowFilter ? els.timeWindowFilter.value : 'all';
  if (selectedFilingType === 'all' && selectedSource === 'all') return defaultMessage;
  const parts = [];
  if (selectedFilingType !== 'all') parts.push(`filing type ${selectedFilingType}`);
  if (selectedSource !== 'all') parts.push(`source lane ${selectedSource}`);
  if (selectedTimeWindow !== 'all') parts.push(`last ${selectedTimeWindow} days`);
  const diagnosticHint = diagnosticSummaryText();
  return `No records match ${parts.join(' / ')}. ${diagnosticHint}`;
}

function diagnosticSummaryText() {
  const diagnostics = state.metadata.lane_diagnostics || {};
  const ownership = diagnostics.ownership_13d_13g;
  if (ownership) {
    return `Ownership lane status: ${laneStatusLabel(ownership.status)}; checked ${ownership.companies_checked || 0} companies over ${ownership.lookback_days || state.metadata.lookback_days || '?'} days; records added: ${ownership.records_added || 0}.`;
  }
  return 'No lane diagnostics are available in this data file.';
}

function renderCards(container, records, emptyMessage, openFirst = false) {
  container.innerHTML = '';
  if (!records.length) {
    container.innerHTML = `<p class="empty-state">${escapeHtml(activeEmptyStateMessage(emptyMessage))}</p>`;
    return;
  }
  records.forEach((record, index) => {
    container.appendChild(createCard(record, openFirst && index === 0));
  });
}

function createCard(record, expanded = false) {
  const node = els.template.content.firstElementChild.cloneNode(true);
  if (recordMatchesHighlight(record)) node.classList.add('highlight-match');
  const button = node.querySelector('.record-summary');
  const indicator = node.querySelector('.expand-indicator');

  node.querySelector('.ticker').textContent = record.ticker;
  node.querySelector('.event-title').textContent = record.event_type;
  node.querySelector('.company-line').textContent = record.company;
  node.querySelector('.score-pill').textContent = `Score ${record.score}`;

  const lanePill = node.querySelector('.lane-pill');
  if (lanePill) {
    lanePill.textContent = isOwnership(record) ? 'Ownership' : String(record.source_group || 'Source').replace(/^SEC\s+/i, '');
    lanePill.classList.add(isOwnership(record) ? 'lane-ownership' : 'lane-insider');
  }
  const filingPill = node.querySelector('.filing-pill');
  if (filingPill) filingPill.textContent = record.filing_type || normalizeFilingType(record.source_form || record.source_type);

  const grade = node.querySelector('.grade-pill');
  grade.textContent = `Evidence ${record.evidence_grade}`;
  grade.classList.add(`grade-${String(record.evidence_grade).toLowerCase().charAt(0)}`);

  const action = node.querySelector('.action-pill');
  action.textContent = record.actionability;
  action.classList.add(actionClass(record.actionability));

  node.querySelector('.freshness-pill').textContent = record.freshness;
  node.querySelector('.filer').textContent = record.filer;
  node.querySelector('.source').textContent = record.source_type || record.source_form;
  node.querySelector('.filed').textContent = formatDate(record.filed_date);
  node.querySelector('.caveat').textContent = record.caveat;
  node.querySelector('.method-line').textContent = methodologyLine(record);

  const badge = node.querySelector('.watchlist-badge');
  if (!record.watchlist_match) badge.classList.add('hidden');

  fillList(node.querySelector('.rank-reasons'), record.rank_reasons);
  fillList(node.querySelector('.does-not-prove'), record.does_not_prove);

  const source = node.querySelector('.source-link');
  source.href = record.source_url;
  source.textContent = 'View source record';

  setExpanded(node, button, indicator, expanded);
  button.addEventListener('click', () => {
    setExpanded(node, button, indicator, node.classList.contains('collapsed'));
  });

  return node;
}

function setExpanded(node, button, indicator, expanded) {
  node.classList.toggle('collapsed', !expanded);
  node.classList.toggle('expanded', expanded);
  button.setAttribute('aria-expanded', expanded ? 'true' : 'false');
  indicator.textContent = expanded ? 'Collapse' : 'Expand';
}

function actionClass(actionability) {
  return {
    'Research Now': 'action-research',
    'Watch': 'action-watch',
    'Context Only': 'action-context',
    'Low Signal': 'action-low',
  }[actionability] || 'action-context';
}

function methodologyLine(record) {
  const pieces = [];
  if (record.record_type) pieces.push(record.record_type);
  if (record.watchlist_match) pieces.push('watchlist relevance');
  if (record.freshness) pieces.push(`${String(record.freshness).toLowerCase()} record`);
  pieces.push(`score ${record.score}`);
  return `Ranked by ${pieces.join(' + ')}; uncertainty and caveats remain attached to the record.`;
}

function fillList(list, items = []) {
  list.innerHTML = '';
  for (const item of items) {
    const li = document.createElement('li');
    li.textContent = item;
    list.appendChild(li);
  }
}

function renderTable() {
  if (!state.filtered.length) {
    els.recordsTable.innerHTML = '<p class="empty-state">No records match the current filters.</p>';
    return;
  }

  const rows = state.filtered.map((record) => `
    <tr>
      <td><strong>${escapeHtml(record.ticker)}</strong><div class="table-sub">${escapeHtml(record.company)}</div></td>
      <td>${escapeHtml(record.event_type)}<div class="table-sub">${escapeHtml(record.record_type)}</div></td>
      <td>${escapeHtml(record.filing_type || normalizeFilingType(record.source_form || record.source_type))}<div class="table-sub">${escapeHtml(record.source_group)}</div></td>
      <td>${escapeHtml(record.filer)}<div class="table-sub">${escapeHtml(record.source_form)}</div></td>
      <td>${record.score}</td>
      <td>${escapeHtml(record.evidence_grade)}</td>
      <td>${escapeHtml(record.actionability)}<div class="table-sub">${escapeHtml(record.freshness)}</div></td>
      <td>${formatDate(record.filed_date)}</td>
      <td><a href="${record.source_url}" target="_blank" rel="noopener noreferrer">Source</a></td>
    </tr>
  `).join('');

  els.recordsTable.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Ticker</th>
          <th>Event</th>
          <th>Filing</th>
          <th>Filer</th>
          <th>Score</th>
          <th>Grade</th>
          <th>Action</th>
          <th>Filed</th>
          <th>Link</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function exportCsv() {
  const headers = ['record_id','ticker','company','source_group','source_type','event_type','record_type','entity_type','filer','role','owner_type','filed_date','event_date','period_end','accession_number','score','evidence_grade','freshness','actionability','caveat','source_url'];
  const lines = [headers.join(',')];
  for (const record of state.filtered) {
    lines.push(headers.map((header) => csvCell(record[header])).join(','));
  }
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = 'capital_trace_visible_records.csv';
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function csvCell(value) {
  const text = String(value ?? '');
  return `"${text.replaceAll('"', '""')}"`;
}

function formatDate(value, short = false) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('en-US', {
    year: 'numeric', month: short ? 'short' : 'long', day: 'numeric',
    ...(short ? {} : { hour: 'numeric', minute: '2-digit' })
  }).format(date);
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

els.refreshButton.addEventListener('click', () => loadRecords({ manual: true }));
els.exportButton.addEventListener('click', exportCsv);
els.searchInput.addEventListener('input', applyFilters);
els.typeFilter.addEventListener('change', applyFilters);
if (els.sourceFilter) els.sourceFilter.addEventListener('change', applyFilters);
if (els.filingTypeFilter) els.filingTypeFilter.addEventListener('change', applyFilters);
if (els.timeWindowFilter) els.timeWindowFilter.addEventListener('change', applyFilters);
if (els.displayModeFilter) els.displayModeFilter.addEventListener('change', applyFilters);
els.actionFilter.addEventListener('change', applyFilters);
if (els.focusFilter) els.focusFilter.addEventListener('change', applyFilters);
els.minScore.addEventListener('input', applyFilters);
els.watchlistOnly.addEventListener('change', applyFilters);
els.hideLowSignal.addEventListener('change', applyFilters);

loadRecords();
if (location.protocol !== 'file:') {
  setInterval(() => loadRecords({ silent: true }), DASHBOARD_CHECK_INTERVAL_MS);
}
