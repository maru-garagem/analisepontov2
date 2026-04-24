// Histórico de processamentos. Implementado em Fase 10.

(async function () {
  if (!(await App.ensureAuthed())) return;
  document.getElementById('logoutBtn').addEventListener('click', App.logout);

  const list = document.getElementById('historyList');
  try {
    const data = await App.apiJson('/api/history?limit=50');
    renderHistorico(data);
  } catch (err) {
    if (err.status === 404) {
      list.textContent = 'Endpoint ainda não implementado.';
    } else {
      list.textContent = 'Erro: ' + err.message;
    }
  }

  function renderHistorico(data) {
    const itens = data?.itens || [];
    if (!itens.length) {
      list.innerHTML = '<p class="text-slate-500">Nenhum processamento registrado.</p>';
      return;
    }
    const t = document.createElement('table');
    t.className = 'min-w-full text-sm';
    t.innerHTML = `
      <thead class="bg-slate-100 text-left">
        <tr>
          <th class="px-3 py-2 font-medium">Data</th>
          <th class="px-3 py-2 font-medium">Arquivo</th>
          <th class="px-3 py-2 font-medium">Empresa</th>
          <th class="px-3 py-2 font-medium">Método</th>
          <th class="px-3 py-2 font-medium">Status</th>
          <th class="px-3 py-2 font-medium">Score</th>
        </tr>
      </thead>
      <tbody></tbody>`;
    const tbody = t.querySelector('tbody');
    for (const i of itens) {
      const tr = document.createElement('tr');
      tr.className = 'border-t border-slate-200';
      tr.innerHTML = `
        <td class="px-3 py-2 text-xs">${new Date(i.criado_em).toLocaleString('pt-BR')}</td>
        <td class="px-3 py-2 truncate max-w-[18rem]">${escapeHtml(i.nome_arquivo_original)}</td>
        <td class="px-3 py-2">${escapeHtml(i.empresa_nome || '—')}</td>
        <td class="px-3 py-2 text-xs">${escapeHtml(i.metodo_usado || '—')}</td>
        <td class="px-3 py-2 text-xs">${escapeHtml(i.status)}</td>
        <td class="px-3 py-2">${typeof i.score_conformidade === 'number' ? Math.round(i.score_conformidade * 100) + '%' : '—'}</td>
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
