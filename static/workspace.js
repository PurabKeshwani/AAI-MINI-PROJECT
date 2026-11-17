'use strict';

const $ = id => document.getElementById(id);
const integer = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 });
const decimal = new Intl.NumberFormat('en-IN', { minimumFractionDigits: 1, maximumFractionDigits: 2 });
const rupees = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 });
const shortDate = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' });
const longDate = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
})[char]);
const n = value => integer.format(value);
const d = value => decimal.format(value);
const money = value => rupees.format(value);
const percent = value => `${(value * 100).toFixed(1)}%`;
const practiceMode = document.body.dataset.mode === 'practice';
const operationsMode = practiceMode || document.body.dataset.mode === 'operations';
const workspaceEndpoint = practiceMode ? 'practice' : 'operations';

const views = {
  overview: ['Inventory operations', 'Monitor stock, plan replenishment and track warehouse deliveries.'],
  inventory: ['Inventory & forecast', 'Review warehouse stock, inbound units and demand at each product location.'],
  vendors: ['Vendor decisions', 'Compare feasible suppliers against the cost of holding and running short.'],
  orders: ['Purchase & transfer orders', 'Track supplier deliveries and warehouse transfers.'],
  agents: ['Agent trace', 'Follow each specialist’s inputs, decisions and handoff.'],
  results: ['Evaluation', 'Review forecast accuracy and the historical policy replay.'],
  data: ['Workspace data', 'Manage the records and supplier terms used for planning.'],
};
const modelNames = { mean28: '28-day mean', seasonal7: 'Weekly seasonal', ridge: 'Ridge regression', selected: 'Validation-selected' };
const statusNames = {
  HIGH: 'High risk', MEDIUM: 'Medium risk', LOW: 'Low risk', OVERSTOCK: 'Overstock',
  OPEN: 'Open', RECEIVED: 'Received', SELECTED: 'Selected', INFEASIBLE: 'Infeasible',
  DRAFT: 'Draft', SUPERSEDED: 'Superseded', CANCELLED: 'Cancelled',
};
const agentMethods = {
  MasterOrchestrator: ['Master orchestrator', 'Coordinates ten specialists over a shared Context and publishes their handoffs.'],
  InventoryTrackingAgent: ['Inventory tracking', 'Validates stock and reservations, then adds open inbound units to available stock to calculate inventory position.'],
  DemandForecastingAgent: ['Demand forecasting', 'Chooses the lowest validation WAPE per product and warehouse, then projects daily demand for 21 days.'],
  StockoutRiskAgent: ['Stockout risk', 'Projects demand and timed receipts through the shortest supplier lead time, then checks safety stock and excess inventory.'],
  AnomalyDetectionAgent: ['Anomaly detection', 'Uses the median absolute deviation of recent sales to flag unusually large deviations.'],
  WarehouseOptimizationAgent: ['Warehouse optimization', 'Plans to move surplus stock toward high-risk locations while protecting 14 days of donor demand and its safety buffer.'],
  ReorderDecisionAgent: ['Reorder decision', 'Creates a purchase proposal when the post-transfer stock position falls below lead-time demand plus safety stock.'],
  VendorIntelligenceAgent: ['Vendor intelligence', 'Checks each supplier’s lot size, minimum order, capacity, discounts and the per-order budget.'],
  CostOptimizationAgent: ['Cost optimization', 'Selects the feasible supplier with the lowest sum of landed, holding and estimated shortage cost.'],
  PurchaseOrderAgent: ['Purchase order', 'Builds structured purchase orders with supplier, quantity, price and ETA.'],
  ReportingDashboardAgent: ['Reporting dashboard', 'Publishes the cycle’s forecasts, risk assessments, supplier decisions and audit messages.'],
};
const icons = {
  observe: '<path d="M2 12s3.6-6 10-6 10 6 10 6-3.6 6-10 6S2 12 2 12Z"/><circle cx="12" cy="12" r="2.5" />',
  forecast: '<path d="M3 18h18M5 14l4-4 4 3 6-7m-4 0h4v4"/>',
  decide: '<path d="M6 4v16m12-16v4m0 5v7M3 10h6m6 2h6"/>',
  execute: '<path d="M4 7h16v13H4zm-1-4h18v4H3zm6 8h6"/>',
  trace: '<path d="M2 6h8v8H2z"/>',
};
const presets = { baseline: ['1', '0'], disruption: ['1.6', '2'] };
const riskRank = { HIGH: 0, MEDIUM: 1, OVERSTOCK: 2, LOW: 3 };
const key = record => `${record.sku}/${record.warehouse}`;
const remainingUnits = order => order.quantity - (order.received_quantity ?? 0);
const groups = (items, field) => [...new Set(items.map(item => item[field]))];
const sum = (items, get) => items.reduce((total, item) => total + get(item), 0);
const plural = (count, singular, pluralWord = singular + 's') => `${n(count)} ${count === 1 ? singular : pluralWord}`;
const dateLabel = (value, formatter = longDate) => formatter.format(new Date(`${value}T00:00:00Z`));
const dateAfter = (value, days) => {
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
};

