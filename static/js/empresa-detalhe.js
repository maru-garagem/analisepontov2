// Detalhe de empresa: edita nome, adiciona/remove CNPJs, lista esqueletos com ações.

(async function () {
  if (!(await App.ensureAuthed())) return;
  document.getElementById('logoutBtn').addEventListener('click', App.logout);

  const root = document.getElementById('detalhe');
  const params = new URLSearchParams(location.search);
  const empresaId = params.get('id');
  if (!empresaId) {
    root.textContent = 'ID da empresa ausente.';
    return;
  }

  async function carregar() {
    root.innerHTML = '<p class="text-slate-500">Carregando...</p>';
    let data;
    try {
      data = await App.apiJson('/api/empresas/' + empresaId);
    } catch (err) {
      root.textContent = 'Erro: ' + err.message;
      return;
    }
    render(data);
  }

  function render(data) {
    root.innerHTML = `
      <section class="bg-white rounded-lg shadow-sm border border-slate-200 p-6 mb-6">
        <div class="flex items-center justify-between mb-4">
          <div>
            <span class="text-sm text-slate-500">Empresa</span>
            <h2 class="text-xl font-medium" id="nomeView">${escapeHtml(data.nome)}</h2>
          </div>
          <button id="editarNome" class="text-sm text-blue-700 hover:underline">Renomear</button>
        </div>

        <div>
          <div class="flex items-center justify-between mb-2">
            <span class="text-sm font-medium">CNPJs vinculados</span>
            <button id="addCnpj" class="text-sm text-blue-700 hover:underline">+ Adicionar</button>
          </div>
          <ul id="cnpjList" class="space-y-1"></ul>
        </div>
      </section>

      <section class="bg-white rounded-lg shadow-sm border border-slate-200 p-6">
        <h3 class="font-medium mb-3">Esqueletos (${data.esqueletos.length})</h3>
        <div id="esqueletosList"></div>
      </section>
    `;

    renderCnpjs(data.cnpjs);
    renderEsqueletos(data.esqueletos);

    document.getElementById('editarNome').onclick = () => renomear(data.nome);
    document.getElementById('addCnpj').onclick = () => adicionarCnpj();
  }

  function renderCnpjs(cnpjs) {
    const ul = document.getElementById('cnpjList');
    ul.innerHTML = '';
    if (cnpjs.length === 0) {
      ul.innerHTML = '<li class="text-sm text-slate-500">Nenhum CNPJ vinculado.</li>';
      return;
    }
    for (const c of cnpjs) {
      const li = document.createElement('li');
      li.className = 'flex items-center justify-between py-1 text-sm';
      li.innerHTML = `
        <span class="font-mono">${escapeHtml(c)}</span>
        <button class="text-red-600 text-xs hover:underline">Remover</button>
      `;
      li.querySelector('button').onclick = () => removerCnpj(c);
      ul.appendChild(li);
    }
  }

  function renderEsqueletos(esqueletos) {
    const container = document.getElementById('esqueletosList');
    if (esqueletos.length === 0) {
      container.innerHTML = '<p class="text-sm text-slate-500">Nenhum esqueleto ainda.</p>';
      return;
    }
    const t = document.createElement('table');
    t.className = 'min-w-full text-sm';
    t.innerHTML = `
      <thead class="bg-slate-100 text-left">
        <tr>
          <th class="px-3 py-2 font-medium">Versão</th>
          <th class="px-3 py-2 font-medium">Status</th>
          <th class="px-3 py-2 font-medium">Fingerprint</th>
          <th class="px-3 py-2 font-medium">Taxa sucesso</th>
          <th class="px-3 py-2 font-medium">Total</th>
          <th class="px-3 py-2 font-medium">Criado em</th>
          <th class="px-3 py-2 font-medium text-right">Ações</th>
        </tr>
      </thead>
      <tbody></tbody>`;
    const tbody = t.querySelector('tbody');
    for (const s of esqueletos) {
      const tr = document.createElement('tr');
      tr.className = 'border-t border-slate-200';
      // Conta fingerprints adicionais. Mostra "+N" quando há mais de 1
      // fingerprint registrado nesta versão — sinal de que o operador já
      // confirmou que diferentes fingerprints pertencem ao mesmo layout.
      const fps = s.fingerprints || [];
      const fpsExtras = fps.filter((f) => f && f !== s.fingerprint);
      const fpExtraTag = fpsExtras.length > 0
        ? `<span class="ml-1 inline-block px-1 rounded bg-blue-100 text-blue-700 text-[10px] font-medium" title="Fingerprints adicionais: ${escapeAttr(fpsExtras.join(', '))}">+${fpsExtras.length}</span>`
        : '';
      tr.innerHTML = `
        <td class="px-3 py-2">v${s.versao}</td>
        <td class="px-3 py-2 text-xs"><span class="inline-block px-2 py-0.5 rounded-full ${badgeStatus(s.status)}">${escapeHtml(s.status)}</span></td>
        <td class="px-3 py-2 font-mono text-xs">${escapeHtml(s.fingerprint.slice(0, 12))}…${fpExtraTag}</td>
        <td class="px-3 py-2">${Math.round((s.taxa_sucesso || 0) * 100)}%</td>
        <td class="px-3 py-2">${s.total_extracoes}</td>
        <td class="px-3 py-2 text-xs">${new Date(s.criado_em).toLocaleString('pt-BR')}</td>
        <td class="px-3 py-2 text-right whitespace-nowrap"></td>
      `;
      const acoes = tr.lastElementChild;

      const btnVer = document.createElement('button');
      btnVer.className = 'inline-block px-2 py-1 text-xs rounded border border-slate-300 hover:bg-slate-50 mr-1';
      btnVer.textContent = 'Ver/Editar';
      btnVer.onclick = () => abrirEditor(s.id);
      acoes.appendChild(btnVer);

      if (s.status === 'ativo') {
        const btn = document.createElement('button');
        btn.className = 'inline-block px-2 py-1 text-xs rounded border border-amber-300 text-amber-700 hover:bg-amber-50';
        btn.textContent = 'Desativar';
        btn.onclick = () => alternarStatus(s.id, 'desativar');
        acoes.appendChild(btn);
      } else {
        const btn = document.createElement('button');
        btn.className = 'inline-block px-2 py-1 text-xs rounded border border-green-300 text-green-700 hover:bg-green-50';
        btn.textContent = 'Reativar';
        btn.onclick = () => alternarStatus(s.id, 'reativar');
        acoes.appendChild(btn);
      }

      tbody.appendChild(tr);
    }
    container.innerHTML = '';
    container.appendChild(t);
  }

  function badgeStatus(s) {
    return {
      ativo: 'bg-green-100 text-green-800',
      inativo: 'bg-slate-200 text-slate-700',
      em_revisao: 'bg-amber-100 text-amber-800',
    }[s] || 'bg-slate-200 text-slate-700';
  }

  async function renomear(atual) {
    const novo = prompt('Novo nome da empresa:', atual);
    if (!novo || novo.trim() === atual) return;
    try {
      await App.apiJson('/api/empresas/' + empresaId, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ nome: novo.trim() }),
      });
    } catch (err) {
      App.toast('Erro: ' + err.message, 'error');
      return;
    }
    App.toast('Renomeado.', 'success');
    carregar();
  }

  async function adicionarCnpj() {
    const c = prompt('CNPJ (com ou sem pontuação):');
    if (!c) return;
    try {
      await App.apiJson('/api/empresas/' + empresaId, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cnpjs_adicionar: [c.trim()] }),
      });
      App.toast('CNPJ adicionado.', 'success');
      carregar();
    } catch (err) {
      App.toast('Erro: ' + err.message, 'error');
    }
  }

  async function removerCnpj(cnpj) {
    if (!confirm('Remover ' + cnpj + '?')) return;
    try {
      await App.apiJson('/api/empresas/' + empresaId, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cnpjs_remover: [cnpj] }),
      });
      App.toast('CNPJ removido.', 'success');
      carregar();
    } catch (err) {
      App.toast('Erro: ' + err.message, 'error');
    }
  }

  async function alternarStatus(esqueletoId, acao) {
    const verbo = acao === 'desativar' ? 'Desativar' : 'Reativar';
    if (!confirm(verbo + ' este esqueleto?')) return;
    try {
      await App.apiJson('/api/esqueletos/' + esqueletoId + '/' + acao, { method: 'POST' });
      App.toast(verbo + ' OK.', 'success');
      carregar();
    } catch (err) {
      App.toast('Erro: ' + err.message, 'error');
    }
  }

  async function abrirEditor(esqueletoId) {
    let dados;
    try {
      dados = await App.apiJson('/api/esqueletos/' + esqueletoId);
    } catch (err) {
      App.toast('Erro ao carregar: ' + err.message, 'error');
      return;
    }

    const overlay = document.createElement('div');
    overlay.className = 'fixed inset-0 bg-black/50 flex items-start justify-center p-4 z-50 overflow-y-auto';
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) overlay.remove();
    });

    const card = document.createElement('div');
    card.className = 'bg-white rounded-lg shadow-xl max-w-3xl w-full my-10 p-6';
    const fpsAll = dados.fingerprints || [];
    const fpExtras = fpsAll.filter((f) => f && f !== dados.fingerprint);
    const fpExtrasHtml = fpExtras.length
      ? `<details class="mt-1 text-xs">
           <summary class="cursor-pointer text-slate-600 hover:text-slate-900">${fpExtras.length} fingerprint(s) adicionais aceito(s)</summary>
           <ul class="mt-1 ml-4 list-disc text-slate-600 font-mono text-xs">
             ${fpExtras.map((f) => `<li>${escapeHtml(f)}</li>`).join('')}
           </ul>
         </details>`
      : '';
    card.innerHTML = `
      <div class="flex items-center justify-between mb-4">
        <h3 class="text-lg font-medium">Esqueleto v${dados.versao} — ${escapeHtml(dados.empresa_nome || '')}</h3>
        <button class="text-slate-500 hover:text-slate-900 text-2xl leading-none" data-close>&times;</button>
      </div>
      <p class="text-xs text-slate-500 mb-1">Fingerprint principal: <code>${escapeHtml(dados.fingerprint)}</code></p>
      ${fpExtrasHtml}
      <label class="block text-sm mb-2 mt-3">Estrutura</label>
      <textarea id="estrutura" rows="14" class="w-full border border-slate-300 rounded px-3 py-2 text-xs font-mono"></textarea>
      <p id="estruturaErro" class="mt-1 text-xs text-red-600"></p>
      <div class="flex justify-end gap-2 mt-4">
        <button data-close class="px-4 py-2 rounded border border-slate-300">Cancelar</button>
        <button id="salvarEstrutura" class="px-4 py-2 rounded bg-blue-600 text-white hover:bg-blue-700">Salvar</button>
      </div>
    `;

    const textarea = card.querySelector('#estrutura');
    const erroEl = card.querySelector('#estruturaErro');
    textarea.value = JSON.stringify(dados.estrutura, null, 2);
    textarea.addEventListener('input', () => {
      try { JSON.parse(textarea.value); erroEl.textContent = ''; }
      catch (e) { erroEl.textContent = 'JSON inválido: ' + e.message; }
    });

    card.querySelectorAll('[data-close]').forEach((b) => (b.onclick = () => overlay.remove()));
    card.querySelector('#salvarEstrutura').onclick = async () => {
      let estrutura;
      try { estrutura = JSON.parse(textarea.value); }
      catch { App.toast('JSON inválido.', 'error'); return; }
      try {
        await App.apiJson('/api/esqueletos/' + esqueletoId, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ estrutura }),
        });
        App.toast('Esqueleto atualizado.', 'success');
        overlay.remove();
        carregar();
      } catch (err) {
        App.toast('Erro: ' + err.message, 'error');
      }
    };

    overlay.appendChild(card);
    document.body.appendChild(overlay);
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[c]);
  }
  function escapeAttr(s) { return escapeHtml(s); }

  carregar();
})();
