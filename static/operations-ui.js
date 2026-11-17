'use strict';

const activityNames = {
  OPENING: 'Opening stock', SALE: 'Sale', STOCK_IN: 'Stock added', STOCK_OUT: 'Stock removed',
  DISPATCH: 'Transfer dispatched', RECEIPT: 'Order received', CANCEL: 'Purchase balance cancelled',
};
const submittedRequests = new WeakMap();

function readyPositions() {
  return state?.coverage.filter(position => position.ready).length ?? 0;
}

function canRelease() {
  return currentCycle() && state.last_run.status === 'DRAFT';
}

function renderPlanningWorkflow() {
  const run = state.last_run;
  const ready = readyPositions();
  const agentCount = groups(state.log, 'agent').length;
  let status = 'Setup required';
  let note = 'Import stock, supplier terms and completed sales to start planning.';
  let signal = 'neutral';
  if (ready) {
    status = 'Ready to plan';
    note = 'Plan replenishment to evaluate stockout risks, transfers and purchases.';
  }
  if (run) {
    if (canRelease()) {
      status = 'Draft ready';
      note = 'Review the draft in Orders to release purchases and dispatch transfers.';
      signal = 'warn';
    } else if (run.status === 'RELEASED') {
      status = 'Plan released';
      note = 'Follow open orders in the ledger and record receipts when deliveries arrive.';
      signal = 'good';
    } else if (currentCycle()) {
      status = 'Plan complete';
      note = 'The agents completed planning without creating new orders.';
      signal = 'good';
    } else {
      status = 'Replan needed';
      note = 'Run planning again to include the latest workspace inputs.';
      signal = 'warn';
    }
  }
  $('runStatus').className = `chip ${signal}`;
  $('runStatus').textContent = status;
  $('workflowCaption').textContent = run
    ? `${agentCount} agents ran on ${dateLabel(run.date)}.${run.excluded ? ' ' + plural(run.excluded, 'location') + ' awaiting data.' : ''}`
    : 'Specialist agents use warehouse stock and recorded demand to plan replenishment.';
  renderStages([
    ['observe', 'Observe', plural(run?.positions ?? state.inventory.length, 'stock position'), 'Check stock and open inbound units'],
    ['forecast', 'Forecast', run ? plural(state.forecasts.length, 'forecast') : '21-day outlook', 'Forecast from completed sales days'],
    ['decide', 'Decide', run ? plural(run.proposals, 'purchase proposal') : 'Replenishment', 'Assess risk, transfers and supplier costs'],
    ['execute', 'Review', run ? plural(run.purchases + run.transfers, 'order') : 'Order drafts',
      run ? `${plural(run.purchases, 'purchase')} / ${plural(run.transfers, 'transfer')}` : 'Release an approved plan to the ledger'],
  ]);
  $('notice').textContent = note;
}

function renderOperations() {
  const workspaceName = practiceMode ? 'Practice workspace' : state.settings.name;
  $('workspaceName').textContent = workspaceName;
  $('workspaceName').title = workspaceName;
  $('workingDate').textContent = dateLabel(state.date);
  $('readinessCounts').innerHTML = [
    ['Stock locations', state.inventory.length],
    ['Supplier terms', state.vendors.length],
    ['Ready to plan', readyPositions()],
  ].map(([label, count]) => `<div><dt>${label}</dt><dd>${n(count)}</dd></div>`).join('');
  $('manageDataLink').textContent = state.inventory.length ? 'Manage workspace data' : 'Set up workspace';
  $('reviewDraftLink').hidden = !canRelease();
  $('exportOrders').href = '/api/' + workspaceEndpoint + '/orders.csv';
  $('supplierCaption').textContent = `Suppliers are evaluated for capacity, order minimums, lot sizes and the ${money(state.last_run?.budget ?? state.settings.budget)} per-order budget.`;
  updateFieldDefault($('settingsName'), state.settings.name);
  updateFieldDefault($('settingsBudget'), state.settings.budget);

  $('planningCoverage').innerHTML = table('Planning coverage', ['Product / SKU', 'Warehouse', ['Days covered'], 'Latest completed sales', 'Readiness'],
    state.coverage.map(row => [productButton(row, true), esc(row.warehouse), n(row.days),
      row.last_sale_date ? dateLabel(row.last_sale_date) : '—',
      row.ready ? '<span class="chip good">Ready</span>' : esc(row.reasons.join('. ')),
    ]), 'compact-table') || empty('Import product and inventory CSVs to add warehouse locations.');

  $('supplierDirectory').innerHTML = table('Supplier terms', ['Supplier', 'Product', ['Lead time'], ['Unit price'], ['Minimum'], ['Lot size'], ['Maximum'], ['Reliability']],
    state.vendors.map(row => [esc(row.name) + `<small>${esc(row.vendor_id)}</small>`, esc(productName(row.sku)),
      `${n(row.lead_days)} d`, money(row.unit_price_inr), n(row.min_qty), n(row.lot_size), n(row.max_qty), percent(row.reliability),
    ]), 'compact-table') || empty('Import supplier terms to compare sourcing options.');

  $('stockActivity').innerHTML = table('Stock activity', ['Date', 'Activity', 'Product', 'Warehouse', ['Quantity'], ['Stock change'], 'Reference'],
    state.activity.map(row => [dateLabel(row.date), esc(activityNames[row.kind]), esc(productName(row.sku)), esc(row.warehouse),
      n(row.quantity), row.delta > 0 ? '+' + n(row.delta) : n(row.delta), esc(row.reference),
    ]), 'compact-table') || empty('Opening balances and recorded transactions will appear here.');
}

