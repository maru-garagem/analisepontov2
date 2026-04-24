// Cadastro assistido: carrega proposta e mostra PDF via iframe com blob URL
// (viewer nativo do navegador — simples e sem dependência de PDF.js).

function cadastroApp() {
  return {
    processingId: null,
    pdfUrl: null,
    erroPdf: null,
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

      // Valida JSON em tempo real
      this.$watch('estruturaTexto', (v) => {
        try {
          JSON.parse(v);
          this.erroEstrutura = null;
        } catch (e) {
          this.erroEstrutura = 'JSON inválido: ' + e.message;
        }
      });

      // Carrega PDF
      await this.carregarPDF();
    },

    async carregarPDF() {
      this.erroPdf = null;
      // Libera blob URL anterior, se existir, antes de criar outra.
      if (this.pdfUrl) {
        try { URL.revokeObjectURL(this.pdfUrl); } catch {}
        this.pdfUrl = null;
      }

      try {
        const resp = await fetch(`/api/extract/${this.processingId}/pdf`, {
          credentials: 'same-origin',
        });
        if (!resp.ok) {
          if (resp.status === 404) {
            throw new Error('PDF expirou (TTL 1h). Reenvie o arquivo.');
          }
          throw new Error('HTTP ' + resp.status + ' ao baixar PDF.');
        }
        const blob = await resp.blob();
        this.pdfUrl = URL.createObjectURL(blob);
        console.log('[cadastro] PDF carregado,', blob.size, 'bytes');
      } catch (err) {
        console.error('[cadastro] erro ao carregar PDF:', err);
        this.erroPdf = err.message;
      }
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
        sessionStorage.setItem('ultimo_resultado', JSON.stringify(res));
        if (this.pdfUrl) {
          try { URL.revokeObjectURL(this.pdfUrl); } catch {}
        }
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
      if (this.pdfUrl) {
        try { URL.revokeObjectURL(this.pdfUrl); } catch {}
      }
      location.href = '/';
    },
  };
}
