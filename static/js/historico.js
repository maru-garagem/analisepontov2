// Histórico: lista processamentos com ações contextuais (Retomar, Detalhes, Apagar).

(async function () {
  if (!(await App.ensureAuthed())) return;
  document.getElementById('logoutBtn').addEventListener('click', App.logout);

  const list = document.getElementById('historyList');
  await carregar();

  async function carregar() {
    try {
      const data = await App.apiJson('/api/history?limit=100');
      renderHistorico(data);
    } catch (err) {
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
          <th class="px-3 py-2 font-medium text-right">Ações</th>
        </tr>
      </thead>
      <tbody></tbody>`;
    const tbody = t.querySelector('tbody');

    for (const i of itens) {
      const tr = document.createElement('tr');
      tr.className = 'border-t border-slate-200';
      const badgeClass = badgeFor(i.status);
      tr.innerHTML = `
        <td class="px-3 py-2 text-xs whitespace-nowrap">${new Date(i.criado_em).toLocaleString('pt-BR')}</td>
        <td class="px-3 py-2 truncate max-w-[14rem]" title="${escapeAttr(i.nome_arquivo_original)}">${escapeHtml(i.nome_arquivo_original)}</td>
        <td class="px-3 py-2">${escapeHtml(i.empresa_nome || '—')}</td>
        <td class="px-3 py-2 text-xs">${escapeHtml(i.metodo_usado || '—')}</td>
        <td class="px-3 py-2 text-xs"><span class="inline-block px-2 py-0.5 rounded-full ${badgeClass}">${escapeHtml(i.status)}</span></td>
        <td class="px-3 py-2">${typeof i.score_conformidade === 'number' ? Math.round(i.score_conformidade * 100) + '%' : '—'}</td>
        <td class="px-3 py-2 text-right whitespace-nowrap"></td>
      `;
      const acoes = tr.lastElementChild;

      if (i.pode_retomar) {
        const a = document.createElement('a');
        a.href = `/cadastro-assistido.html?id=${i.id}`;
        a.className = 'inline-block px-2 py-1 text-xs rounded bg-blue-600 text-white hover:bg-blue-700 mr-1';
        a.textContent = 'Retomar';
        acoes.appendChild(a);
      } else if (i.status === 'aguardando_cadastro') {
        const s = document.createElement('span');
        s.className = 'inline-block px-2 py-1 text-xs text-slate-500 mr-1';
        s.textContent = '(expirado)';
        s.title = 'PDF não está mais no servidor. Reenvie.';
        acoes.appendChild(s);
      }

      const btnDet = document.createElement('button');
      btnDet.className = 'inline-block px-2 py-1 text-xs rounded border border-slate-300 hover:bg-slate-50 mr-1';
      btnDet.textContent = 'Detalhes';
      btnDet.onclick = () => mostrarDetalhes(i.id);
      acoes.appendChild(btnDet);

      const btnDel = document.createElement('button');
      btnDel.className = 'inline-block px-2 py-1 text-xs rounded border border-red-300 text-red-700 hover:bg-red-50';
      btnDel.textContent = 'Apagar';
      btnDel.onclick = () => apagar(i.id);
      acoes.appendChild(btnDel);

      tbody.appendChild(tr);
    }

    list.innerHTML = '';
    list.appendChild(t);
  }

  function badgeFor(status) {
    const map = {
      sucesso: 'bg-green-100 text-green-800',
      sucesso_com_aviso: 'bg-amber-100 text-amber-800',
      aguardando_cadastro: 'bg-blue-100 text-blue-800',
      em_processamento: 'bg-slate-200 text-slate-700',
      nao_cartao_ponto: 'bg-purple-100 text-purple-800',
      falhou: 'bg-red-100 text-red-800',
    };
    return map[status] || 'bg-slate-200 text-slate-700';
  }

  async function mostrarDetalhes(id) {
    let data;
    try {
      data = await App.apiJson('/api/history/' + id);
    } catch (err) {
      App.toast('Erro ao buscar detalhes: ' + err.message, 'error');
      return;
    }
    abrirModal(data);
  }

  async function apagar(id) {
    if (!confirm('Apagar este processamento? Essa ação é permanente.')) return;
    try {
      await App.apiJson('/api/history/' + id, { method: 'DELETE' });
      App.toast('Removido.', 'success');
      carregar();
    } catch (err) {
      App.toast('Erro ao apagar: ' + err.message, 'error');
    }
  }

  function abrirModal(data) {
    const overlay = document.createElement('div');
    overlay.className =
      'fixed inset-0 bg-black/50 flex items-start justify-center p-4 z-50 overflow-y-auto';
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) overlay.remove();
    });

    const card = document.createElement('div');
    card.className = 'bg-white rounded-lg shadow-xl max-w-4xl w-full my-10 p-6';

    const linhas = data.resultado_json?.linhas || [];
    const cols = linhas[0] ? Object.keys(linhas[0]) : [];

    card.innerHTML = `
      <div class="flex items-center justify-between mb-4">
        <h3 class="text-lg font-medium">Detalhes do processamento</h3>
        <button class="text-slate-500 hover:text-slate-900 text-2xl leading-none" data-close>&times;</button>
      </div>
      <dl class="grid grid-cols-2 gap-x-6 gap-y-1 text-sm mb-4">
        <dt class="text-slate-500">Arquivo</dt><dd class="font-medium">${escapeHtml(data.nome_arquivo_original)}</dd>
        <dt class="text-slate-500">Empresa</dt><dd>${escapeHtml(data.empresa_nome || '—')}</dd>
        <dt class="text-slate-500">Método</dt><dd>${escapeHtml(data.metodo_usado || '—')}</dd>
        <dt class="text-slate-500">Status</dt><dd>${escapeHtml(data.status)}</dd>
        <dt class="text-slate-500">Score</dt><dd>${typeof data.score_conformidade === 'number' ? Math.round(data.score_conformidade * 100) + '%' : '—'}</dd>
        <dt class="text-slate-500">Tempo</dt><dd>${data.tempo_processamento_ms ?? '—'} ms</dd>
      </dl>
      <h4 class="font-medium text-sm mb-1">Cabeçalho</h4>
      <pre class="bg-slate-50 rounded p-2 text-xs mb-4 overflow-x-auto">${escapeHtml(JSON.stringify(data.resultado_json?.cabecalho ?? {}, null, 2))}</pre>
      <h4 class="font-medium text-sm mb-1">Linhas (${linhas.length})</h4>
      <div class="overflow-x-auto"></div>
    `;

    const linhasContainer = card.querySelector('div.overflow-x-auto');
    if (linhas.length === 0) {
      linhasContainer.innerHTML = '<p class="text-slate-500 text-sm">Nenhuma linha.</p>';
    } else {
      const table = document.createElement('table');
      table.className = 'min-w-full text-xs';
      let thead = '<thead class="bg-slate-100"><tr>';
      for (const c of cols) thead += `<th class="px-2 py-1 text-left font-medium">${escapeHtml(c)}</th>`;
      thead += '</tr></thead><tbody>';
      let tbody = '';
      for (const linha of linhas) {
        tbody += '<tr class="border-t border-slate-200">';
        for (const c of cols) {
          tbody += `<td class="px-2 py-1">${escapeHtml(linha[c] ?? '')}</td>`;
        }
        tbody += '</tr>';
      }
      table.innerHTML = thead + tbody + '</tbody>';
      linhasContainer.appendChild(table);
    }

    card.querySelector('[data-close]').onclick = () => overlay.remove();
    overlay.appendChild(card);
    document.body.appendChild(overlay);
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[c]);
  }
  function escapeAttr(s) {
    return escapeHtml(s);
  }
})();
