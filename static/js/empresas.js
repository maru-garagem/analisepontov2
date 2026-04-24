// Listagem de empresas cadastradas. Implementado em Fase 10.

(async function () {
  if (!(await App.ensureAuthed())) return;
  document.getElementById('logoutBtn').addEventListener('click', App.logout);

  const list = document.getElementById('empresasList');
  try {
    const data = await App.apiJson('/api/empresas');
    renderEmpresas(data.itens || []);
  } catch (err) {
    list.textContent = 'Erro: ' + err.message;
  }

  function renderEmpresas(data) {
    if (!Array.isArray(data) || data.length === 0) {
      list.innerHTML = '<p class="text-slate-500">Nenhuma empresa cadastrada ainda.</p>';
      return;
    }
    const t = document.createElement('table');
    t.className = 'min-w-full text-sm';
    t.innerHTML = `
      <thead class="bg-slate-100 text-left">
        <tr>
          <th class="px-3 py-2 font-medium">Empresa</th>
          <th class="px-3 py-2 font-medium">CNPJs</th>
          <th class="px-3 py-2 font-medium">Esqueletos</th>
          <th class="px-3 py-2 font-medium">Taxa de sucesso</th>
        </tr>
      </thead>
      <tbody></tbody>`;
    const tbody = t.querySelector('tbody');
    for (const e of data) {
      const tr = document.createElement('tr');
      tr.className = 'border-t border-slate-200 hover:bg-slate-50 cursor-pointer';
      tr.addEventListener('click', () => {
        location.href = `/empresa-detalhe.html?id=${e.id}`;
      });
      tr.innerHTML = `
        <td class="px-3 py-2">
          <a href="/empresa-detalhe.html?id=${e.id}" class="text-blue-700 hover:underline">${escapeHtml(e.nome || '—')}</a>
        </td>
        <td class="px-3 py-2 font-mono text-xs">${(e.cnpjs || []).map(escapeHtml).join('<br>')}</td>
        <td class="px-3 py-2">${e.total_esqueletos ?? 0}</td>
        <td class="px-3 py-2">${typeof e.taxa_sucesso_media === 'number' ? Math.round(e.taxa_sucesso_media * 100) + '%' : '—'}</td>
      `;
      tbody.appendChild(tr);
    }
    list.innerHTML = '';
    list.appendChild(t);
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[c]);
  }
})();
