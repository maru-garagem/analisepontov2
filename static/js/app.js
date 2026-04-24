// Dashboard: upload múltiplo + fila de cards com polling independente.
// Cada arquivo vira um card na fila com status próprio. Processamentos
// continuam em background no servidor mesmo se a aba for fechada.

(async function () {
  if (!(await App.ensureAuthed())) return;

  document.getElementById('logoutBtn').addEventListener('click', (e) => {
    e.currentTarget.disabled = true;
    e.currentTarget.textContent = 'Saindo...';
    App.logout();
  });

  // Mostra último resultado se voltamos do cadastro
  const ultimoRaw = sessionStorage.getItem('ultimo_resultado');
  if (ultimoRaw) {
    sessionStorage.removeItem('ultimo_resultado');
    try {
      renderResult(JSON.parse(ultimoRaw));
      App.toast('Esqueleto salvo e PDF extraído.', 'success');
    } catch {}
  }

  // Popula dropdown de modelo
  const modeloSelect = document.getElementById('modeloSelect');
  try {
    const data = await App.apiJson('/api/extract/modelos-disponiveis');
    for (const m of data.modelos || []) {
      const opt = document.createElement('option');
      opt.value = m;
      opt.textContent = m + (m === data.padrao ? '  (padrão)' : '');
      modeloSelect.appendChild(opt);
    }
  } catch {
    // falha silenciosa — fica só com "Padrão do servidor"
  }

  const dropZone = document.getElementById('dropZone');
  const fileInput = document.getElementById('fileInput');
  const filaCard = document.getElementById('filaCard');
  const fila = document.getElementById('fila');

  dropZone.addEventListener('click', () => fileInput.click());
  dropZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropZone.classList.add('bg-blue-50', 'border-blue-500');
  });
  dropZone.addEventListener('dragleave', () => {
    dropZone.classList.remove('bg-blue-50', 'border-blue-500');
  });
  dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropZone.classList.remove('bg-blue-50', 'border-blue-500');
    for (const f of e.dataTransfer.files) handleFile(f);
  });
  fileInput.addEventListener('change', () => {
    for (const f of fileInput.files) handleFile(f);
    fileInput.value = ''; // permite reenviar o mesmo arquivo depois
  });

  document.getElementById('limparFila').addEventListener('click', () => {
    for (const li of [...fila.children]) {
      if (li.dataset.status && li.dataset.status !== 'enviando' && li.dataset.status !== 'processando') {
        li.remove();
      }
    }
    if (fila.children.length === 0) filaCard.classList.add('hidden');
  });

  async function handleFile(file) {
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      App.toast(`"${file.name}" não é um PDF.`, 'error');
      return;
    }
    filaCard.classList.remove('hidden');
    const card = criarCard(file.name);
    fila.prepend(card);

    const fd = new FormData();
    fd.append('file', file);
    const modelo = modeloSelect.value;
    if (modelo) fd.append('modelo_potente', modelo);

    let start;
    try {
      atualizarCard(card, { status: 'enviando', msg: 'Enviando...' });
      start = await App.apiJson('/api/extract', { method: 'POST', body: fd });
    } catch (err) {
      atualizarCard(card, { status: 'falhou', msg: err.message });
      return;
    }

    atualizarCard(card, {
      status: 'processando',
      msg: 'Processando no servidor...',
      processingId: start.processing_id,
    });

    let final;
    try {
      final = await App.poll(
        () => `/api/extract/${start.processing_id}/status`,
        {
          intervalMs: 1500,
          maxMs: 600000,
          shouldStop: (d) => d.status !== 'em_processamento',
        }
      );
    } catch (err) {
      atualizarCard(card, { status: 'falhou', msg: err.message, processingId: start.processing_id });
      return;
    }

    if (final.status === 'aguardando_cadastro') {
      atualizarCard(card, {
        status: 'aguardando_cadastro',
        msg: 'Empresa nova — cadastro assistido pronto.',
        processingId: start.processing_id,
      });
      return;
    }

    if (final.status === 'nao_cartao_ponto') {
      atualizarCard(card, {
        status: 'nao_cartao_ponto',
        msg: 'Não é cartão de ponto.',
        processingId: start.processing_id,
      });
      return;
    }

    if (final.status === 'falhou') {
      atualizarCard(card, {
        status: 'falhou',
        msg: final.detalhe_erro || 'erro desconhecido',
        processingId: start.processing_id,
      });
      return;
    }

    // sucesso / sucesso_com_aviso
    atualizarCard(card, {
      status: final.status,
      msg: `${final.empresa_nome || 'Empresa'} — ${
        typeof final.score_conformidade === 'number'
          ? Math.round(final.score_conformidade * 100) + '%'
          : '—'
      }`,
      processingId: start.processing_id,
      dadosResultado: final,
    });
  }

  function criarCard(nome) {
    const li = document.createElement('li');
    li.className = 'border border-slate-200 rounded p-3 flex items-center justify-between gap-4 text-sm';
    li.innerHTML = `
      <div class="flex items-center gap-3 min-w-0">
        <span class="card-icon"></span>
        <div class="min-w-0">
          <div class="font-medium truncate" title="${escapeAttr(nome)}">${escapeHtml(nome)}</div>
          <div class="card-msg text-xs text-slate-500 truncate"></div>
        </div>
      </div>
      <div class="card-acoes flex items-center gap-2 whitespace-nowrap"></div>
    `;
    return li;
  }

  function atualizarCard(card, { status, msg, processingId, dadosResultado }) {
    card.dataset.status = status;
    if (processingId) card.dataset.processingId = processingId;

    const icon = card.querySelector('.card-icon');
    const msgEl = card.querySelector('.card-msg');
    const acoes = card.querySelector('.card-acoes');

    icon.innerHTML = iconePorStatus(status);
    msgEl.textContent = msg || '';
    acoes.innerHTML = '';

    if (status === 'aguardando_cadastro' && processingId) {
      const a = document.createElement('a');
      a.href = `/cadastro-assistido.html?id=${processingId}`;
      a.className = 'px-3 py-1 rounded bg-blue-600 text-white text-xs hover:bg-blue-700';
      a.textContent = 'Cadastrar →';
      // feedback imediato ao clicar
      a.addEventListener('click', () => {
        a.textContent = 'Abrindo...';
        a.classList.add('opacity-75', 'pointer-events-none');
      });
      acoes.appendChild(a);
    }

    if ((status === 'sucesso' || status === 'sucesso_com_aviso') && dadosResultado) {
      const btn = document.createElement('button');
      btn.className = 'px-3 py-1 rounded border border-slate-300 text-xs hover:bg-slate-50';
      btn.textContent = 'Ver';
      btn.addEventListener('click', () => {
        renderResult(dadosResultado);
        document.getElementById('resultCard').scrollIntoView({ behavior: 'smooth' });
      });
      acoes.appendChild(btn);
    }
  }

  function iconePorStatus(status) {
    const cores = {
      enviando: 'text-blue-600',
      processando: 'text-blue-600',
      aguardando_cadastro: 'text-amber-600',
      sucesso: 'text-green-600',
      sucesso_com_aviso: 'text-amber-600',
      falhou: 'text-red-600',
      nao_cartao_ponto: 'text-purple-600',
    };
    if (status === 'enviando' || status === 'processando') {
      return `<svg class="animate-spin h-4 w-4 ${cores[status]}" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" fill="none" opacity="0.25"/><path d="M4 12a8 8 0 018-8" stroke="currentColor" stroke-width="3" fill="none"/></svg>`;
    }
    const glifo = {
      aguardando_cadastro: '⏳',
      sucesso: '✓',
      sucesso_com_aviso: '⚠',
      falhou: '✗',
      nao_cartao_ponto: '?',
    }[status] || '•';
    return `<span class="text-lg ${cores[status] || 'text-slate-400'}">${glifo}</span>`;
  }

  function renderResult(data) {
    const resultCard = document.getElementById('resultCard');
    resultCard.classList.remove('hidden');

    const score = data.score_conformidade;
    const badge = document.getElementById('scoreBadge');
    if (typeof score === 'number') {
      const pct = Math.round(score * 100);
      badge.textContent = `Confiança: ${pct}%`;
      badge.className =
        'text-xs font-semibold px-2 py-1 rounded-full ' +
        (pct >= 85
          ? 'bg-green-100 text-green-800'
          : pct >= 70
          ? 'bg-amber-100 text-amber-800'
          : 'bg-red-100 text-red-800');
    } else {
      badge.textContent = '';
    }

    const meta = document.getElementById('resultMeta');
    meta.innerHTML = '';
    const metaRows = [
      ['Empresa', data.empresa_nome || '—'],
      ['CNPJ detectado', data.cnpj_detectado || '—'],
      ['Método', data.metodo_usado || '—'],
      ['Tempo (ms)', data.tempo_processamento_ms ?? '—'],
      ['Status', data.status],
    ];
    for (const [k, v] of metaRows) {
      const dt = document.createElement('dt');
      dt.className = 'text-slate-500';
      dt.textContent = k;
      const dd = document.createElement('dd');
      dd.className = 'text-slate-900 font-medium';
      dd.textContent = v;
      meta.appendChild(dt);
      meta.appendChild(dd);
    }

    const headerEl = document.getElementById('resultHeader');
    const resultado = data.resultado_json || {};
    headerEl.textContent = JSON.stringify(resultado.cabecalho || {}, null, 2);

    const linhas = resultado.linhas || [];
    const linesEl = document.getElementById('resultLines');
    linesEl.innerHTML = '';
    if (linhas.length === 0) {
      linesEl.innerHTML = '<p class="text-slate-500 text-sm">Nenhuma linha extraída.</p>';
    } else {
      const cols = Object.keys(linhas[0]);
      const t = document.createElement('table');
      t.className = 'min-w-full text-sm';
      const thead = document.createElement('thead');
      thead.className = 'bg-slate-100 text-left text-slate-700';
      const thr = document.createElement('tr');
      for (const c of cols) {
        const th = document.createElement('th');
        th.className = 'px-3 py-2 font-medium';
        th.textContent = c;
        thr.appendChild(th);
      }
      thead.appendChild(thr);
      t.appendChild(thead);
      const tbody = document.createElement('tbody');
      for (const linha of linhas) {
        const tr = document.createElement('tr');
        tr.className = 'border-t border-slate-200';
        for (const c of cols) {
          const td = document.createElement('td');
          td.className = 'px-3 py-2';
          const val = linha[c];
          td.textContent = val === null || val === undefined ? '' : String(val);
          tr.appendChild(td);
        }
        tbody.appendChild(tr);
      }
      t.appendChild(tbody);
      linesEl.appendChild(t);
    }

    const avisos = resultado.avisos || [];
    const wEl = document.getElementById('resultWarnings');
    wEl.innerHTML = '';
    if (avisos.length) {
      const h = document.createElement('h3');
      h.className = 'font-medium text-sm mb-2 text-amber-700';
      h.textContent = 'Avisos';
      const ul = document.createElement('ul');
      ul.className = 'list-disc list-inside text-sm text-amber-700';
      for (const a of avisos) {
        const li = document.createElement('li');
        li.textContent = a;
        ul.appendChild(li);
      }
      wEl.appendChild(h);
      wEl.appendChild(ul);
    }
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[c]);
  }
  function escapeAttr(s) { return escapeHtml(s); }
})();
