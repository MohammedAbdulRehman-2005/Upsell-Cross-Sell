/**
 * Plural Commerce Automation - Application Controller
 * Handles routing, view rendering, state management, and real-time backend updates.
 */

// Application State Store
const state = {
  activeView: 'overview',
  providerStatus: null,
  overviewMetrics: null,
  campaigns: [],
  filteredCampaigns: [],
  selectedCampaignId: null,
  selectedCampaignDetail: null,
  simulatorMessages: [],
  orders: [],
  customers: [],
  products: [],
  selectedProductDetail: null,
  isDispatching: false,
  ordersCustomersTab: 'orders',
  campaignStatusFilter: 'ALL',
  campaignSearchQuery: '',
  productSearchQuery: '',
  isProfileOpen: false,
};

// Utilities
const $ = (id) => document.getElementById(id);
const esc = (str) =>
  String(str ?? '')
    .replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function formatTime(isoString) {
  if (!isoString) return '—';
  try {
    const d = new Date(isoString);
    return isNaN(d.getTime()) ? String(isoString) : d.toLocaleString();
  } catch {
    return String(isoString);
  }
}

function formatShortTime(isoString) {
  if (!isoString) return '';
  try {
    const d = new Date(isoString);
    return isNaN(d.getTime()) ? '' : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch {
    return '';
  }
}

function getBadgeHtml(status) {
  const s = String(status || '').toUpperCase();
  let cls = 'badge-suppressed';
  if (s === 'READY_FOR_DISPATCH') cls = 'badge-ready';
  else if (s === 'SENT_SIMULATED') cls = 'badge-simulated';
  else if (s === 'PROVIDER_ACCEPTED') cls = 'badge-accepted';
  else if (s === 'DELIVERED') cls = 'badge-delivered';
  else if (s === 'READ') cls = 'badge-read';
  else if (s.includes('FAILED') || s === 'DELIVERY_UNKNOWN') cls = 'badge-failed';
  return `<span class="badge ${cls}">${esc(s)}</span>`;
}

function getProvenanceBadgeHtml(prov) {
  const p = String(prov || '').toUpperCase();
  if (p === 'SYNTHETIC_FIXTURE') return `<span class="badge badge-prov-synthetic">SYNTHETIC_FIXTURE</span>`;
  if (p === 'VERIFIED_CATALOGUE') return `<span class="badge badge-prov-verified">VERIFIED_CATALOGUE</span>`;
  return `<span class="badge badge-prov-workbook">WORKBOOK_ONLY</span>`;
}

// Toast Notifications
function showToast(message, type = 'info') {
  const container = $('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `<span>${esc(message)}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transition = 'opacity 0.3s ease';
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

// Confirmation Modal Dialog
let confirmCallback = null;
function openConfirmModal({ title, message, confirmText = 'Confirm', isDanger = false, onConfirm }) {
  $('confirm-title').textContent = title;
  $('confirm-message').textContent = message;
  const btn = $('confirm-action-btn');
  btn.textContent = confirmText;
  btn.className = `btn ${isDanger ? 'btn-danger' : 'btn-primary'}`;
  confirmCallback = onConfirm;
  $('confirm-modal').classList.add('active');
}

function closeConfirmModal() {
  $('confirm-modal').classList.remove('active');
  confirmCallback = null;
}

$('confirm-action-btn').addEventListener('click', async () => {
  if (typeof confirmCallback === 'function') {
    const cb = confirmCallback;
    closeConfirmModal();
    await cb();
  } else {
    closeConfirmModal();
  }
});
$('confirm-cancel-btn').addEventListener('click', closeConfirmModal);

// Navigation View Routing & Information Architecture
function normalizeViewName(raw) {
  if (!raw) return 'overview';
  const lower = String(raw).toLowerCase().trim();
  if (lower === 'whatsapp-inbox' || lower === 'inbox') return 'inbox';
  if (lower === 'orders' || lower === 'orders-customers') return 'orders-customers';
  if (lower === 'campaigns') return 'campaigns';
  if (lower === 'settings') return 'settings';
  return 'overview';
}

function getViewFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const viewParam = params.get('view');
  return normalizeViewName(viewParam);
}

function updateUrlForView(viewName, replace = false) {
  let targetUrl = '/simulator';
  if (viewName === 'inbox') {
    targetUrl = '/simulator?view=whatsapp-inbox';
  } else if (viewName === 'orders-customers') {
    targetUrl = '/simulator?view=orders-customers';
  } else if (viewName === 'campaigns') {
    targetUrl = '/simulator?view=campaigns';
  } else if (viewName === 'settings') {
    targetUrl = '/simulator?view=settings';
  }

  const currentPath = window.location.pathname + window.location.search;
  if (currentPath !== targetUrl) {
    if (replace) {
      window.history.replaceState({ view: viewName }, '', targetUrl);
    } else {
      window.history.pushState({ view: viewName }, '', targetUrl);
    }
  }
}

function switchView(viewName, updateHistory = true) {
  const normalized = normalizeViewName(viewName);
  state.activeView = normalized;

  document.querySelectorAll('.nav-link').forEach((link) => {
    const isTarget = link.dataset.view === normalized;
    link.classList.toggle('active', isTarget);
  });
  document.querySelectorAll('.view-pane').forEach((pane) => {
    pane.classList.toggle('active', pane.id === `view-${normalized}`);
  });

  const titles = {
    overview: 'Overview Dashboard',
    campaigns: 'Campaign Management',
    inbox: 'WhatsApp Commerce Inbox',
    'orders-customers': 'Orders & Customers',
    settings: 'Settings & Integration Status',
  };
  $('topbar-title').textContent = titles[normalized] || 'Dashboard';

  if (updateHistory) {
    updateUrlForView(normalized, false);
  }

  // Load view-specific data
  if (normalized === 'overview') loadOverview();
  else if (normalized === 'campaigns') loadCampaigns();
  else if (normalized === 'inbox') loadInbox();
  else if (normalized === 'orders-customers') loadOrdersAndCustomers();
  else if (normalized === 'settings') loadSettings();
}
window.switchView = switchView;

// Browser Back and Forward Navigation Listener
window.addEventListener('popstate', (e) => {
  const targetView = (e.state && e.state.view) ? normalizeViewName(e.state.view) : getViewFromUrl();
  switchView(targetView, false);
});

document.querySelectorAll('.nav-link').forEach((link) => {
  link.addEventListener('click', (e) => {
    e.preventDefault();
    switchView(link.dataset.view);
  });
});

// Topbar Global Actions
$('btn-refresh').addEventListener('click', async () => {
  showToast('Refreshing dashboard data...', 'info');
  await loadProviderStatus();
  switchView(state.activeView);
});

$('btn-demo-seed').addEventListener('click', () => {
  openConfirmModal({
    title: 'Seed Demo Campaigns',
    message:
      'This will idempotently seed 3 realistic demo orders (1 simulated sent conversation, 1 ready for dispatch, 1 suppressed) using synthetic catalogue metadata for demonstration purposes. Proceed?',
    confirmText: 'Seed Demo Data',
    onConfirm: async () => {
      try {
        const res = await window.api.seedDemoCampaigns();
        showToast(res.detail || 'Demo campaigns seeded successfully.', 'success');
        await loadOverview();
        if (state.activeView === 'inbox') await loadInbox();
        else if (state.activeView === 'campaigns') await loadCampaigns();
      } catch (err) {
        showToast(`Seed failed: ${err.message}`, 'error');
      }
    },
  });
});

// Global Provider Status Check
async function loadProviderStatus() {
  try {
    const status = await window.api.getProviderStatus();
    state.providerStatus = status;

    const el = $('provider-indicator-pill');
    if (el) {
      el.style.display = 'none';
    }
    const sidebarBadge = $('sidebar-inbox-badge');

    if (status.active_provider.includes('SIMULATOR')) {
      el.className = 'provider-indicator simulated';
      el.innerHTML = `<span class="env-dot" style="background:#059669"></span><span>SIMULATOR MODE</span>`;
      if (sidebarBadge) {
        sidebarBadge.textContent = 'SIM';
        sidebarBadge.style.display = 'inline-block';
        sidebarBadge.style.background = '#065f46';
        sidebarBadge.style.color = '#34d399';
        sidebarBadge.title = 'Simulator Mode Active';
      }
    } else if (status.is_configured) {
      el.className = 'provider-indicator meta-live';
      el.innerHTML = `<span class="env-dot" style="background:#2563eb"></span><span>META CLOUD API (CONFIGURED)</span>`;
      if (sidebarBadge) {
        sidebarBadge.textContent = 'LIVE';
        sidebarBadge.style.display = 'inline-block';
        sidebarBadge.style.background = '#1e40af';
        sidebarBadge.style.color = '#93c5fd';
        sidebarBadge.title = 'Meta WhatsApp Cloud API Live';
      }
    } else {
      el.className = 'provider-indicator meta-unconfigured';
      el.innerHTML = `<span class="env-dot" style="background:#d97706"></span><span>META API (NOT CONFIGURED)</span>`;
      if (sidebarBadge) {
        sidebarBadge.textContent = 'NOT CONFIGURED';
        sidebarBadge.style.display = 'inline-block';
        sidebarBadge.style.background = '#92400e';
        sidebarBadge.style.color = '#fcd34d';
        sidebarBadge.title = 'Meta API Not Configured';
      }
    }
  } catch (err) {
    console.error('Failed to load provider status:', err);
  }
}

// ==========================================================================
// VIEW 1: Overview
// ==========================================================================
async function loadOverview() {
  const container = $('overview-content');
  container.innerHTML = `<div class="empty-state"><p>Loading authoritative dashboard metrics...</p></div>`;
  try {
    const metrics = await window.api.getOverviewMetrics();
    state.overviewMetrics = metrics;

    const total = metrics.total_campaigns || 0;
    const ready = metrics.ready_for_dispatch || 0;
    const sim = metrics.sent_simulated || 0;
    const accepted = metrics.provider_accepted || 0;
    const delivered = metrics.delivered || 0;
    const read = metrics.read || 0;
    const suppressed = metrics.suppressed || 0;
    const failed = (metrics.failed_permanent || 0) + (metrics.failed_retryable || 0) + (metrics.delivery_unknown || 0);

    const readyPct = total ? ((ready / total) * 100).toFixed(0) : 0;
    const simPct = total ? ((sim / total) * 100).toFixed(0) : 0;
    const delivPct = total ? ((delivered / total) * 100).toFixed(0) : 0;
    const suppPct = total ? ((suppressed / total) * 100).toFixed(0) : 0;

    let recentTableHtml = '<p class="text-muted" style="padding:16px;">No recent campaigns found. Click "Load Demo Data" above to generate demo walkthrough records.</p>';
    if (metrics.recent_campaigns && metrics.recent_campaigns.length > 0) {
      recentTableHtml = `
        <table class="data-table">
          <thead>
            <tr>
              <th>Order ID</th>
              <th>Customer</th>
              <th>Phone</th>
              <th>Status</th>
              <th>Recommended SKUs</th>
              <th>Created At</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            ${metrics.recent_campaigns
              .map(
                (c) => `
              <tr>
                <td class="td-strong">${esc(c.external_order_id)}</td>
                <td>${esc(c.external_customer_id)}</td>
                <td>${esc(c.recipient_phone || '—')}</td>
                <td>${getBadgeHtml(c.delivery_status)}</td>
                <td><span style="font-size:12px;color:#0284c7;font-weight:600;">${(c.recommended_skus || []).join(', ') || '—'}</span></td>
                <td>${formatShortTime(c.created_at)}</td>
                <td>
                  <button class="btn btn-secondary btn-sm" onclick="openCampaignFromOverview(${c.campaign_id})">View</button>
                </td>
              </tr>
            `
              )
              .join('')}
          </tbody>
        </table>
      `;
    }

    container.innerHTML = `
      <!-- KPI Row -->
      <div class="kpi-grid">
        <div class="kpi-card">
          <div class="kpi-top">
            <span class="kpi-label">Total Campaigns</span>
            <div class="kpi-icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"></path><polyline points="22,6 12,13 2,6"></polyline></svg></div>
          </div>
          <div class="kpi-value">${total}</div>
          <div class="kpi-sub">${metrics.total_orders} total customer orders</div>
        </div>

        <div class="kpi-card">
          <div class="kpi-top">
            <span class="kpi-label">Ready for Dispatch</span>
            <div class="kpi-icon" style="background:#fef3c7;color:#d97706;"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 14 14"></polyline></svg></div>
          </div>
          <div class="kpi-value" style="color:#d97706;">${ready}</div>
          <div class="kpi-sub">Eligible post-delivery campaigns</div>
        </div>

        <div class="kpi-card">
          <div class="kpi-top">
            <span class="kpi-label">Simulator Sent</span>
            <div class="kpi-icon" style="background:#e0f2fe;color:#0284c7;"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg></div>
          </div>
          <div class="kpi-value" style="color:#0284c7;">${sim}</div>
          <div class="kpi-sub">Simulated demo conversations</div>
        </div>

        <div class="kpi-card">
          <div class="kpi-top">
            <span class="kpi-label">Meta Cloud Accepted</span>
            <div class="kpi-icon" style="background:#ede9fe;color:#7c3aed;"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"></polyline></svg></div>
          </div>
          <div class="kpi-value" style="color:#7c3aed;">${accepted}</div>
          <div class="kpi-sub">HTTP 200 provider acceptance</div>
        </div>

        <div class="kpi-card">
          <div class="kpi-top">
            <span class="kpi-label">Confirmed Delivered</span>
            <div class="kpi-icon" style="background:#dcfce7;color:#16a34a;"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg></div>
          </div>
          <div class="kpi-value" style="color:#16a34a;">${delivered}</div>
          <div class="kpi-sub">${read} confirmed read by customer</div>
        </div>

        <div class="kpi-card">
          <div class="kpi-top">
            <span class="kpi-label">Suppressed</span>
            <div class="kpi-icon" style="background:#f1f5f9;color:#64748b;"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"></line></svg></div>
          </div>
          <div class="kpi-value" style="color:#64748b;">${suppressed}</div>
          <div class="kpi-sub">Consent or eligibility filtered</div>
        </div>
      </div>

      <!-- Provider Status Banner -->
      <div class="card" style="border-left: 4px solid ${metrics.is_provider_configured ? '#10b981' : '#f59e0b'};">
        <div class="card-header">
          <div>
            <h3 class="card-title">Active WhatsApp Messaging Provider: ${esc(metrics.active_provider)}</h3>
            <p class="card-subtitle">${
              metrics.active_provider.includes('SIMULATOR')
                ? 'Interactive local WhatsApp customer simulator is active. All messages are stored in local database with explicit SIMULATED provenance.'
                : metrics.is_provider_configured
                ? 'Meta WhatsApp Cloud API credentials and template settings are configured.'
                : 'Meta WhatsApp Cloud API is selected but credentials are not configured.'
            }</p>
          </div>
          <button class="btn btn-secondary btn-sm" onclick="switchView('settings')">Provider Settings</button>
        </div>
      </div>

      <!-- Recent Campaigns Table -->
      <div class="card">
        <div class="card-header">
          <div>
            <h3 class="card-title">Recent Post-Delivery Upsell Campaigns</h3>
            <p class="card-subtitle">Real-time status across order-delivery triggers and recommendation allocations</p>
          </div>
          <button class="btn btn-secondary btn-sm" onclick="switchView('campaigns')">View All Campaigns</button>
        </div>
        <div class="table-container" style="border:none;border-radius:0;">
          ${recentTableHtml}
        </div>
      </div>
    `;
  } catch (err) {
    container.innerHTML = `<div class="empty-state"><p class="text-danger">Failed to load overview: ${esc(err.message)}</p></div>`;
  }
}

window.openCampaignFromOverview = function (campaignId) {
  switchView('campaigns');
  openCampaignDrawer(campaignId);
};

// ==========================================================================
// VIEW 2: Campaign Management
// ==========================================================================
async function loadCampaigns() {
  const container = $('campaigns-table-container');
  container.innerHTML = `<div class="empty-state"><p>Loading campaign records...</p></div>`;
  try {
    const list = await window.api.getCampaigns();
    state.campaigns = list;
    applyCampaignFilters();
  } catch (err) {
    container.innerHTML = `<div class="empty-state"><p class="text-danger">Failed to load campaigns: ${esc(err.message)}</p></div>`;
  }
}

function applyCampaignFilters() {
  const q = (state.campaignSearchQuery || '').toLowerCase().trim();
  const statusFilter = state.campaignStatusFilter;

  state.filteredCampaigns = state.campaigns.filter((c) => {
    const matchesQuery =
      !q ||
      c.external_order_id.toLowerCase().includes(q) ||
      c.external_customer_id.toLowerCase().includes(q) ||
      (c.recipient_phone && c.recipient_phone.includes(q)) ||
      (c.recommended_skus || []).some((sku) => sku.toLowerCase().includes(q));

    const matchesStatus = statusFilter === 'ALL' || c.delivery_status === statusFilter;
    return matchesQuery && matchesStatus;
  });

  renderCampaignsTable();
}

function renderCampaignsTable() {
  const container = $('campaigns-table-container');
  const items = state.filteredCampaigns;

  if (!items || items.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        <h4>No matching campaigns found</h4>
        <p>Try adjusting your search query or status filter, or click "Load Demo Data" above.</p>
      </div>
    `;
    return;
  }

  container.innerHTML = `
    <table class="data-table">
      <thead>
        <tr>
          <th>Campaign ID</th>
          <th>Order Reference</th>
          <th>Customer</th>
          <th>Recipient Phone</th>
          <th>Status</th>
          <th>Recommendations</th>
          <th>Provider Info</th>
          <th>Created</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody>
        ${items
          .map((c) => {
            const isEligibleForDispatch =
              c.delivery_status === 'READY_FOR_DISPATCH' || c.delivery_status === 'FAILED_RETRYABLE';
            return `
            <tr>
              <td><code>#${c.campaign_id}</code></td>
              <td class="td-strong">${esc(c.external_order_id)}</td>
              <td>${esc(c.external_customer_id)}</td>
              <td>${esc(c.recipient_phone || '—')}</td>
              <td>${getBadgeHtml(c.delivery_status)}</td>
              <td>
                <span style="color:#0284c7;font-weight:600;font-size:12px;">
                  ${(c.recommended_skus || []).join(', ') || '<span style="color:#94a3b8;">None (Suppressed)</span>'}
                </span>
              </td>
              <td>
                <span style="font-size:11.5px;color:#475569;">
                  ${esc(c.provider || '—')}
                  ${c.provider_message_id ? `<br><code style="font-size:10px;">${esc(c.provider_message_id.slice(0, 18))}...</code>` : ''}
                </span>
              </td>
              <td>${formatShortTime(c.created_at)}</td>
              <td>
                <div style="display:flex;gap:6px;">
                  <button class="btn btn-secondary btn-sm" onclick="openCampaignDrawer(${c.campaign_id})">Details</button>
                  ${
                    isEligibleForDispatch
                      ? `<button class="btn btn-primary btn-sm" onclick="confirmDispatch(${c.campaign_id}, ${c.delivery_status === 'FAILED_RETRYABLE'})">${c.delivery_status === 'FAILED_RETRYABLE' ? 'Retry' : 'Dispatch'}</button>`
                      : ''
                  }
                </div>
              </td>
            </tr>
          `;
          })
          .join('')}
      </tbody>
    </table>
  `;
}

// Campaign Filters Event Listeners
$('campaign-search').addEventListener('input', (e) => {
  state.campaignSearchQuery = e.target.value;
  applyCampaignFilters();
});

$('campaign-status-filter').addEventListener('change', (e) => {
  state.campaignStatusFilter = e.target.value;
  applyCampaignFilters();
});

// Campaign Detail & Decision Trace Drawer
async function openCampaignDrawer(campaignId) {
  $('trace-modal').classList.add('active');
  $('trace-modal-body').innerHTML = `<p>Loading campaign detail and recommendation decision trace...</p>`;
  try {
    const detail = await window.api.getCampaignDetail(campaignId);
    state.selectedCampaignDetail = detail;

    const cardsHtml = (detail.recommendation_cards || [])
      .map(
        (card) => `
      <div style="display:flex;align-items:center;justify-content:space-between;padding:8px 12px;background:#ffffff;border:1px solid #e2e8f0;border-radius:6px;margin-bottom:6px;">
        <div>
          <span style="font-weight:600;color:#0f172a;font-size:13px;">${card.slot_number}. ${esc(card.product_name)}</span>
          <div style="font-size:11.5px;color:#64748b;">
            SKU: <code>${esc(card.product_code)}</code> | From Source: <code>${esc(card.source_product_code || '—')}</code> (Rank #${card.source_rank || '—'})
          </div>
        </div>
        <div style="text-align:right;">
          <div style="font-weight:600;color:#059669;font-size:13px;">${card.unit_price ? '₹' + card.unit_price.toFixed(2) : 'Price on request'}</div>
          <div>${getProvenanceBadgeHtml(card.metadata_source)}</div>
        </div>
      </div>
    `
      )
      .join('');

    const trace = detail.decision_trace || {};
    const traceJson = JSON.stringify(trace, null, 2);

    $('trace-modal-title').textContent = `Campaign #${detail.campaign_id} — ${detail.external_order_id}`;
    $('trace-modal-body').innerHTML = `
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px;">
        <div class="detail-row"><span class="detail-label">Delivery Status:</span><span class="detail-val">${getBadgeHtml(detail.delivery_status)}</span></div>
        <div class="detail-row"><span class="detail-label">Customer ID:</span><span class="detail-val">${esc(detail.external_customer_id)}</span></div>
        <div class="detail-row"><span class="detail-label">Recipient Phone:</span><span class="detail-val">${esc(detail.recipient_phone || '—')}</span></div>
        <div class="detail-row"><span class="detail-label">Provider:</span><span class="detail-val">${esc(detail.provider || '—')}</span></div>
        <div class="detail-row"><span class="detail-label">Provider Message ID:</span><span class="detail-val"><code>${esc(detail.provider_message_id || '—')}</code></span></div>
        <div class="detail-row"><span class="detail-label">Suppression Reason:</span><span class="detail-val">${esc(detail.suppression_reason || 'None')}</span></div>
      </div>

      <h5 style="font-size:12px;text-transform:uppercase;color:#0369a1;margin:16px 0 8px;">Delivered Order Line Items (Purchased Products &mdash; Source Triggers)</h5>
      <div style="margin-bottom:16px;">
        ${(detail.source_items || [])
          .map(
            (item) => `
          <div style="display:flex;justify-content:space-between;padding:6px 10px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:4px;margin-bottom:4px;font-size:12px;">
            <span><span class="tag-sku-purchased">${esc(item.sku_code)}</span> &mdash; ${esc(item.product_name)}</span>
            <span style="font-weight:600;color:#0284c7;">Qty: ${item.quantity}${item.unit_price ? ' &bull; ₹' + item.unit_price.toFixed(2) : ''}</span>
          </div>
        `
          )
          .join('') || '<p class="text-muted">No items recorded.</p>'}
      </div>

      <h5 style="font-size:12px;text-transform:uppercase;color:#15803d;margin:16px 0 8px;">Allocated Recommendation Cards (Algorithmic Cross-Sells &mdash; Purchased Excluded) (${(detail.recommendation_cards || []).length})</h5>
      <div style="margin-bottom:16px;">
        ${cardsHtml || '<p class="text-muted">No recommendations generated.</p>'}
      </div>

      <h5 style="font-size:12px;text-transform:uppercase;color:#64748b;margin:16px 0 8px;">Deterministic Decision Trace & Provenance</h5>
      <pre style="background:#0f172a;color:#f8fafc;padding:12px;border-radius:6px;font-size:11px;max-height:220px;overflow:auto;">${esc(traceJson)}</pre>
    `;
  } catch (err) {
    $('trace-modal-body').innerHTML = `<p class="text-danger">Failed to load campaign details: ${esc(err.message)}</p>`;
  }
}

window.openCampaignDrawer = openCampaignDrawer;
$('trace-modal-close-btn').addEventListener('click', () => $('trace-modal').classList.remove('active'));

// Dispatch Trigger with Confirmation Guard
function confirmDispatch(campaignId, isRetry = false) {
  openConfirmModal({
    title: isRetry ? 'Retry Campaign Dispatch' : 'Dispatch Upsell Campaign',
    message: `You are about to dispatch campaign #${campaignId} via the active messaging provider. Note: External HTTP calls are never executed under database row locks. Proceed?`,
    confirmText: isRetry ? 'Confirm Retry' : 'Confirm Dispatch',
    onConfirm: async () => {
      try {
        const res = await window.api.dispatchCampaign(campaignId, isRetry);
        showToast(res.detail || 'Dispatch executed successfully.', 'success');
        await loadCampaigns();
      } catch (err) {
        showToast(`Dispatch failed: ${err.message}`, 'error');
      }
    },
  });
}
window.confirmDispatch = confirmDispatch;

// Collapsible Customer Profile Panel Handler
function toggleProfilePanel(forceState = null) {
  if (forceState !== null) {
    state.isProfileOpen = Boolean(forceState);
  } else {
    state.isProfileOpen = !state.isProfileOpen;
  }

  const container = $('inbox-container');
  if (container) {
    container.classList.toggle('profile-closed', !state.isProfileOpen);
  }

  const toggleBtn = $('btn-toggle-profile');
  if (toggleBtn) {
    toggleBtn.classList.toggle('active', state.isProfileOpen);
    const titleText = state.isProfileOpen ? 'Hide customer profile' : 'Show customer profile';
    toggleBtn.title = titleText;
    toggleBtn.setAttribute('aria-label', titleText);
    toggleBtn.setAttribute('aria-expanded', String(state.isProfileOpen));
    const labelSpan = toggleBtn.querySelector('.btn-toggle-label');
    if (labelSpan) {
      labelSpan.textContent = state.isProfileOpen ? 'Hide Profile' : 'Profile';
    }
  }

  try {
    localStorage.setItem('inbox_profile_open', String(state.isProfileOpen));
  } catch (e) {}
}
window.toggleProfilePanel = toggleProfilePanel;

// ==========================================================================
// VIEW 3: WhatsApp Inbox (First-Class Feature)
// ==========================================================================
async function loadInbox() {
  const container = $('inbox-container');
  if (container) {
    container.classList.toggle('profile-closed', !state.isProfileOpen);
  }
  try {
    const campaigns = await window.api.getCampaigns();
    state.campaigns = campaigns;
    renderInboxThreads();

    // Select first campaign if none selected
    if (!state.selectedCampaignId && campaigns.length > 0) {
      selectInboxCampaign(campaigns[0].campaign_id);
    } else if (state.selectedCampaignId) {
      selectInboxCampaign(state.selectedCampaignId);
    } else {
      renderInboxEmptyChat();
    }
  } catch (err) {
    $('inbox-threads-list').innerHTML = `<p class="text-danger" style="padding:16px;">Failed to load threads: ${esc(err.message)}</p>`;
  }
}

function renderInboxThreads() {
  const container = $('inbox-threads-list');
  const items = state.campaigns;

  if (!items || items.length === 0) {
    container.innerHTML = `<p class="text-muted" style="padding:16px;">No conversations yet. Click "Load Demo Data" above.</p>`;
    return;
  }

  container.innerHTML = items
    .map(
      (c) => `
    <div class="thread-item ${state.selectedCampaignId === c.campaign_id ? 'active' : ''}" onclick="selectInboxCampaign(${c.campaign_id})">
      <div class="thread-header">
        <span class="thread-title">${esc(c.external_order_id)}</span>
        <span class="thread-time">${formatShortTime(c.created_at)}</span>
      </div>
      <div class="thread-sub">${esc(c.external_customer_id)} &bull; ${esc(c.recipient_phone || 'No Phone')}</div>
      <div style="margin-top:2px;">${getBadgeHtml(c.delivery_status)}</div>
    </div>
  `
    )
    .join('');
}

function renderInboxEmptyChat() {
  $('inbox-chat-header').innerHTML = `<h4>Select a Conversation</h4>`;
  $('inbox-chat-messages').innerHTML = `
    <div class="empty-state">
      <h4>No conversation selected</h4>
      <p>Select a customer order thread from the left column to view message bubbles and interact.</p>
    </div>
  `;
  $('inbox-chat-actions').innerHTML = '';
  $('inbox-right-drawer').innerHTML = `<p class="text-muted">Customer details appear here.</p>`;
}

async function selectInboxCampaign(campaignId) {
  state.selectedCampaignId = campaignId;
  renderInboxThreads();

  $('inbox-chat-header').innerHTML = `<h4>Loading Conversation #${campaignId}...</h4>`;
  $('inbox-chat-messages').innerHTML = `<div class="empty-state"><p>Retrieving WhatsApp conversation history...</p></div>`;
  $('inbox-chat-actions').innerHTML = '';
  $('inbox-right-drawer').innerHTML = `<div class="empty-state"><p>Loading order &amp; customer profile...</p></div>`;

  try {
    const detail = await window.api.getCampaignDetail(campaignId);
    state.selectedCampaignDetail = detail;

    // Header
    const isSimulated = detail.is_simulated || detail.delivery_status === 'SENT_SIMULATED';
    const custDisplay = detail.customer_name ? `${detail.customer_name} (${detail.external_customer_id})` : detail.external_customer_id;
    $('inbox-chat-header').innerHTML = `
      <div class="chat-header-info">
        <h4>${esc(custDisplay)} &mdash; Order ${esc(detail.external_order_id)}</h4>
        <p>${esc(detail.recipient_phone || 'No phone')} &bull; Provider: <strong>${esc(detail.provider || 'SIMULATOR')}</strong></p>
      </div>
      <div class="chat-header-actions">
        ${getBadgeHtml(detail.delivery_status)}
        <button id="btn-toggle-profile" class="btn-toggle-profile ${state.isProfileOpen ? 'active' : ''}" 
                onclick="toggleProfilePanel()" 
                title="${state.isProfileOpen ? 'Hide customer profile' : 'Show customer profile'}"
                aria-label="${state.isProfileOpen ? 'Hide customer profile' : 'Show customer profile'}"
                aria-expanded="${state.isProfileOpen}">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
            <circle cx="12" cy="7" r="4"></circle>
          </svg>
          <span class="btn-toggle-label">${state.isProfileOpen ? 'Hide Profile' : 'Profile'}</span>
        </button>
      </div>
    `;

    const inboxContainer = $('inbox-container');
    if (inboxContainer) {
      inboxContainer.classList.toggle('profile-closed', !state.isProfileOpen);
    }

    // Messages
    const msgs = detail.simulator_messages || [];
    const provenanceBanner = `
      <div class="chat-provenance-banner">
        <div style="display:flex;align-items:center;flex-wrap:wrap;gap:6px;">
          <strong>Delivered Order:</strong> <code>${esc(detail.external_order_id)}</code> &bull;
          <strong>Purchased Items:</strong> ${(detail.source_items || []).map((i) => `
            <span class="tag-sku-purchased" title="SKU: ${esc(i.sku_code)} &bull; Quantity: ${i.quantity || 1}">
              ${(i.product_name && i.product_name !== i.sku_code) ? `<span class="tag-product-name">${esc(i.product_name)}</span>` : ''}
              <span class="tag-sku-badge">${esc(i.sku_code)}${i.quantity > 1 ? ` &times;${i.quantity}` : ''}</span>
            </span>
          `).join('') || 'None'}
        </div>
        <div>
          <span class="badge badge-simulated">POST-DELIVERY UPSELL</span>
        </div>
      </div>
    `;

    if (!msgs || msgs.length === 0) {
      const canDispatch =
        detail.delivery_status === 'READY_FOR_DISPATCH' || detail.delivery_status === 'FAILED_RETRYABLE';
      $('inbox-chat-messages').innerHTML = `
        ${provenanceBanner}
        <div class="empty-state">
          <h4>No Messages Sent Yet</h4>
          <p>Campaign status is <strong>${detail.delivery_status}</strong>.</p>
          ${
            canDispatch
              ? `<div style="margin-top:14px;"><button class="btn btn-primary" onclick="confirmDispatch(${detail.campaign_id}, ${detail.delivery_status === 'FAILED_RETRYABLE'})">Dispatch WhatsApp Template Message</button></div>`
              : detail.delivery_status === 'SUPPRESSED'
              ? `<p class="text-muted" style="margin-top:8px;">Suppressed: ${esc(detail.suppression_reason)}</p>`
              : ''
          }
        </div>
      `;
    } else {
      $('inbox-chat-messages').innerHTML = provenanceBanner + msgs
        .map((m) => {
          const isOut = m.direction === 'OUTBOUND';
          const cards = m.recommendations_payload;
          const cardsHtml =
            isOut && cards && cards.length > 0
              ? `
            <div class="cards-deck">
              ${cards
                .map(
                  (card) => `
                <div class="rec-card-chat">
                  <div class="rec-card-title">${card.slot_number}. ${esc(card.product_name)}</div>
                  <div class="rec-card-sub">SKU: ${esc(card.product_code)}${card.unit_price ? ' &bull; ₹' + Number(card.unit_price).toFixed(2) : ''}</div>
                  ${card.source_product_code ? (() => {
                    const srcItem = (detail.source_items || []).find((s) => s.sku_code === card.source_product_code);
                    const srcName = srcItem && srcItem.product_name && srcItem.product_name !== card.source_product_code
                      ? `${esc(srcItem.product_name)} (${esc(card.source_product_code)})`
                      : esc(card.source_product_code);
                    return `<div class="badge-lineage">Cross-sell for purchased <strong>${srcName}</strong> (Rank #${card.source_rank || '1'})</div>`;
                  })() : '<div class="badge-lineage">Personalized cross-sell for order</div>'}
                  <div class="rec-card-actions">
                    <button class="btn-card-action" onclick="sendInteraction('${detail.campaign_id}', 'VIEW_RECOMMENDATION', '${esc(card.product_code)}')">View Details</button>
                    <button class="btn-card-action" style="background:#dcfce7;border-color:#86efac;color:#166534;" onclick="sendInteraction('${detail.campaign_id}', 'INTERESTED', '${esc(card.product_code)}')">Interested</button>
                    <button class="btn-card-action" onclick="sendInteraction('${detail.campaign_id}', 'NOT_INTERESTED', '${esc(card.product_code)}')">Pass</button>
                  </div>
                </div>
              `
                )
                .join('')}
            </div>
          `
              : '';

          return `
          <div class="message-bubble ${isOut ? 'outbound' : 'inbound'}">
            ${m.is_simulated ? '<span class="sim-tag">SIMULATED MESSAGE</span>' : ''}
            <div>${esc(m.body_text)}</div>
            ${cardsHtml}
            <div class="bubble-time">${formatShortTime(m.created_at)}</div>
          </div>
        `;
        })
        .join('');

      // Scroll to bottom
      const msgContainer = $('inbox-chat-messages');
      msgContainer.scrollTop = msgContainer.scrollHeight;
    }

    // Interactive Action Controls
    if (detail.delivery_status !== 'SUPPRESSED') {
      $('inbox-chat-actions').innerHTML = `
        <span style="font-size:12px;color:#64748b;font-weight:600;margin-right:4px;">Simulate Customer:</span>
        <button class="btn btn-secondary btn-sm" onclick="sendInteraction('${detail.campaign_id}', 'VIEW_ALL_RECOMMENDATIONS')">View All Recommendations</button>
        <button class="btn btn-secondary btn-sm" onclick="sendInteraction('${detail.campaign_id}', 'INTERESTED')">Interested in Order</button>
        <button class="btn btn-secondary btn-sm" onclick="sendInteraction('${detail.campaign_id}', 'NOT_INTERESTED')">Not Interested</button>
      `;
    } else {
      $('inbox-chat-actions').innerHTML = `<span style="font-size:12px;color:#94a3b8;">Campaign is SUPPRESSED. Customer interactions are not permitted.</span>`;
    }

    // Dynamic consent badge
    let consentBadge = '<span class="badge badge-delivered">ACTIVE OPT-IN</span>';
    if (detail.opted_out === true) {
      consentBadge = '<span class="badge badge-failed">OPTED_OUT</span>';
    } else if (detail.whatsapp_opt_in === false) {
      consentBadge = '<span class="badge badge-suppressed">NO MARKETING CONSENT</span>';
    }

    // Right Drawer Content: Distinct sections for Purchased Items vs Recommendations
    $('inbox-right-drawer').innerHTML = `
      <div class="drawer-header-row">
        <div class="drawer-title-box">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
            <circle cx="12" cy="7" r="4"></circle>
          </svg>
          <span>Customer &amp; Order Intelligence</span>
        </div>
        <button class="btn-close-drawer" onclick="toggleProfilePanel(false)" title="Hide customer profile" aria-label="Close profile panel">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="18" y1="6" x2="6" y2="18"></line>
            <line x1="6" y1="6" x2="18" y2="18"></line>
          </svg>
        </button>
      </div>

      <div class="drawer-section">
        <h5>Customer Profile</h5>
        <div class="detail-row"><span class="detail-label">Full Name</span><span class="detail-val">${esc(detail.customer_name || '—')}</span></div>
        <div class="detail-row"><span class="detail-label">Customer ID</span><span class="detail-val">${esc(detail.external_customer_id)}</span></div>
        <div class="detail-row"><span class="detail-label">Phone</span><span class="detail-val">${esc(detail.recipient_phone || '—')}</span></div>
        <div class="detail-row"><span class="detail-label">Consent State</span><span class="detail-val">${consentBadge}</span></div>
      </div>

      <div class="drawer-section">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;">
          <h5 style="margin:0;">Delivered Order Items</h5>
          <span class="badge badge-purchased">PURCHASED (${(detail.source_items || []).length})</span>
        </div>
        <div class="detail-row"><span class="detail-label">Order Ref</span><span class="detail-val"><code>${esc(detail.external_order_id)}</code></span></div>
        <p style="font-size:11px;color:#64748b;margin:6px 0;">Actual products bought in this order (source recommendation triggers):</p>
        <div style="margin-top:4px;">
          ${(detail.source_items || [])
            .map(
              (i) =>
                `<div style="font-size:11.5px;color:#334155;padding:3px 0;border-bottom:1px solid #f1f5f9;display:flex;justify-content:space-between;">
                  <span><strong>${esc(i.sku_code)}</strong> &mdash; ${esc(i.product_name)}</span>
                  <span style="font-weight:600;color:#0284c7;">x${i.quantity}</span>
                </div>`
            )
            .join('') || '<p class="text-muted">No line items.</p>'}
        </div>
      </div>

      <div class="drawer-section">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;">
          <h5 style="margin:0;">Cross-Sell Recommendations</h5>
          <span class="badge badge-rec">RECOMMENDED (${(detail.recommendation_cards || []).length})</span>
        </div>
        <p style="font-size:11px;color:#64748b;margin:4px 0 8px;">Algorithmic recommendations (already-purchased items strictly excluded):</p>
        <div style="margin-top:4px;">
          ${(detail.recommendation_cards || [])
            .map(
              (c) =>
                `<div style="font-size:11.5px;color:#334155;padding:4px 0;border-bottom:1px solid #f1f5f9;">
                  <div style="display:flex;justify-content:space-between;">
                    <span style="font-weight:600;">${c.slot_number}. ${esc(c.product_name)}</span>
                    <span style="color:#059669;font-weight:700;">${c.unit_price ? '₹' + c.unit_price.toFixed(2) : ''}</span>
                  </div>
                  <div style="font-size:10.5px;color:#64748b;">
                    SKU: <code>${esc(c.product_code)}</code> &bull; Source: <code>${esc(c.source_product_code || '—')}</code> (Rank #${c.source_rank || '—'})
                  </div>
                </div>`
            )
            .join('') || '<p class="text-muted">No recommendations generated.</p>'}
        </div>
      </div>

      <div class="drawer-section">
        <h5>Campaign &amp; Dispatch</h5>
        <div class="detail-row"><span class="detail-label">Status</span><span class="detail-val">${getBadgeHtml(detail.delivery_status)}</span></div>
        <div class="detail-row"><span class="detail-label">Provider</span><span class="detail-val">${esc(detail.provider || 'SIMULATOR')}</span></div>
        <div class="detail-row"><span class="detail-label">Accepted</span><span class="detail-val">${formatShortTime(detail.provider_accepted_at) || '—'}</span></div>
        <div class="detail-row"><span class="detail-label">Delivered</span><span class="detail-val">${formatShortTime(detail.delivered_at) || '—'}</span></div>
      </div>

      <div class="drawer-section">
        <h5>Decision Trace</h5>
        <button class="btn btn-secondary btn-sm" style="width:100%;margin-top:4px;" onclick="openCampaignDrawer(${detail.campaign_id})">Inspect Full Decision Trace</button>
      </div>
    `;
  } catch (err) {
    $('inbox-chat-header').innerHTML = `<h4>Error</h4>`;
    $('inbox-chat-messages').innerHTML = `<div class="empty-state"><p class="text-danger">Failed to load conversation: ${esc(err.message)}</p></div>`;
    $('inbox-right-drawer').innerHTML = `<p class="text-danger" style="padding:16px;">Failed to load details: ${esc(err.message)}</p>`;
  }
}
window.selectInboxCampaign = selectInboxCampaign;

async function sendInteraction(campaignId, actionType, productCode = null) {
  try {
    showToast(`Sending simulated customer action: ${actionType}...`, 'info');
    await window.api.sendSimulatorInteraction(campaignId, actionType, productCode);
    await selectInboxCampaign(campaignId);
    showToast('Customer interaction recorded and replied in simulator.', 'success');
  } catch (err) {
    showToast(`Action failed: ${err.message}`, 'error');
  }
}
window.sendInteraction = sendInteraction;

// ==========================================================================
// VIEW 4: Orders & Customers
// ==========================================================================
async function loadOrdersAndCustomers() {
  if (state.ordersCustomersTab === 'orders') {
    await loadOrdersTab();
  } else {
    await loadCustomersTab();
  }
}

$('tab-btn-orders').addEventListener('click', () => {
  state.ordersCustomersTab = 'orders';
  $('tab-btn-orders').classList.add('active');
  $('tab-btn-customers').classList.remove('active');
  loadOrdersTab();
});

$('tab-btn-customers').addEventListener('click', () => {
  state.ordersCustomersTab = 'customers';
  $('tab-btn-customers').classList.add('active');
  $('tab-btn-orders').classList.remove('active');
  loadCustomersTab();
});

async function loadOrdersTab() {
  const container = $('orders-customers-content');
  container.innerHTML = `<div class="empty-state"><p>Loading persisted orders...</p></div>`;
  try {
    const orders = await window.api.getOrders();
    state.orders = orders;

    if (!orders || orders.length === 0) {
      container.innerHTML = `<div class="empty-state"><h4>No Orders Found</h4><p>Click "Load Demo Data" above to generate demo orders.</p></div>`;
      return;
    }

    container.innerHTML = `
      <div class="table-container">
        <table class="data-table">
          <thead>
            <tr>
              <th>Order ID</th>
              <th>Customer</th>
              <th>Status</th>
              <th>Delivered At</th>
              <th>Purchased Items (Order Line Items)</th>
              <th>Generated Cross-Sell Recommendations</th>
              <th>Campaign Status</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            ${orders
              .map(
                (o) => `
              <tr>
                <td class="td-strong">${esc(o.external_order_id)}</td>
                <td>
                  <div>${esc(o.customer_name || o.customer_external_id)}</div>
                  <small style="color:#64748b;">${esc(o.customer_external_id)}</small>
                </td>
                <td><span class="badge ${o.order_status === 'DELIVERED' ? 'badge-delivered' : 'badge-suppressed'}">${esc(o.order_status)}</span></td>
                <td>${formatTime(o.delivered_at)}</td>
                <td>
                  ${(o.items && o.items.length > 0)
                    ? `<div class="product-item-stack">
                        ${o.items.map((i) => `
                          <div class="product-item-chip purchased" title="SKU: ${esc(i.product_code)} &bull; Quantity: ${i.quantity}">
                            <div class="product-name-row">
                              <span class="product-primary-name">${esc(i.product_name || i.product_code)}</span>
                              <span class="product-qty-tag">&times;${i.quantity}</span>
                            </div>
                            <span class="product-secondary-sku">${esc(i.product_code)}</span>
                          </div>
                        `).join('')}
                      </div>`
                    : '<span style="color:#94a3b8;font-size:11px;">No line items</span>'
                  }
                </td>
                <td>
                  ${(() => {
                    const recs = (o.recommended_products && o.recommended_products.length > 0)
                      ? o.recommended_products
                      : (o.recommended_skus || []).map((sku) => ({ product_code: sku, product_name: sku }));
                    if (!recs || recs.length === 0) {
                      return '<span style="color:#94a3b8;font-size:11px;">None</span>';
                    }
                    return `<div class="product-item-stack">
                      ${recs.map((r, idx) => `
                        <div class="product-item-chip recommendation" title="Rank #${idx + 1} &bull; SKU: ${esc(r.product_code)}">
                          <div class="product-name-row">
                            <span class="product-primary-name"><span class="product-rank-badge">#${idx + 1}</span>${esc(r.product_name || r.product_code)}</span>
                          </div>
                          <span class="product-secondary-sku">${esc(r.product_code)}</span>
                        </div>
                      `).join('')}
                    </div>`;
                  })()}
                </td>
                <td>${o.campaign_status ? getBadgeHtml(o.campaign_status) : '<span style="color:#94a3b8;">No campaign</span>'}</td>
                <td>${formatShortTime(o.created_at)}</td>
              </tr>
            `
              )
              .join('')}
          </tbody>
        </table>
      </div>
    `;
  } catch (err) {
    container.innerHTML = `<div class="empty-state"><p class="text-danger">Failed to load orders: ${esc(err.message)}</p></div>`;
  }
}

async function loadCustomersTab() {
  const container = $('orders-customers-content');
  container.innerHTML = `<div class="empty-state"><p>Loading customer profiles and consent provenance...</p></div>`;
  try {
    const customers = await window.api.getCustomers();
    state.customers = customers;

    if (!customers || customers.length === 0) {
      container.innerHTML = `<div class="empty-state"><h4>No Customers Found</h4></div>`;
      return;
    }

    container.innerHTML = `
      <div class="table-container">
        <table class="data-table">
          <thead>
            <tr>
              <th>Customer ID</th>
              <th>Full Name</th>
              <th>Masked Phone</th>
              <th>WhatsApp Opt-In</th>
              <th>Opted Out</th>
              <th>Consent Provenance</th>
              <th>Orders</th>
              <th>Campaigns</th>
            </tr>
          </thead>
          <tbody>
            ${customers
              .map((c) => {
                const optInBadge =
                  c.whatsapp_opt_in === true
                    ? `<span class="badge badge-delivered">OPTED_IN</span>`
                    : c.whatsapp_opt_in === false
                    ? `<span class="badge badge-failed">NOT_OPTED_IN</span>`
                    : `<span class="badge badge-suppressed">UNKNOWN</span>`;

                const optOutBadge =
                  c.opted_out === true
                    ? `<span class="badge badge-failed">OPTED_OUT</span>`
                    : `<span class="badge badge-delivered">ACTIVE</span>`;

                return `
                <tr>
                  <td class="td-strong">${esc(c.external_customer_id)}</td>
                  <td>${esc(c.full_name || '—')}</td>
                  <td><code>${esc(c.masked_phone)}</code></td>
                  <td>${optInBadge}</td>
                  <td>${optOutBadge}</td>
                  <td>
                    <div style="font-size:12px;color:#334155;">${esc(c.consent_source || 'Not recorded')}</div>
                    <small style="color:#64748b;">${formatTime(c.consent_recorded_at)}</small>
                  </td>
                  <td><strong>${c.order_count}</strong></td>
                  <td><strong>${c.campaign_count}</strong></td>
                </tr>
              `;
              })
              .join('')}
          </tbody>
        </table>
      </div>
    `;
  } catch (err) {
    container.innerHTML = `<div class="empty-state"><p class="text-danger">Failed to load customers: ${esc(err.message)}</p></div>`;
  }
}

// ==========================================================================
// VIEW 5: Product Catalogue & Mappings
// ==========================================================================
async function loadCatalogue() {
  const container = $('catalogue-table-container');
  container.innerHTML = `<div class="empty-state"><p>Loading 70-SKU product catalogue and mappings...</p></div>`;
  try {
    const products = await window.api.getProducts(state.productSearchQuery);
    state.products = products;
    renderCatalogueTable();
  } catch (err) {
    container.innerHTML = `<div class="empty-state"><p class="text-danger">Failed to load catalogue: ${esc(err.message)}</p></div>`;
  }
}

function renderCatalogueTable() {
  const container = $('catalogue-table-container');
  const items = state.products;

  if (!items || items.length === 0) {
    container.innerHTML = `<div class="empty-state"><h4>No Products Found</h4><p>Try searching for a different product name or SKU code.</p></div>`;
    return;
  }

  container.innerHTML = `
    <table class="data-table">
      <thead>
        <tr>
          <th>SKU Code</th>
          <th>Product Name</th>
          <th>Unit Price</th>
          <th>Rx Status</th>
          <th>Active</th>
          <th>Stock Available</th>
          <th>Metadata Provenance</th>
          <th>Ranked Targets</th>
          <th>Action</th>
        </tr>
      </thead>
      <tbody>
        ${items
          .map((p) => {
            const rxBadge =
              p.is_rx === true
                ? `<span class="badge badge-failed">Rx PRESCRIPTION</span>`
                : p.is_rx === false
                ? `<span class="badge badge-delivered">OTC ELIGIBLE</span>`
                : `<span class="badge badge-suppressed">UNKNOWN (NULL)</span>`;

            return `
            <tr>
              <td class="td-strong"><code>${esc(p.product_code)}</code></td>
              <td>${esc(p.product_name)}</td>
              <td>${p.unit_price ? '₹' + p.unit_price.toFixed(2) : '<span style="color:#94a3b8;">—</span>'}</td>
              <td>${rxBadge}</td>
              <td>${p.is_active === true ? '<span style="color:#16a34a;font-weight:600;">Active</span>' : p.is_active === false ? '<span style="color:#dc2626;">Inactive</span>' : '<span style="color:#94a3b8;">NULL</span>'}</td>
              <td>${p.available_quantity != null ? p.available_quantity : '<span style="color:#94a3b8;">NULL</span>'}</td>
              <td>${getProvenanceBadgeHtml(p.metadata_source)}</td>
              <td><span style="font-weight:600;color:#0284c7;">${p.mapped_recommendations_count} targets</span></td>
              <td>
                <button class="btn btn-secondary btn-sm" onclick="openProductDetail('${esc(p.product_code)}')">View Mappings</button>
              </td>
            </tr>
          `;
          })
          .join('')}
      </tbody>
    </table>
  `;
}

$('catalogue-search').addEventListener('input', (e) => {
  state.productSearchQuery = e.target.value;
  loadCatalogue();
});

async function openProductDetail(productCode) {
  $('product-modal').classList.add('active');
  $('product-modal-body').innerHTML = `<p>Loading product mappings for ${esc(productCode)}...</p>`;
  try {
    const detail = await window.api.getProductDetail(productCode);
    state.selectedProductDetail = detail;

    $('product-modal-title').textContent = `${detail.product_code} — ${detail.product_name}`;
    $('product-modal-body').innerHTML = `
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:16px;">
        <div class="detail-row"><span class="detail-label">Unit Price:</span><span class="detail-val">${detail.unit_price ? '₹' + detail.unit_price.toFixed(2) : '—'}</span></div>
        <div class="detail-row"><span class="detail-label">Rx Prescription:</span><span class="detail-val">${detail.is_rx === true ? 'Yes (Rx)' : detail.is_rx === false ? 'No (OTC)' : 'Unknown'}</span></div>
        <div class="detail-row"><span class="detail-label">Catalogue Provenance:</span><span class="detail-val">${getProvenanceBadgeHtml(detail.metadata_source)}</span></div>
        <div class="detail-row"><span class="detail-label">Available Stock:</span><span class="detail-val">${detail.available_quantity ?? 'Unknown'}</span></div>
      </div>

      <h5 style="font-size:12px;text-transform:uppercase;color:#64748b;margin:16px 0 8px;">Ranked Product Recommendations (1–6)</h5>
      <div class="table-container">
        <table class="data-table">
          <thead>
            <tr>
              <th>Rank</th>
              <th>Target SKU</th>
              <th>Product Name</th>
              <th>Price</th>
              <th>Rx Status</th>
              <th>Provenance</th>
            </tr>
          </thead>
          <tbody>
            ${(detail.mappings || [])
              .map(
                (m) => `
              <tr>
                <td class="td-strong">#${m.rank}</td>
                <td><code>${esc(m.recommended_product_code)}</code></td>
                <td>${esc(m.recommended_product_name)}</td>
                <td>${m.unit_price ? '₹' + m.unit_price.toFixed(2) : '—'}</td>
                <td>${m.is_rx === true ? '<span class="badge badge-failed">Rx</span>' : m.is_rx === false ? '<span class="badge badge-delivered">OTC</span>' : '<span class="badge badge-suppressed">NULL</span>'}</td>
                <td>${getProvenanceBadgeHtml(m.metadata_source)}</td>
              </tr>
            `
              )
              .join('')}
          </tbody>
        </table>
      </div>
    `;
  } catch (err) {
    $('product-modal-body').innerHTML = `<p class="text-danger">Failed to load product detail: ${esc(err.message)}</p>`;
  }
}
window.openProductDetail = openProductDetail;
$('product-modal-close-btn').addEventListener('click', () => $('product-modal').classList.remove('active'));

// ==========================================================================
// VIEW 6: Settings & Integrations
// ==========================================================================
async function loadSettings() {
  const container = $('settings-content');
  container.innerHTML = `<div class="empty-state"><p>Checking backend health and provider configuration...</p></div>`;
  try {
    const [live, ready, prov] = await Promise.all([
      window.api.getHealthLive().catch((e) => ({ status: 'DOWN', error: e.message })),
      window.api.getHealthReady().catch((e) => ({ status: 'NOT READY', error: e.message })),
      window.api.getProviderStatus().catch((e) => ({ configured_provider: 'ERROR', error: e.message })),
    ]);

    container.innerHTML = `
      <div style="max-width:800px;display:flex;flex-direction:column;gap:20px;">
        <!-- Messaging Provider Status Card -->
        <div class="card">
          <div class="card-header">
            <div>
              <h3 class="card-title">WhatsApp Messaging Integration</h3>
              <p class="card-subtitle">Active gateway adapter: <strong>${esc(prov.active_provider || '—')}</strong></p>
            </div>
            <div>${getBadgeHtml(prov.is_configured ? 'READY_FOR_DISPATCH' : 'FAILED_PERMANENT')}</div>
          </div>
          <div class="card-body">
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
              <div class="detail-row"><span class="detail-label">Configured Mode:</span><span class="detail-val"><code>${esc(prov.configured_provider || '—')}</code></span></div>
              <div class="detail-row"><span class="detail-label">API Version:</span><span class="detail-val">${esc(prov.api_version || 'v21.0')}</span></div>
              <div class="detail-row"><span class="detail-label">Template Name:</span><span class="detail-val"><code>${esc(prov.template_name || '—')}</code></span></div>
              <div class="detail-row"><span class="detail-label">Template Language:</span><span class="detail-val">${esc(prov.template_language || 'en_US')}</span></div>
              <div class="detail-row"><span class="detail-label">HTTP Timeout:</span><span class="detail-val">${prov.http_timeout_seconds || 10} seconds</span></div>
              <div class="detail-row"><span class="detail-label">Webhook Verify Token:</span><span class="detail-val">${prov.webhook_verify_token_configured ? '<span style="color:#16a34a;font-weight:600;">Configured</span>' : '<span style="color:#d97706;">Not set</span>'}</span></div>
              <div class="detail-row"><span class="detail-label">Webhook App Secret:</span><span class="detail-val">${prov.webhook_app_secret_configured ? '<span style="color:#16a34a;font-weight:600;">Configured</span>' : '<span style="color:#d97706;">Not set</span>'}</span></div>
            </div>
            <div style="margin-top:16px;padding:12px;background:#f8fafc;border-radius:6px;font-size:12.5px;color:#475569;">
              <strong>System Notice:</strong> ${esc(prov.notes || '—')}
            </div>
          </div>
        </div>

        <!-- System & Database Health -->
        <div class="card">
          <div class="card-header">
            <div>
              <h3 class="card-title">Backend & Database Health Probes</h3>
              <p class="card-subtitle">FastAPI application status and PostgreSQL database connection</p>
            </div>
          </div>
          <div class="card-body">
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
              <div class="detail-row">
                <span class="detail-label">Liveness Probe (GET /health/live):</span>
                <span class="detail-val" style="color:${live.status === 'ok' ? '#16a34a' : '#dc2626'};font-weight:700;">${esc(live.status || 'DOWN')}</span>
              </div>
              <div class="detail-row">
                <span class="detail-label">Readiness Probe (GET /health/ready):</span>
                <span class="detail-val" style="color:${ready.status === 'ready' ? '#16a34a' : '#dc2626'};font-weight:700;">${esc(ready.status || 'NOT READY')}</span>
              </div>
              <div class="detail-row">
                <span class="detail-label">Database Connected:</span>
                <span class="detail-val">${ready.database === 'connected' ? '<span style="color:#16a34a;font-weight:600;">Connected (PostgreSQL)</span>' : '<span style="color:#dc2626;">Disconnected</span>'}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    `;
  } catch (err) {
    container.innerHTML = `<div class="empty-state"><p class="text-danger">Failed to load settings: ${esc(err.message)}</p></div>`;
  }
}

// Initial Bootstrap on Page Load
document.addEventListener('DOMContentLoaded', async () => {
  await loadProviderStatus();
  const initialView = getViewFromUrl();
  updateUrlForView(initialView, true);
  switchView(initialView, false);
});
