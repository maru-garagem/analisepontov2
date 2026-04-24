// Dashboard: upload (1 ou múltiplo) + fila de cards com polling independente.
//
// Regra da UX:
//  - Upload de 1 PDF apenas: se cair em cadastro assistido, redirect direto
//    para a tela de cadastro (comportamento tradicional, não perde o fluxo
//    de alteração/validação).
//  - Upload múltiplo: cada arquivo vira card na fila; cadastros pendentes
//    ficam destacados com botão "Cadastrar →" e o usuário decide por qual
//    começar.
//
// A fila é persistida em sessionStorage para sobreviver a F5 ou ao retorno
// da tela de cadastro assistido (que faz location.href = '/').

(async function () {
  if (!(await App.ensureAuthed())) return;

  document.getElementById('logoutBtn').addEventListener('click', (e) => {
    e.currentTarget.disabled = true;
    e.currentTarget.textContent = 'Saindo...';
    App.logout();
  });

  // Mostra último resultado se voltamos do cadastro assistido
  const ultimoRaw = sessionStorage.getItem('ultimo_resultado');
  if (ultimoRaw) {
    sessionStorage.removeItem('ultimo_resultado');
    try {
      renderResult(JSON.parse(ultimoRaw));
      App.toast('Esqueleto salvo e PDF extraído.', 'success');
    } catch {}
  }

  // Popula dropdown de modelo + toggle de webhook
  const modeloSelect = document.getElementById('modeloSelect');
  const webhookToggle = document.getElementById('webhookToggle');
  const enviarWebhookCb = document.getElementById('enviarWebhook');
  try {
    const data = await App.apiJson('/api/extract/modelos-disponiveis');
    for (const m of data.modelos || []) {
      const opt = document.createElement('option');
      opt.value = m.id;
      const anotacoes = [];
      if (m.id === data.padrao) anotacoes.push('padrão');
      if (m.suporta_visao === false) anotacoes.push('sem visão — só PDF digital');
      opt.textContent = m.id + (anotacoes.length ? ` (${anotacoes.join(', ')})` : '');
      modeloSelect.appendChild(opt);
    }
    if (data.webhook_disponivel) {
      webhookToggle.classList.remove('hidden');
    }
  } catch {}

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
    receberArquivos([...e.dataTransfer.files]);
  });
  fileInput.addEventListener('change', () => {
    receberArquivos([...fileInput.files]);
    fileInput.value = '';
  });

  function receberArquivos(arquivos) {
    const pdfs = arquivos.filter((f) => f.name.toLowerCase().endsWith('.pdf'));
    const rejeitados = arquivos.length - pdfs.length;
    if (rejeitados > 0) {
      App.toast(`${rejeitados} arquivo(s) não-PDF ignorados.`, 'warn');
    }
    if (pdfs.length === 0) return;

    // Se o usuário enviou exatamente 1 PDF e ele cair em cadastro,
    // redireciona automaticamente para a tela de cadastro assistido.
    const abrirCadastroSePrecisar = pdfs.length === 1;
    for (const f of pdfs) handleFile(f, abrirCadastroSePrecisar);
  }

  document.getElementById('limparFila').addEventListener('click', () => {
    for (const li of [...fila.children]) {
      if (
        li.dataset.status &&
        li.dataset.status !== 'enviando' &&
        li.dataset.status !== 'processando'
      ) {
        li.remove();
        removerDoSession(li.dataset.processingId);
      }
    }
    if (fila.children.length === 0) filaCard.classList.add('hidden');
  });

  // --- Persistência em sessionStorage ------------------------------------

  const SS_KEY = 'fila_processamentos';

  function lerSession() {
    try {
      return JSON.parse(sessionStorage.getItem(SS_KEY) || '[]');
    } catch {
      return [];
    }
  }
  function salvarSession(arr) {
    sessionStorage.setItem(SS_KEY, JSON.stringify(arr));
  }
  function adicionarNoSession(item) {
    const arr = lerSession().filter((i) => i.processing_id !== item.processing_id);
    arr.push(item);
    salvarSession(arr);
  }
  function removerDoSession(pid) {
    if (!pid) return;
    salvarSession(lerSession().filter((i) => i.processing_id !== pid));
  }

  // Restaura fila ao abrir o dashboard (F5, volta de cadastro, etc.)
  const pendentes = lerSession();
  if (pendentes.length > 0) {
    filaCard.classList.remove('hidden');
    for (const p of pendentes) restaurarCard(p);
  }

  async function restaurarCard({ processing_id, nome }) {
    const card = criarCard(nome || '(arquivo)');
    card.dataset.processingId = processing_id;
    fila.prepend(card);
    atualizarCard(card, { status: 'processando', msg: 'Verificando status...', processingId: processing_id });
    try {
      const status = await App.apiJson(`/api/extract/${processing_id}/status`);
      if (status.status === 'em_processamento') {
        continuarPolling(card, processing_id);
      } else {
        refletirStatusFinal(card, status);
      }
    } catch (err) {
      atualizarCard(card, { status: 'falhou', msg: 'Consulta falhou: ' + err.message, processingId: processing_id });
    }
  }

  async function continuarPolling(card, processing_id) {
    try {
      const final = await App.poll(
        () => `/api/extract/${processing_id}/status`,
        { intervalMs: 1500, maxMs: 600000, shouldStop: (d) => d.status !== 'em_processamento' }
      );
      refletirStatusFinal(card, final);
    } catch (err) {
      atualizarCard(card, { status: 'falhou', msg: err.message, processingId: processing_id });
    }
  }

  function refletirStatusFinal(card, final) {
    const pid = card.dataset.processingId;
    if (final.status === 'aguardando_cadastro') {
      atualizarCard(card, {
        status: 'aguardando_cadastro',
        msg: 'Empresa nova — cadastro assistido pronto.',
        processingId: pid,
      });
    } else if (final.status === 'nao_cartao_ponto') {
      atualizarCard(card, { status: 'nao_cartao_ponto', msg: 'Não é cartão de ponto.', processingId: pid });
      removerDoSession(pid);
    } else if (final.status === 'falhou') {
      atualizarCard(card, { status: 'falhou', msg: final.detalhe_erro || 'erro desconhecido', processingId: pid });
      removerDoSession(pid);
    } else {
      atualizarCard(card, {
        status: final.status,
        msg: `${final.empresa_nome || 'Empresa'} — ${
          typeof final.score_conformidade === 'number'
            ? Math.round(final.score_conformidade * 100) + '%'
            : '—'
        }`,
        processingId: pid,
        dadosResultado: final,
      });
      removerDoSession(pid);
    }
  }

  // --- Upload de um arquivo ---------------------------------------------

  async function handleFile(file, abrirCadastroSePrecisar) {
    filaCard.classList.remove('hidden');
    const card = criarCard(file.name);
    fila.prepend(card);
    atualizarCard(card, { status: 'enviando', msg: 'Enviando...' });

    const fd = new FormData();
    fd.append('file', file);
    const modelo = modeloSelect.value;
    if (modelo) fd.append('modelo_potente', modelo);
    if (enviarWebhookCb && enviarWebhookCb.checked) {
      fd.append('enviar_webhook', 'true');
    }

    let start;
    try {
      start = await App.apiJson('/api/extract', { method: 'POST', body: fd });
    } catch (err) {
      atualizarCard(card, { status: 'falhou', msg: err.message });
      return;
    }

    card.dataset.processingId = start.processing_id;
    adicionarNoSession({ processing_id: start.processing_id, nome: file.name });
    atualizarCard(card, {
      status: 'processando',
      msg: 'Processando no servidor...',
      processingId: start.processing_id,
    });

    let final;
    try {
      final = await App.poll(
        () => `/api/extract/${start.processing_id}/status`,
        { intervalMs: 1500, maxMs: 600000, shouldStop: (d) => d.status !== 'em_processamento' }
      );
    } catch (err) {
      atualizarCard(card, { status: 'falhou', msg: err.message, processingId: start.processing_id });
      return;
    }

    if (final.status === 'aguardando_cadastro' && abrirCadastroSePrecisar) {
      // 1 único PDF e cai em cadastro → abre a tela direto.
      App.toast('Empresa nova: indo para cadastro assistido...', 'info');
      location.href = `/cadastro-assistido.html?id=${start.processing_id}`;
      return;
    }

    refletirStatusFinal(card, final);
  }

  // --- Renderização dos cards -------------------------------------------

  function criarCard(nome) {
    const li = document.createElement('li');
    li.className = 'border border-slate-200 rounded p-3 flex items-center justify-between gap-4 text-sm transition';
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

    // destaque visual para quando exige ação do usuário
    card.classList.remove('border-blue-400', 'bg-blue-50');
    if (status === 'aguardando_cadastro') {
      card.classList.add('border-blue-400', 'bg-blue-50');
    }

    card.querySelector('.card-icon').innerHTML = iconePorStatus(status);
    card.querySelector('.card-msg').textContent = msg || '';

    const acoes = card.querySelector('.card-acoes');
    acoes.innerHTML = '';

    if (status === 'aguardando_cadastro' && processingId) {
      const a = document.createElement('a');
      a.href = `/cadastro-assistido.html?id=${processingId}`;
      a.className = 'px-3 py-1 rounded bg-blue-600 text-white text-xs hover:bg-blue-700 font-medium';
      a.innerHTML = 'Cadastrar →';
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

  // --- Resultado renderizado --------------------------------------------

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