function updateFieldDefault(field, value) {
  const original = field.defaultValue;
  const untouched = field.value === original;
  field.defaultValue = value;
  if (untouched) field.value = value;
}

function renderOperationalOrders() {
  const run = state.last_run;
  const filter = $('orderFilter').value;
  const orders = state.orders.filter(order => filter ? order.status === filter : order.status !== 'SUPERSEDED').slice().reverse();
  const showPlan = run && ['DRAFT', 'SUPERSEDED'].includes(run.status);
  $('releaseBar').hidden = !showPlan;
  $('reviewRelease').hidden = !canRelease();
  if (showPlan) {
    $('releaseTitle').textContent = canRelease() ? `Order plan ${run.id} is ready for review` : 'A fresh plan is required';
    $('releaseDescription').textContent = canRelease()
      ? `${plural(run.purchases, 'purchase order')} and ${plural(run.transfers, 'transfer')} awaiting release.`
      : 'The previous drafts expired or workspace inputs changed. Run planning to get current orders.';
  }
  $('orderCount').textContent = plural(orders.length, 'order');
  $('ordersTable').innerHTML = table('Order ledger',
    ['Order ID', 'Product / SKU', 'Supplier / route', ['Ordered'], ['Received'], ['Landed'], 'ETA', 'Status', 'Actions'],
    orders.map(order => {
      const route = order.kind === 'PURCHASE' ? order.vendor : `${order.source} to ${order.warehouse}`;
      const late = order.status === 'OPEN' && order.eta < state.date;
      const status = order.status === 'OPEN' && order.received_quantity > 0
        ? '<span class="chip OPEN">Part received</span>' : chip(order.status);
      return [
        `<span class="ledger-id">${esc(order.id)}</span><small>${order.kind === 'PURCHASE' ? 'Purchase' : 'Transfer'}</small>`,
        productButton(order, true), `${esc(route)}<small>Destination ${esc(order.warehouse)}</small>`,
        n(order.quantity), n(order.received_quantity), order.kind === 'PURCHASE' ? money(order.total_inr) : '—',
        `${dateLabel(order.eta)}${late ? '<small class="overdue">Overdue</small>' : ''}`,
        status, orderActions(order),
      ];
    }), 'compact-table'
  ) || empty(filter ? 'No orders match this status.' : 'Drafts will appear here after a replenishment plan.');
  syncOperationControls();
}

function orderActions(order) {
  const actions = [];
  if (order.status === 'OPEN') {
    actions.push(`<button type="button" class="text-button" data-mutation data-receive="${esc(order.id)}">Receive</button>`);
    if (order.kind === 'PURCHASE') {
      actions.push(`<button type="button" class="text-button" data-mutation data-cancel-order="${esc(order.id)}">Cancel</button>`);
    }
  }
  if (['OPEN', 'RECEIVED', 'CANCELLED'].includes(order.status)) {
    actions.push(`<a class="text-link" href="/api/${workspaceEndpoint}/orders.csv?id=${encodeURIComponent(order.id)}" download>CSV</a>`);
  }
  return actions.length ? `<div class="order-actions">${actions.join('')}</div>` : '—';
}

function syncOperationControls() {
  const locked = !state || busy;
  const ready = readyPositions() > 0;
  document.querySelectorAll('[data-mutation], [data-close-operation]').forEach(button => { button.disabled = locked; });
  document.querySelectorAll('button[onclick="act(\'cycle\')"]').forEach(button => {
    button.disabled = locked || !ready;
  });
  $('recordStock').disabled = locked || !state.inventory.length;
  $('recordStock').hidden = Boolean(state && !state.inventory.length);
  $('runCycle').hidden = Boolean(state && !ready);
  $('setupWorkspace').hidden = !state || ready || currentView === 'data';
  $('reviewRelease').disabled = locked || !canRelease();
}