let state;
let currentView = 'overview';
let busy = false;
let autoActive = false;
let autoTimer;
let decisionKey;
const offlineSnapshot = $('offlineSnapshot');

function icon(name, size = 17) {
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" aria-hidden="true">${icons[name]}</svg>`;
}

function productName(sku) {
  const name = state.products.find(product => product.sku === sku)?.name;
  if (operationsMode && !practiceMode) return name || sku;
  return name ? name.toLowerCase().replace(/^.| t-light/, value => value.toUpperCase()) : sku;
}

function chip(status) {
  return `<span class="chip ${esc(status)}">${esc(statusNames[status] || status)}</span>`;
}

function table(label, headers, rows, layout = '') {
  if (!rows.length) return '';
  const columns = headers.map(header => Array.isArray(header)
    ? { label: header[0], numeric: true }
    : { label: header, numeric: false });
  return `<div class="table-frame ${layout}" role="region" aria-label="${esc(label)}" tabindex="0">
    <table><thead><tr>${columns.map(column =>
      `<th scope="col"${column.numeric ? ' class="number"' : ''}>${esc(column.label)}</th>`
    ).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${row.map((cell, index) =>
      `<td data-label="${esc(columns[index].label)}"${columns[index].numeric ? ' class="number"' : ''}>${cell}</td>`
    ).join('')}</tr>`).join('')}</tbody></table></div>`;
}

function empty(message, action = false) {
  return `<div class="empty"><p>${esc(message)}</p>${action
    ? `<button class="text-button" data-mutation onclick="act('cycle')">${operationsMode ? 'Run replenishment plan' : 'Run agent cycle'}</button>` : ''}</div>`;
}

function productButton(record, showSku = false) {
  return `<button type="button" class="text-button product-link" data-decision="${esc(key(record))}">${esc(productName(record.sku))}</button>
    ${showSku ? `<small>${esc(record.sku)}</small>` : ''}`;
}

function allowedView(id) {
  return Object.hasOwn(views, id) && id !== (operationsMode ? 'results' : 'data');
}

function tab(id) {
  if (allowedView(id)) {
    if (location.hash !== '#' + id) history.pushState(null, '', '#' + id);
    showView(id);
  }
}

function showView(id) {
  if (!allowedView(id)) return;
  currentView = id;
  document.querySelectorAll('.section').forEach(section => section.classList.toggle('active', section.id === id));
  document.querySelectorAll('.tabs button').forEach((button, index) => {
    const selected = Object.keys(views)[index] === id;
    button.classList.toggle('active', selected);
    if (selected) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
  $('pageTitle').textContent = views[id][0];
  $('pageDescription').textContent = views[id][1];
  $('actionUpdate').hidden = true;
  if (state && operationsMode) syncOperationControls();
  if (state && id === 'overview') drawChart();
}

function syncLocation() {
  const id = location.hash.slice(1);
  if (allowedView(id) || !id) showView(id || 'overview');
}

function currentCycle() {
  if (operationsMode) return Boolean(state?.last_run?.current);
  return state?.last_run?.date === state?.date && Boolean(state?.last_run);
}

function renderStages(stages) {
  $('stages').innerHTML = stages.map(([name, label, value, detail]) =>
    `<li><span>${icon(name)}${label}</span><strong>${value}</strong><small>${detail}</small></li>`
  ).join('');
}

function renderWorkflow() {
  if (operationsMode) return renderPlanningWorkflow();
  const run = state.last_run;
  const agents = groups(state.log, 'agent').length;
  const orders = run ? state.orders.filter(order => order.id?.includes(run.date.replaceAll('-', ''))) : [];
  const high = run?.high_risks ?? (currentCycle() ? state.risks.filter(risk => risk.level === 'HIGH').length : null);
  const purchases = run?.purchases ?? orders.filter(order => order.kind === 'PURCHASE').length;
  const transfers = run?.transfers ?? orders.filter(order => order.kind === 'TRANSFER').length;
  const atDate = currentCycle();
  $('runStatus').className = `chip ${atDate ? 'good' : 'neutral'}`;
  $('runStatus').textContent = atDate ? 'Cycle complete' : 'Ready to run';
  $('workflowCaption').textContent = run
    ? `${n(agents)} agents coordinated through shared state. Last run ${dateLabel(run.date)}.`
    : 'The orchestrator hands shared state from stock checks to order execution.';
  const stages = [
    ['observe', 'Observe', run ? plural(state.inventory.length, 'stock position') : 'Stock checks', 'Validate inventory and inbound orders'],
    ['forecast', 'Forecast', run ? plural(state.forecasts.length, 'forecast') : 'Demand models', 'Compare models for a 21-day horizon'],
    ['decide', 'Decide', run?.proposals != null ? plural(run.proposals, 'purchase proposal')
      : high === null ? 'Replenishment' : plural(high, 'high risk'), 'Check risk, plan transfers, rank suppliers'],
    ['execute', 'Execute', run ? plural(purchases + transfers, 'order') + ' created' : 'Order ledger',
      run ? `${plural(purchases, 'purchase')} / ${plural(transfers, 'transfer')}` : 'Commit orders to the simulation ledger'],
  ];
  renderStages(stages);
  const day = state.last_day;
  if (!atDate && day?.date === state.date) {
    $('notice').textContent = `${n(day.received)} units received, ${n(day.served)} served, ${n(day.unmet)} unmet. Demand comes from the forecast.`;
  } else if (atDate) {
    $('notice').textContent = `${plural(purchases, 'purchase order')} and ${plural(transfers, 'transfer')} committed to SQLite.`;
  } else {
    $('notice').textContent = state.simulated_days ? state.last_action : 'Run the cycle to detect risks and generate replenishment orders.';
  }
}

function renderOverview() {
  renderWorkflow();
  $('date').textContent = `Simulation date: ${dateLabel(state.date)}`;
  const openPurchases = state.orders.filter(order => order.kind === 'PURCHASE' && order.status === 'OPEN');
  const high = state.risks.filter(risk => risk.level === 'HIGH').length;
  const metrics = [
    ['Available stock', n(sum(state.inventory, row => row.on_hand - row.reserved)), `${state.inventory.length} product locations`],
    ['High stockout risks', (operationsMode ? state.last_run : currentCycle()) ? n(high) : '—',
      operationsMode && state.last_run ? `Assessed ${dateLabel(state.last_run.date, shortDate)}` : currentCycle() ? 'Before corrective actions' : 'Pending a plan', high > 0],
    ['Open purchase orders', n(openPurchases.length), operationsMode
      ? `${n(sum(openPurchases, remainingUnits))} units awaiting receipt`
      : `${money(sum(openPurchases, order => order.total_inr))} committed`],
    ['Agents in workflow', n(Object.keys(agentMethods).length), '10 specialists and an orchestrator'],
  ];
  $('metrics').innerHTML = metrics.map(([label, value, note, critical]) =>
    `<div><span>${label}</span><strong${critical ? ' class="critical"' : ''}>${value}</strong><small>${note}</small></div>`
  ).join('');
  $('metrics').setAttribute('aria-busy', 'false');

  const risks = state.risks.filter(risk => risk.level !== 'LOW').sort((a, b) =>
    riskRank[a.level] - riskRank[b.level] || b.expected_shortage - a.expected_shortage
  );
  $('riskTotal').textContent = (operationsMode ? state.last_run : currentCycle()) ? plural(risks.length, 'flagged position') : '';
  $('risks').innerHTML = table('Stockout and overstock watch',
    ['Product', 'Warehouse', 'Risk', ['Cover'], ['Safety stock'], ['Shortfall']],
    risks.map(risk => [productButton(risk), esc(risk.warehouse), chip(risk.level),
      `${d(risk.days_cover)} d`, n(risk.safety_stock), d(risk.expected_shortage)]), 'compact-table'
  ) || empty((operationsMode ? state.last_run : currentCycle()) ? 'No stock positions were flagged in the last plan.'
    : 'Stock risk will appear after planning.', !operationsMode && !currentCycle());

  $('warehouses').innerHTML = groups(state.inventory, 'warehouse').map(warehouse => {
    const rows = state.inventory.filter(row => row.warehouse === warehouse);
    const stock = sum(rows, row => row.on_hand);
    const capacity = sum(rows, row => row.capacity);
    return `<div class="warehouse"><div class="warehouse-row"><strong>${esc(warehouse)}</strong><span>${n(stock)} units</span></div>
      <progress max="${capacity}" value="${stock}" aria-label="${esc(warehouse)} capacity used"></progress>
      <small>${percent(stock / capacity)} of ${n(capacity)} units of capacity</small></div>`;
  }).join('') || empty('Warehouse balances appear after importing stock locations.');
  drawChart();
}

function drawChart() {
  if (!state || currentView !== 'overview') return;
  const svg = $('chart');
  const emptyChart = state.history.length < 2;
  svg.toggleAttribute('hidden', emptyChart);
  svg.closest('.demand-panel').querySelector('.chart-legend').hidden = emptyChart;
  if (emptyChart) {
    $('chartNote').textContent = 'Import completed sales days to see demand history here.';
    return;
  }
  const width = Math.round(svg.clientWidth);
  const height = Math.round(svg.clientHeight);
  if (!width || !height || state.history.length < 2) return;
  const dates = state.history.map(row => row.date);
  const actual = state.history.map(row => row.units);
  const horizon = state.forecasts[0]?.values.length ?? 0;
  const forecast = Array.from({ length: horizon }, (_, index) => sum(state.forecasts, item => item.values[index]));
  const maximum = Math.max(1, ...actual, ...forecast);
  const magnitude = 10 ** Math.floor(Math.log10(maximum / 3));
  const interval = Math.max(1, Math.ceil(maximum / 3 / magnitude) * magnitude);
  const left = 49;
  const right = width - 8;
  const top = 12;
  const bottom = height - 28;
  const last = state.last_run?.date ?? dates.at(-1);
  const gap = forecast.length && operationsMode ? Math.max(0, (Date.parse(last) - Date.parse(dates.at(-1))) / 86400000) : 0;
  const forecastOffset = actual.length + gap;
  const step = (right - left) / (forecastOffset + forecast.length - 1);
  const x = index => left + step * index;
  const y = value => bottom - value / (interval * 3) * (bottom - top);
  const path = (values, offset = 0) => values.map((value, index) =>
    `${index ? 'L' : 'M'}${x(offset + index).toFixed(2)},${y(value).toFixed(2)}`
  ).join(' ');
  const bandX = forecast.length ? x(forecastOffset - .5) : right;
  const forecastStart = dateAfter(last, 1);
  const forecastEnd = dateAfter(last, horizon);
  const contiguous = last === dates.at(-1);
  const tick = (at, date, anchor = 'middle') =>
    `<text x="${at}" y="${height - 7}" text-anchor="${anchor}">${dateLabel(date, shortDate)}</text>`;
  const ticks = [
    tick(left, dates[0], 'start'),
    ...(width > 460 && dates.length > 25 ? [tick(x(20), dates[20])] : []),
    tick(x(actual.length - 1), dates.at(-1), forecast.length ? 'middle' : 'end'),
    ...(forecast.length ? [tick(right, forecastEnd, 'end')] : []),
  ];
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  svg.innerHTML = `${forecast.length ? `<rect class="chart-band" x="${bandX}" y="${top}" width="${right - bandX}" height="${bottom - top}"/>` : ''}
    ${[0, 1, 2, 3].map(index =>
      `<line class="chart-grid" x1="${left}" x2="${right}" y1="${y(index * interval)}" y2="${y(index * interval)}"/>
      <text x="${left - 9}" y="${y(index * interval) + 4}" text-anchor="end">${n(index * interval)}</text>`).join('')}
    <path class="series observed" d="${path(actual)}"/>
    ${forecast.length ? `<line class="chart-boundary" x1="${bandX}" x2="${bandX}" y1="${top}" y2="${bottom}"/>
      <path class="series forecast" d="${contiguous
        ? path([actual.at(-1), ...forecast], actual.length - 1) : path(forecast, forecastOffset)}"/>` : ''}
    ${ticks.join('')}`;
  const note = forecast.length
    ? `Forecast: ${dateLabel(forecastStart, shortDate)} to ${dateLabel(forecastEnd)}${state.date > last ? ' (most recent run)' : ''}.`
    : 'Run planning to add the 21-day forecast.';
  $('chartNote').textContent = note;
  svg.setAttribute('aria-label', `Observed daily sales from ${dateLabel(dates[0])} to ${dateLabel(dates.at(-1))}. ${note}`);
}

function renderInventory() {
  const filter = $('warehouseFilter');
  const warehouse = filter.value;
  filter.innerHTML = '<option value="">All warehouses</option>' + groups(state.inventory, 'warehouse')
    .map(value => `<option value="${esc(value)}">${esc(value)}</option>`).join('');
  if ([...filter.options].some(option => option.value === warehouse)) filter.value = warehouse;
  const rows = state.inventory.filter(row => !filter.value || row.warehouse === filter.value);
  const forecasts = new Map(state.forecasts.map(forecast => [key(forecast), forecast]));
  const inbound = new Map();
  for (const order of state.orders.filter(item => item.status === 'OPEN')) {
    inbound.set(key(order), (inbound.get(key(order)) || 0) + remainingUnits(order));
  }
  const forecastDate = state.last_run ? `Forecast from ${dateLabel(state.last_run.date)}.` : 'Forecast pending.';
  $('inventoryCaption').textContent = `${plural(rows.length, 'product location')}. ${forecastDate}`;
  $('inventoryTable').innerHTML = table('Product locations', [
    'Product / SKU', 'Warehouse', ['On hand'], ['Reserved'], ['On order'], ['Capacity'], ['Forecast / day'], 'Model',
  ], rows.map(row => {
    const forecast = forecasts.get(key(row));
    return [productButton(row, true), esc(row.warehouse), n(row.on_hand), n(row.reserved),
      n(inbound.get(key(row)) || 0), n(row.capacity), forecast ? d(forecast.mean) : '—',
      forecast ? esc(modelNames[forecast.model] || forecast.model) : 'Pending'];
  }), 'compact-table') || empty('No inventory is available for this warehouse.');
}

function supplierTable(proposal) {
  const sorted = [...proposal.candidates].sort((a, b) => Number(b.feasible) - Number(a.feasible) || a.score - b.score);
  return table('Supplier cost comparison', ['Supplier', ['Lead'], ['Qty'], ['Landed'], ['Holding'], ['Shortage'], ['Total score'], 'Decision'],
    sorted.map(vendor => [
      esc(vendor.name), `${n(vendor.effective_lead)} d`, n(vendor.quantity), money(vendor.landed),
      money(vendor.holding_cost), money(vendor.shortage_cost), money(vendor.score),
      vendor.vendor_id === proposal.selected?.vendor_id ? chip('SELECTED')
        : vendor.feasible ? 'Alternative' : `${chip('INFEASIBLE')}<small>${esc(supplierConstraint(vendor))}</small>`,
    ]), 'compact-table supplier-table'
  );
}

function supplierConstraint(vendor) {
  if (vendor.constraint_reasons?.length) return vendor.constraint_reasons.join(' / ');
  const reasons = [];
  if (vendor.quantity < vendor.min_qty) reasons.push('Below minimum order');
  if (vendor.landed > 100000) reasons.push('Over per-order budget');
  return reasons.join(' / ') || 'Outside order constraints';
}

function supplierReason(proposal) {
  if (!proposal.selected) return 'No supplier met the order constraints.';
  const { selected } = proposal;
  return `${selected.name} had the lowest feasible estimated total cost (${money(selected.score)}).`;
}

function renderVendors() {
  const host = $('vendorTable');
  const groups = [...host.querySelectorAll('details')];
  const expanded = new Set(groups.filter(group => group.open).map(group => group.dataset.proposal));
  host.innerHTML = state.proposals.map((proposal, index) =>
    `<details class="supplier-group" data-proposal="${esc(key(proposal))}"${expanded.has(key(proposal)) || (!groups.length && index === 0) ? ' open' : ''}>
      <summary><strong>${esc(productName(proposal.sku))}</strong>
      <small>${esc(proposal.warehouse)} / ${esc(supplierReason(proposal))}</small></summary>${supplierTable(proposal)}
    </details>`
  ).join('') || empty((operationsMode ? state.last_run : currentCycle()) ? 'The last plan found no positions that needed purchasing.' : 'Run planning to compare suppliers for positions that need replenishment.', !currentCycle());
}

function renderOrders() {
  if (operationsMode) return renderOperationalOrders();
  const filter = $('orderFilter').value;
  const orders = state.orders.filter(order => !filter || order.status === filter).slice().reverse();
  $('orderCount').textContent = plural(orders.length, 'order');
  $('ordersTable').innerHTML = table('Execution ledger', ['Order ID', 'Type', 'Product / SKU', 'Destination', 'Supplier / source', ['Qty'], ['Total'], 'ETA', 'Status'],
    orders.map(order => [
      `<span class="ledger-id">${esc(order.id)}</span>`, order.kind === 'PURCHASE' ? 'Purchase' : 'Transfer',
      productButton(order, true), esc(order.warehouse), esc(order.vendor || order.source), n(order.quantity),
      order.kind === 'PURCHASE' ? money(order.total_inr) : '—', dateLabel(order.eta), chip(order.status),
    ]), 'compact-table'
  ) || empty(filter ? 'No orders match this status.' : 'There are no orders in the simulation ledger.', !filter && !currentCycle());
}

function renderTrace() {
  const run = state.last_run;
  const count = groups(state.log, 'agent').length;
  const scenario = run?.multiplier == null ? '' : ` Demand ${d(run.multiplier)}×, supplier delay ${plural(run.delay, 'day')}.`;
  $('traceCaption').textContent = run ? `Run on ${dateLabel(run.date)}.${scenario}` : 'Run a cycle to see the messages in execution order.';
  $('agentCount').textContent = count ? `${count} agents ran` : `${Object.keys(agentMethods).length} agents configured`;

  const expanded = new Set([...$('trace').querySelectorAll('details[open]')].map(entry => entry.dataset.traceIndex));
  if (state.log.length) {
    $('trace').innerHTML = `<ol class="trace-list">${state.log.map((entry, index) => {
      const [name, method] = agentMethods[entry.agent] || [entry.agent, ''];
      return `<li class="trace-entry"><span class="trace-mark">${icon('trace', 16)}</span>
        <details data-trace-index="${index}"${expanded.has(String(index)) ? ' open' : ''}>
          <summary>${esc(name)}</summary>
          <p class="trace-method">${esc(method)}</p>
        </details>
        <p class="trace-report">${esc(entry.message)}</p>
      </li>`;
    }).join('')}</ol>`;
  } else {
    $('trace').innerHTML = empty('The trace fills with actual agent messages after a run.', true)
      + `<ol class="trace-list">${Object.values(agentMethods).map(([name, method]) =>
        `<li class="trace-entry"><span class="trace-mark">${icon('trace', 16)}</span><details><summary>${esc(name)}</summary>
          <p class="trace-method">${esc(method)}</p></details></li>`).join('')}</ol>`;
  }
  $('anomalies').innerHTML = state.anomalies.length
    ? `<ul class="signal-list">${state.anomalies.map(item =>
      `<li>${esc(item.type)}${item.sku ? `<small>${esc(productName(item.sku))} / ${esc(item.warehouse)} / robust z ${d(item.robust_z)}</small>` : ''}</li>`
    ).join('')}</ul><p class="anomaly-context">Sales flags indicate unusual observations. Their cause is unverified.</p>`
    : empty(run ? 'No anomaly signals in the most recent run.' : 'Sales anomalies and scenario delays appear here after a run.');
}

function renderEvaluation() {
  $('forecastMetrics').innerHTML = table('Forecast backtest', ['Model', ['WAPE'], ['MAE']],
    (state.evaluation.forecast || []).map(row => [esc(modelNames[row.model] || row.model), percent(row.wape), d(row.mae)])
  ) || empty('The forecast evaluation file is unavailable.');
  const policy = state.evaluation.policy || [];
  const metrics = [
    ['Fill rate', row => percent(row.fill_rate)],
    ['Unmet units', row => n(row.lost_units)],
    ['Operating cost', row => money(row.operating_cost_inr)],
    ['Procurement spend', row => money(row.procurement_inr)],
    ['Closing inventory', row => n(row.closing_on_hand)],
  ];
  $('policyMetrics').innerHTML = policy.length
    ? table('Inventory policy replay', ['Metric', ...policy.map(row => [row.policy === 'threshold30' ? '30-unit threshold' : 'Agentic policy'])],
      metrics.map(([label, format]) => [label, ...policy.map(format)]))
    : empty('The inventory replay evaluation file is unavailable.');
}

function facts(entries) {
  return `<dl class="detail-facts">${entries.map(([label, value]) =>
    `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join('')}</dl>`;
}

function renderDecision() {
  const row = state.inventory.find(item => key(item) === decisionKey);
  if (!row) return;
  const forecast = state.forecasts.find(item => key(item) === decisionKey);
  const risk = state.risks.find(item => key(item) === decisionKey);
  const proposal = state.proposals.find(item => key(item) === decisionKey);
  const orders = state.orders.filter(item => item.sku === row.sku && (item.warehouse === row.warehouse || item.source === row.warehouse));
  const openInbound = orders.filter(item => item.status === 'OPEN' && item.warehouse === row.warehouse);
  $('decisionTitle').textContent = productName(row.sku);
  $('decisionLocation').textContent = `${row.warehouse} / SKU ${row.sku}`;
  const modelScores = Object.entries(forecast?.validation_wape ?? {}).sort(([, a], [, b]) => a - b);
  const forecastDetails = forecast
    ? `<p>${esc(modelNames[forecast.model] || forecast.model)} forecasts ${d(forecast.mean)} units per day on average over the first seven days.</p>
      ${modelScores.length ? facts(modelScores.map(([model, score]) => [modelNames[model] || model, percent(score)]))
        + '<p>Validation WAPE by model. Lower is better.</p>' : ''}`
    : '<p>Run planning to select a forecast model for this location.</p>';
  const riskDetails = risk
    ? `<p>${chip(risk.level)} Assessed before corrective actions.</p>${facts([
      ['Days of cover', `${d(risk.days_cover)} d`], ['Shortest supplier lead', `${n(risk.lead_days)} d`],
      ['Safety stock', `${n(risk.safety_stock)} units`], ['Expected shortfall', `${d(risk.expected_shortage)} units`],
    ])}` : '<p>Run planning to assess this position.</p>';
  const ledger = table('Orders for this location', ['Order', 'Route / supplier', ['Qty'], 'ETA', 'Status'],
    orders.slice().reverse().map(order => [
      `<span class="ledger-id">${esc(order.id)}</span>`,
      esc(order.kind === 'TRANSFER' ? `${order.source} → ${order.warehouse}` : order.vendor),
      n(order.quantity), dateLabel(order.eta), chip(order.status),
    ])
  ) || '<p>No ledger orders involve this location.</p>';
  $('decisionBody').innerHTML = `
    <section><h3>Current stock</h3>${facts([
      ['Available units', n(row.on_hand - row.reserved)], ['Open inbound units', n(sum(openInbound, remainingUnits))],
      ['Capacity', n(row.capacity)],
    ])}</section>
    <section><h3>Demand forecast</h3>${forecastDetails}</section>
    <section><h3>Stockout assessment</h3>${riskDetails}</section>
    <section><h3>Purchasing decision</h3>${proposal
      ? `<p>${esc(supplierReason(proposal))}</p>${supplierTable(proposal)}`
      : `<p>${currentCycle() ? 'No purchase proposal was needed after warehouse transfers and inbound stock were considered.'
        : 'Run planning to evaluate supplier options.'}</p>`}</section>
    <section><h3>Order execution</h3>${ledger}</section>`;
}

function render(data) {
  state = data;
  renderOverview();
  renderInventory();
  renderVendors();
  renderOrders();
  renderTrace();
  if (operationsMode) renderOperations();
  else renderEvaluation();
  if ($('decisionDialog').open) renderDecision();
}

function scenario() {
  return { multiplier: Number($('multiplier').value), delay: Number($('delay').value) };
}

function applyScenario([multiplier, delay]) {
  for (const [id, value] of [['multiplier', multiplier], ['delay', delay]]) {
    const field = $(id);
    if (![...field.options].some(option => option.value === value)) {
      field.add(new Option(id === 'delay' ? `+${value} days` : `${value}×`, value));
    }
    field.value = value;
  }
  syncControls();
}

function syncControls() {
  if (operationsMode) return syncOperationControls();
  const { multiplier, delay } = scenario();
  document.querySelectorAll('[data-mutation]').forEach(button => { button.disabled = !state || busy || autoActive || Boolean(offlineSnapshot); });
  document.querySelectorAll('[data-preset], #multiplier, #delay').forEach(control => {
    control.disabled = !state || busy || autoActive || Boolean(offlineSnapshot);
  });
  for (const [name, [factor, lag]] of Object.entries(presets)) {
    $(name).setAttribute('aria-pressed', multiplier === Number(factor) && delay === Number(lag));
  }
  $('auto').disabled = !state || (busy && !autoActive) || Boolean(offlineSnapshot);
  $('auto').textContent = autoActive ? 'Stop auto loop' : 'Start auto loop';
  $('scenarioHint').textContent = offlineSnapshot ? 'Start python app.py to run a new scenario.'
    : autoActive ? 'Auto loop alternates planning and day advancement.'
    : currentCycle() ? 'Today’s cycle is committed. Advance or reset to apply new inputs.'
    : 'Scenario inputs apply to the next agent cycle.';
}

async function request(path, options) {
  const response = await fetch('/api/' + path, { cache: 'no-store', ...options });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || `Request returned HTTP ${response.status}.`);
  }
  return response.json();
}

