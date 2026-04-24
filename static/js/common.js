// Helpers compartilhados: wrappers fetch, toasts, auth redirect.
// Exposto como window.App para não precisar de módulos ES.

(function () {
  const App = {};

  async function api(path, opts = {}) {
    const resp = await fetch(path, {
      credentials: 'same-origin',
      ...opts,
    });
    if (resp.status === 401) {
      if (!location.pathname.endsWith('/login.html')) {
        location.href = '/login.html';
      }
      throw new Error('unauthenticated');
    }
    return resp;
  }

  async function apiJson(path, opts = {}) {
    const resp = await api(path, opts);
    let body = null;
    try {
      body = await resp.json();
    } catch {
      // vazio
    }
    if (!resp.ok) {
      const msg = (body && (body.detail || body.message)) || resp.statusText;
      const err = new Error(msg);
      err.status = resp.status;
      err.body = body;
      throw err;
    }
    return body;
  }

  async function apiPostJson(path, payload) {
    return apiJson(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  }

  function toast(msg, tipo = 'info', ms = 4000) {
    const el = document.createElement('div');
    const cores = {
      info: 'bg-blue-600',
      success: 'bg-green-600',
      warn: 'bg-amber-500',
      error: 'bg-red-600',
    };
    el.className =
      'fixed top-4 right-4 px-4 py-2 rounded shadow-lg z-50 text-white text-sm transition-opacity ' +
      (cores[tipo] || cores.info);
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => {
      el.style.opacity = '0';
      setTimeout(() => el.remove(), 300);
    }, ms);
  }

  async function logout() {
    try {
      await api('/api/auth/logout', { method: 'POST' });
    } finally {
      location.href = '/login.html';
    }
  }

  async function ensureAuthed() {
    try {
      const data = await apiJson('/api/auth/me');
      if (!data.authenticated) {
        location.href = '/login.html';
        return false;
      }
      return true;
    } catch {
      return false;
    }
  }

  // Polling simples com backoff linear capado.
  async function poll(pathFn, { intervalMs = 1500, maxMs = 600000, shouldStop }) {
    const inicio = Date.now();
    while (Date.now() - inicio < maxMs) {
      const data = await apiJson(pathFn());
      if (shouldStop(data)) return data;
      await new Promise((r) => setTimeout(r, intervalMs));
    }
    throw new Error('Tempo esgotado aguardando processamento.');
  }

  App.api = api;
  App.apiJson = apiJson;
  App.apiPostJson = apiPostJson;
  App.toast = toast;
  App.logout = logout;
  App.ensureAuthed = ensureAuthed;
  App.poll = poll;
  window.App = App;
})();
