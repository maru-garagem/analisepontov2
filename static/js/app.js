// Dashboard principal: upload + polling de status + exibição de resultado.

(async function () {
  if (!(await App.ensureAuthed())) return;

  document.getElementById('logoutBtn').addEventListener('click', App.logout);

  const dropZone = document.getElementById('dropZone');
  const fileInput = document.getElementById('fileInput');
  const progress = document.getElementById('progress');
  const progressText = document.getElementById('progressText');
  const resultCard = document.getElementById('resultCard');

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
    if (e.dataTransfer.files.length > 0) handleFile(e.dataTransfer.files[0]);
  });
  fileInput.addEventListener('change', () => {
    if (fileInput.files.length > 0) handleFile(fileInput.files[0]);
  });

  async function handleFile(file) {
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      App.toast('Apenas arquivos PDF são aceitos.', 'error');
      return;
    }
    resultCard.classList.add('hidden');
    progress.classList.remove('hidden');
    progressText.textContent = 'Enviando...';

    const fd = new FormData();
    fd.append('file', file);
    let start;
    try {
      start = await App.apiJson('/api/extract', { method: 'POST', body: fd });
    } catch (err) {
      progress.classList.add('hidden');
      App.toast('Falha no upload: ' + err.message, 'error');
      return;
    }

    progressText.textContent = 'Processando...';
    try {
      const final = await App.poll(
        () => `/api/extract/${start.processing_id}/status`,
        {
          intervalMs: 1500,
          maxMs: 600000,
          shouldStop: (d) =>
            !['em_processamento'].includes(d.status),
        }
      );

      progress.classList.add('hidden');

      if (final.status === 'aguardando_cadastro') {
        App.toast('Empresa nova: indo para cadastro assistido...', 'info');
        location.href = `/cadastro-assistido.html?id=${start.processing_id}`;
        return;
      }

      if (final.status === 'nao_cartao_ponto') {
        App.toast('Documento não parece ser um cartão de ponto.', 'warn');
        return;
      }

      if (final.status === 'falhou') {
        App.toast('Falha: ' + (final.detalhe_erro || 'erro desconhecido'), 'error');
        return;
      }

      renderResult(final);
    } catch (err) {
      progress.classList.add('hidden');
      App.toast(err.message, 'error');
    }
  }

  function renderResult(data) {
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
})();