async function submitOperation(action, payload, form) {
  if (busy || !state) return false;
  const formError = form?.querySelector('.form-error');
  if (formError) formError.hidden = true;
  busy = true;
  $('error').hidden = true;
  $('actionUpdate').hidden = true;
  if (action === 'plan') {
    $('cycleLabel').textContent = 'Planning…';
    $('runStatus').textContent = 'Planning';
    reportAction('Agents are checking stock, forecasting demand and comparing suppliers.');
  }
  syncControls();
  try {
    const result = await request(workspaceEndpoint + '/' + action, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    render(result);
    if (form?.closest('dialog')) form.closest('dialog').close();
    if (form) submittedRequests.delete(form);
    reportAction(result.last_action);
    return true;
  } catch (error) {
    renderWorkflow();
    $('actionUpdate').hidden = true;
    if (formError) {
      formError.textContent = error.message;
      formError.hidden = false;
    } else {
      showError(error.message, true);
    }
    return false;
  } finally {
    busy = false;
    $('cycleLabel').textContent = 'Plan replenishment';
    syncControls();
  }
}

function formPayload(form) {
  return Object.fromEntries(new FormData(form));
}

function addToken(form, values) {
  const fingerprint = JSON.stringify(values);
  let previous = submittedRequests.get(form);
  if (previous?.fingerprint !== fingerprint) {
    previous = { fingerprint, token: crypto.randomUUID() };
    submittedRequests.set(form, previous);
  }
  return { ...values, token: previous.token };
}

function openOperation(formId) {
  const form = $(formId);
  const error = form.querySelector('.form-error');
  error.hidden = true;
  form.closest('dialog').showModal();
}

if (operationsMode && location.protocol !== 'file:') {
  $('cycleLabel').textContent = 'Plan replenishment';
  $('recordStock').addEventListener('click', () => {
    $('stockForm').reset();
    $('stockPosition').replaceChildren(...state.inventory.map(row =>
      new Option(`${productName(row.sku)} / ${row.warehouse}`, key(row))
    ));
    openOperation('stockForm');
  });

  $('stockForm').addEventListener('submit', event => {
    event.preventDefault();
    const form = event.currentTarget;
    const { position, ...values } = formPayload(form);
    const [sku, warehouse] = position.split('/');
    void submitOperation('activity', addToken(form, { ...values, sku, warehouse }), form);
  });

  $('settingsForm').addEventListener('submit', event => {
    event.preventDefault();
    void submitOperation('settings', formPayload(event.currentTarget), event.currentTarget);
  });

  $('importForm').addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const uploads = [...new FormData(form)].filter(([, file]) => file?.size);
    if (!uploads.length || sum(uploads, ([, file]) => file.size) > 4 * 1024 * 1024) {
      showError('Select CSV files with a combined size of 4 MB or less.');
      return;
    }
    try {
      const files = Object.fromEntries(await Promise.all(uploads.map(async ([kind, file]) => [kind, await file.text()])));
      if (await submitOperation('import', { files }, form)) form.reset();
    } catch {
      showError('Could not read the selected files.');
    }
  });

  $('reviewRelease').addEventListener('click', () => {
    if (!canRelease()) return;
    const run = state.last_run;
    $('releaseForm').dataset.planId = run.id;
    $('releaseSummary').textContent = `Release ${plural(run.purchases, 'purchase order')} and ${plural(run.transfers, 'transfer')} to the ledger. Export released purchase orders to share with suppliers.`;
    $('releaseSummary').nextElementSibling.hidden = run.transfers === 0;
    openOperation('releaseForm');
  });

  $('releaseForm').addEventListener('submit', event => {
    event.preventDefault();
    const form = event.currentTarget;
    void submitOperation('release', { plan_id: form.dataset.planId }, form);
  });

  $('receiptForm').addEventListener('submit', event => {
    event.preventDefault();
    const form = event.currentTarget;
    void submitOperation('receipt', addToken(form, { id: form.dataset.orderId, ...formPayload(form) }), form);
  });

  $('cancelForm').addEventListener('submit', event => {
    event.preventDefault();
    const form = event.currentTarget;
    void submitOperation('cancel', { id: form.dataset.orderId }, form);
  });
  $('restorePractice').addEventListener('click', () => openOperation('restoreForm'));
  $('restoreForm').addEventListener('submit', event => {
    event.preventDefault();
    if (practiceMode) void submitOperation('restore', {}, event.currentTarget);
  });

  document.querySelectorAll('[data-close-operation]').forEach(button => {
    button.addEventListener('click', () => button.closest('dialog').close());
  });
  document.querySelectorAll('.operation-dialog').forEach(dialog => {
    dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
  });

  $('ordersTable').addEventListener('click', event => {
    const action = event.target.closest('[data-receive], [data-cancel-order]');
    if (!action) return;
    const order = state.orders.find(row => row.id === (action.dataset.receive || action.dataset.cancelOrder));
    if (!order || order.status !== 'OPEN') return;
    const remaining = remainingUnits(order);
    if (action.dataset.receive) {
      const form = $('receiptForm');
      form.reset();
      form.dataset.orderId = order.id;
      form.elements.quantity.max = remaining;
      form.elements.quantity.value = remaining;
      $('receiptOrder').textContent = `${order.id} / ${plural(remaining, 'unit')} remaining at ${order.warehouse}`;
      openOperation('receiptForm');
    } else {
      $('cancelForm').dataset.orderId = order.id;
      $('cancelSummary').textContent = `Confirm cancellation with ${order.vendor} before removing the remaining ${plural(remaining, 'unit')} from ${order.id}. Already received stock stays in inventory.`;
      openOperation('cancelForm');
    }
  });
  void loadState();
}
