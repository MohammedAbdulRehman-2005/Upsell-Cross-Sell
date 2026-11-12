/**
 * Plural Commerce Automation - Centralized REST API Client
 * Clean, robust wrapper for all backend endpoints with timeout, error handling, and typing helpers.
 */

const API_BASE_URL = '/api/v1';

class ApiClient {
  constructor(baseUrl = API_BASE_URL, defaultTimeoutMs = 15000) {
    this.baseUrl = baseUrl.replace(/\/+$/, '');
    this.defaultTimeoutMs = defaultTimeoutMs;
  }

  /**
   * Universal fetch helper with timeout and structured error extraction.
   * @param {string} endpoint - Path relative to base URL (or absolute if starts with /health)
   * @param {RequestInit} [options]
   */
  async request(endpoint, options = {}) {
    const url = endpoint.startsWith('/health') ? endpoint : `${this.baseUrl}${endpoint}`;
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), this.defaultTimeoutMs);

    const headers = {
      Accept: 'application/json',
      ...options.headers,
    };
    if (options.body && typeof options.body === 'string' && !headers['Content-Type']) {
      headers['Content-Type'] = 'application/json';
    }

    try {
      const response = await fetch(url, {
        ...options,
        headers,
        signal: controller.signal,
      });

      const text = await response.text();
      let data;
      try {
        data = text ? JSON.parse(text) : null;
      } catch {
        data = text;
      }

      if (!response.ok) {
        let errorMsg = `HTTP ${response.status}`;
        if (data && typeof data === 'object') {
          errorMsg = data.detail || data.error || data.message || JSON.stringify(data);
        } else if (typeof data === 'string' && data) {
          errorMsg = data;
        }
        const error = new Error(errorMsg);
        error.status = response.status;
        error.data = data;
        throw error;
      }

      return data;
    } catch (err) {
      if (err.name === 'AbortError') {
        const timeoutErr = new Error(`Request to ${url} timed out after ${this.defaultTimeoutMs}ms.`);
        timeoutErr.status = 408;
        throw timeoutErr;
      }
      throw err;
    } finally {
      clearTimeout(timeoutId);
    }
  }

  // --- Health Endpoints ---
  async getHealthLive() {
    return this.request('/health/live');
  }

  async getHealthReady() {
    return this.request('/health/ready');
  }

  // --- Overview & Dashboard Metrics ---
  async getOverviewMetrics() {
    return this.request('/analytics/overview');
  }

  async getProviderStatus() {
    return this.request('/campaigns/provider-status');
  }

  // --- Campaigns ---
  async getCampaigns() {
    return this.request('/campaigns');
  }

  async getCampaignDetail(campaignId) {
    return this.request(`/campaigns/${campaignId}`);
  }

  async dispatchCampaign(campaignId, forceRetry = false) {
    return this.request(`/campaigns/${campaignId}/dispatch`, {
      method: 'POST',
      body: JSON.stringify({ force_retry: Boolean(forceRetry) }),
    });
  }

  // --- WhatsApp Simulator & Interactions ---
  async getSimulatorMessages(campaignId) {
    return this.request(`/simulator/campaigns/${campaignId}/messages`);
  }

  async sendSimulatorInteraction(campaignId, actionType, productCode = null, customerNote = null) {
    return this.request(`/simulator/campaigns/${campaignId}/interact`, {
      method: 'POST',
      body: JSON.stringify({
        action_type: actionType,
        product_code: productCode,
        customer_note: customerNote,
      }),
    });
  }

  async seedDemoCampaigns() {
    return this.request('/simulator/demo-seed', {
      method: 'POST',
    });
  }

  // --- Orders & Customers ---
  async getOrders(limit = 100, offset = 0) {
    return this.request(`/orders?limit=${limit}&offset=${offset}`);
  }

  async getCustomers(limit = 100, offset = 0) {
    return this.request(`/customers?limit=${limit}&offset=${offset}`);
  }

  // --- Product Catalogue & Mappings ---
  async getProducts(searchTerm = null, limit = 150, offset = 0) {
    const q = searchTerm && searchTerm.trim() ? `&q=${encodeURIComponent(searchTerm.trim())}` : '';
    return this.request(`/products?limit=${limit}&offset=${offset}${q}`);
  }

  async getProductDetail(productCode) {
    return this.request(`/products/${encodeURIComponent(productCode)}`);
  }

  // --- Stage 8 Storefront & Demo Delivery ---
  async createStorefrontOrder(checkoutPayload) {
    return this.request('/store/orders', {
      method: 'POST',
      body: JSON.stringify(checkoutPayload),
    });
  }

  async getStorefrontOrderTracking(orderId) {
    return this.request(`/store/orders/${encodeURIComponent(orderId)}/tracking`);
  }
}

// Export singleton instance for app scripts
window.api = new ApiClient();