function showError(message, retry = false) {
  $('errorMessage').textContent = message;
  $('retry').hidden = !retry;
  $('error').hidden = false;
}

async function loadState() {
  if (busy) return;
  busy = true;
  $('error').hidden = true;
  syncControls();
  try {
    const firstLoad = !state;
    const data = await request(operationsMode ? workspaceEndpoint + '/state' : 'state');
    render(data);
    if (firstLoad && data.last_run?.multiplier != null) {
      applyScenario([String(data.last_run.multiplier), String(data.last_run.delay)]);
    }
  } catch {
    showError('Could not load the workspace. Check that python app.py is running, then retry.', true);
    if (!state) $('runStatus').textContent = 'Unavailable';
  } finally {
    busy = false;
    syncControls();
  }
}

function reportAction(message) {
  $('actionUpdate').textContent = message;
  $('actionUpdate').hidden = currentView === 'overview';
  if (currentView === 'overview') $('notice').textContent = message;
}

async function mutate(path) {
  if (!state || busy) return false;
  busy = true;
  $('error').hidden = true;
  if (path === 'cycle') {
    $('cycleLabel').textContent = 'Running agents…';
    $('runStatus').textContent = 'Running';
    reportAction('The agent cycle is running…');
  }
  syncControls();
  try {
    const result = await request(path, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(scenario()),
    });
    render(result);
    if (currentView !== 'overview' || result.last_action.startsWith('Cycle already executed')) reportAction(result.last_action);
    return true;
  } catch (error) {
    $('actionUpdate').hidden = true;
    renderWorkflow();
    showError(`Could not confirm the update. Refresh the simulation state before trying again. ${error.message}`, true);
    return false;
  } finally {
    $('cycleLabel').textContent = 'Run agent cycle';
    busy = false;
    syncControls();
  }
}

