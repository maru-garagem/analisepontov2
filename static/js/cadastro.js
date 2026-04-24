// Cadastro assistido: carrega proposta, renderiza PDF com PDF.js (esperando
// window.pdfjsLib carregar via ESM), e expõe state via Alpine.

function cadastroApp() {
  return {
    processingId: null,
    pdfDoc: null,
    paginaAtual: 1,
    totalPaginas: 0,
    empresa_candidata_id: null,
    empresa_candidata_nome: null,
    usarEmpresaExistente: false,
    amostra: [],
    colunasAmostra: [],
    confianca: null,
    estruturaTexto: '{}',
    erroEstrutura: null,
    form: {
      nome_empresa: '',
      cnpjs: [],
    },

    get podeConfirmar() {
      if (this.erroEstrutura) return false;
      if (this.usarEmpresaExistente) return true;
      return this.form.nome_empresa.trim().length > 0;
    },

    async init() {
      const authed = await App.ensureAuthed();
      if (!authed) return;

      const params = new URLSearchParams(location.search);
      this.processingId = params.get('id');
      if (!this.processingId) {
        App.toast('ID de processamento ausente.', 'error');
        location.href = '/';
        return;
      }

      // Carrega proposta
      let proposta;
      try {
        proposta = await App.apiJson(
          `/api/extract/${this.processingId}/cadastro-proposta`
        );
      } catch (err) {
        App.toast('Proposta indisponível: ' + err.message, 'error');
        setTimeout(() => (location.href = '/'), 2000);
        return;
      }

      this.empresa_candidata_id = proposta.empresa_candidata_id;
      this.empresa_candidata_nome = proposta.empresa_candidata_nome;
      this.usarEmpresaExistente = !!proposta.empresa_candidata_id;
      this.form.nome_empresa = proposta.nome_empresa_sugerido || '';
      this.form.cnpjs = [...proposta.cnpjs_sugeridos];
      if (proposta.cnpj_detectado_no_pdf && !this.form.cnpjs.includes(proposta.cnpj_detectado_no_pdf)) {
        this.form.cnpjs.unshift(proposta.cnpj_detectado_no_pdf);
      }
      if (this.form.cnpjs.length === 0) this.form.cnpjs.push('');
      this.amostra = proposta.amostra_linhas || [];
      this.colunasAmostra = this.amostra.length > 0 ? Object.keys(this.amostra[0]) : [];
      this.confianca = proposta.confianca ?? null;
      this.estruturaTexto = JSON.stringify(proposta.estrutura || {}, null, 2);

      // Watch JSON válido
      this.$watch('estruturaTexto', (v) => {
        try {
          JSON.parse(v);
          this.erroEstrutura = null;
        } catch (e) {
          this.erroEstrutura = 'JSON inválido: ' + e.message;
        }
      });

      // Carrega PDF
      try {
        await this.carregarPDF();
      } catch (err) {
        App.toast('Falha ao carregar PDF: ' + err.message, 'error');
      }
    },

    async carregarPDF() {
      // Aguarda pdfjsLib do ESM module
      let tentativas = 0;
      while (!window.pdfjsLib && tentativas < 20) {
        await new Promise((r) => setTimeout(r, 100));
        tentativas++;
      }
      if (!window.pdfjsLib) throw new Error('PDF.js não carregou.');

      const resp = await fetch(`/api/extract/${this.processingId}/pdf`, {
        credentials: 'same-origin',
      });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const buf = await resp.arrayBuffer();
      this.pdfDoc = await window.pdfjsLib.getDocument({ data: buf }).promise;
      this.totalPaginas = this.pdfDoc.numPages;
      await this.renderizarPagina(1);
    },

    async renderizarPagina(n) {
      if (!this.pdfDoc) return;
      const page = await this.pdfDoc.getPage(n);
      const canvas = document.getElementById('pdfCanvas');
      const ctx = canvas.getContext('2d');
      const viewport = page.getViewport({ scale: 1.3 });
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      await page.render({ canvasContext: ctx, viewport }).promise;
      this.paginaAtual = n;
    },

    paginaAnterior() {
      if (this.paginaAtual > 1) this.renderizarPagina(this.paginaAtual - 1);
    },
    proximaPagina() {
      if (this.paginaAtual < this.totalPaginas) this.renderizarPagina(this.paginaAtual + 1);
    },

    adicionarCnpj() {
      this.form.cnpjs.push('');
    },
    removerCnpj(i) {
      this.form.cnpjs.splice(i, 1);
    },

    async confirmar() {
      let estrutura;
      try {
        estrutura = JSON.parse(this.estruturaTexto);
      } catch {
        App.toast('JSON da estrutura inválido.', 'error');
        return;
      }

      const payload = {
        nome_empresa: this.form.nome_empresa || this.empresa_candidata_nome || 'Empresa',
        cnpjs: this.form.cnpjs.map((c) => c.trim()).filter(Boolean),
        estrutura: estrutura,
        exemplos_validados: this.amostra.length
          ? [{ trecho_pdf: '', saida_esperada: this.amostra[0] }]
          : [],
        empresa_id: this.usarEmpresaExistente ? this.empresa_candidata_id : null,
      };

      try {
        const res = await App.apiPostJson(
          `/api/extract/${this.processingId}/cadastro-confirmar`,
          payload
        );
        App.toast('Esqueleto salvo. Extraindo...', 'success');
        // Volta para o dashboard exibindo o resultado via sessionStorage
        sessionStorage.setItem('ultimo_resultado', JSON.stringify(res));
        location.href = '/';
      } catch (err) {
        App.toast('Erro ao confirmar: ' + err.message, 'error');
      }
    },

    async cancelar() {
      if (!confirm('Cancelar o cadastro? O esqueleto não será salvo.')) return;
      try {
        await App.apiPostJson(
          `/api/extract/${this.processingId}/cadastro-cancelar`,
          {}
        );
      } catch {
        // ignora — já saindo
      }
      location.href = '/';
    },
  };
}