function act(path) {
  if (!state || busy || autoActive || offlineSnapshot) return;
  if (operationsMode) {
    if (path === 'cycle') void submitOperation('plan', {});
    return;
  }
  if (path === 'reset') {
    $('resetDialog').returnValue = '';
    $('resetDialog').showModal();
  } else {
    void mutate(path);
  }
}

function toggleAuto() {
  if (autoActive) {
    autoActive = false;
    clearTimeout(autoTimer);
    syncControls();
    return;
  }
  if (!state || busy || offlineSnapshot) return;
  autoActive = true;
  syncControls();
  autoTimer = setTimeout(autoTick, 6000);
}

async function autoTick() {
  if (!autoActive) return;
  const committed = await mutate(currentCycle() ? 'advance' : 'cycle');
  if (autoActive && committed) autoTimer = setTimeout(autoTick, 6000);
  else if (autoActive) toggleAuto();
}

for (const [name, values] of Object.entries(presets)) {
  $(name).addEventListener('click', () => applyScenario(values));
}
$('multiplier').addEventListener('change', syncControls);
$('delay').addEventListener('change', syncControls);
$('warehouseFilter').addEventListener('change', renderInventory);
$('orderFilter').addEventListener('change', renderOrders);
$('retry').addEventListener('click', loadState);
$('resetDialog').addEventListener('close', () => {
  if ($('resetDialog').returnValue === 'reset') void mutate('reset');
});
document.addEventListener('click', event => {
  const button = event.target.closest('[data-decision]');
  if (!button) return;
  decisionKey = button.dataset.decision;
  renderDecision();
  $('decisionDialog').showModal();
});
window.addEventListener('hashchange', syncLocation);
window.addEventListener('popstate', syncLocation);
new ResizeObserver(drawChart).observe($('chart'));
syncLocation();
if (offlineSnapshot) {
  render(JSON.parse(offlineSnapshot.textContent));
  syncControls();
} else if (location.protocol === 'file:') {
  showError('Open DEMO_PREVIEW.html for an offline snapshot, or run python app.py to use the app.');
  $('runStatus').textContent = 'Offline';
} else if (!operationsMode) {
  const exportLink = $('exportOrders');
  if (exportLink) exportLink.href = '/api/orders.csv';
  void loadState();
}
