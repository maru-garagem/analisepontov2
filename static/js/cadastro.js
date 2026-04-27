// Cadastro assistido: carrega proposta e mostra PDF via iframe com blob URL
// (viewer nativo do navegador — simples e sem dependência de PDF.js).
//
// Decisões UX dessa tela:
// - Quando o CNPJ casa com empresa que JÁ tem esqueleto ativo, oferecemos
//   dois caminhos visíveis: "anexar layout à versão atual" (default,
//   recomendado) ou "criar nova versão". Isso evita o looping de cadastros
//   sucessivos que aparecia quando o fingerprint flutuava entre meses do
//   mesmo layout.
// - O bloco "Completar data do período" é um nível mais alto que JSON cru:
//   inputs de campo/coluna com explicação. O usuário não precisa decorar
//   a estrutura do parsing — só preenche os 3 nomes.

function cadastroApp() {
  return {
    processingId: null,
    pdfUrl: null,
    erroPdf: null,
    empresa_candidata_id: null,
    empresa_candidata_nome: null,
    esqueletoAtivo: null,    // EsqueletoAtivoInfo ou null
    usarEmpresaExistente: false,
    // Modo escolhido quando há esqueleto ativo: 'anexar' (default) ou 'nova_versao'.
    // Vazio quando não há esqueleto ativo.
    modoCadastro: '',
    amostra: [],
    colunasAmostra: [],
    confianca: null,
    estruturaTexto: '{}',
    erroEstrutura: null,
    enviando: false,
    cancelando: false,
    modelosBaratos: [],
    modeloBaratoPadrao: '',
    modeloFallback: '',

    // UI didática para parsing.completar_data_do_periodo
    completarData: {
      ativo: false,
      campo_periodo: 'periodo',
      coluna_dia: 'dia',
      coluna_destino: 'data',
    },

    form: {
      nome_empresa: '',
      cnpjs: [],
    },

    get podeConfirmar() {
      if (this.enviando || this.cancelando) return false;
      if (this.erroEstrutura) return false;
      if (this.esqueletoAtivo) {
        // Quando há esqueleto ativo, exigimos uma escolha explícita.
        if (!this.modoCadastro) return false;
        return true;
      }
      if (this.usarEmpresaExistente) return true;
      return this.form.nome_empresa.trim().length > 0;
    },

    get textoBotaoConfirmar() {
      if (this.modoCadastro === 'anexar') {
        return 'Anexar layout e extrair';
      }
      if (this.modoCadastro === 'nova_versao') {
        return 'Criar nova versão e extrair';
      }
      return 'Confirmar e salvar esqueleto';
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

      // Catálogo de modelos baratos para o dropdown
      try {
        const catalogo = await App.apiJson('/api/extract/modelos-disponiveis');
        this.modelosBaratos = catalogo.modelos_baratos || [];
        this.modeloBaratoPadrao = catalogo.padrao_barato || '';
      } catch {}

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
      this.esqueletoAtivo = proposta.esqueleto_ativo_da_empresa || null;
      // Quando há esqueleto ativo, usar empresa existente é implícito.
      this.usarEmpresaExistente = !!proposta.empresa_candidata_id;
      // Default: anexar é a opção segura (não cria nova versão).
      if (this.esqueletoAtivo) {
        this.modoCadastro = 'anexar';
      }
      this.form.nome_empresa = proposta.nome_empresa_sugerido || '';
      this.form.cnpjs = [...proposta.cnpjs_sugeridos];
      if (proposta.cnpj_detectado_no_pdf && !this.form.cnpjs.includes(proposta.cnpj_detectado_no_pdf)) {
        this.form.cnpjs.unshift(proposta.cnpj_detectado_no_pdf);
      }
      if (this.form.cnpjs.length === 0) this.form.cnpjs.push('');
      this.amostra = proposta.amostra_linhas || [];
      this.colunasAmostra = this.amostra.length > 0 ? Object.keys(this.amostra[0]) : [];
      this.confianca = proposta.confianca ?? null;

      const estrutura = proposta.estrutura || {};
      if (estrutura.modelo_fallback) {
        this.modeloFallback = estrutura.modelo_fallback;
      }
      // Se a IA já propôs completar_data_do_periodo, hidrata a UI.
      const cdp = estrutura.parsing?.completar_data_do_periodo;
      if (cdp && typeof cdp === 'object') {
        this.completarData.ativo = true;
        this.completarData.campo_periodo = cdp.campo_periodo || this.completarData.campo_periodo;
        this.completarData.coluna_dia = cdp.coluna_dia || this.completarData.coluna_dia;
        this.completarData.coluna_destino = cdp.coluna_destino || cdp.coluna_dia || this.completarData.coluna_destino;
      }
      this.estruturaTexto = JSON.stringify(estrutura, null, 2);

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
      if (this.enviando) return;
      let estrutura;
      try {
        estrutura = JSON.parse(this.estruturaTexto);
      } catch {
        App.toast('JSON da estrutura inválido.', 'error');
        return;
      }

      this.enviando = true;
      App.toast('Salvando esqueleto e extraindo...', 'info');

      // 1. Modelo fallback escolhido na UI vai pra estrutura.modelo_fallback.
      if (this.modeloFallback) {
        estrutura.modelo_fallback = this.modeloFallback;
      } else {
        delete estrutura.modelo_fallback;
      }

      // 2. completar_data_do_periodo: pega valores da UI didática.
      estrutura.parsing = estrutura.parsing || {};
      if (this.completarData.ativo) {
        const existente = estrutura.parsing.completar_data_do_periodo || {};
        estrutura.parsing.completar_data_do_periodo = {
          // Preserva regex_periodo se já existir; senão, default do backend.
          ...existente,
          campo_periodo: this.completarData.campo_periodo.trim(),
          coluna_dia: this.completarData.coluna_dia.trim(),
          coluna_destino: (this.completarData.coluna_destino || this.completarData.coluna_dia).trim(),
        };
      } else {
        delete estrutura.parsing.completar_data_do_periodo;
      }

      // 3. Decide caminho: anexar (sem nova versão) ou criar nova versão.
      const anexar = this.esqueletoAtivo && this.modoCadastro === 'anexar';

      const payload = {
        nome_empresa: this.form.nome_empresa || this.empresa_candidata_nome || 'Empresa',
        cnpjs: this.form.cnpjs.map((c) => c.trim()).filter(Boolean),
        estrutura: estrutura,
        exemplos_validados: this.amostra.length
          ? [{ trecho_pdf: '', saida_esperada: this.amostra[0] }]
          : [],
        empresa_id: this.usarEmpresaExistente ? this.empresa_candidata_id : null,
        anexar_a_versao_atual: !!anexar,
      };

      try {
        const res = await App.apiPostJson(
          `/api/extract/${this.processingId}/cadastro-confirmar`,
          payload
        );
        sessionStorage.setItem('ultimo_resultado', JSON.stringify(res));
        if (this.pdfUrl) {
          try { URL.revokeObjectURL(this.pdfUrl); } catch {}
        }
        location.href = '/';
      } catch (err) {
        this.enviando = false;
        App.toast('Erro ao confirmar: ' + err.message, 'error');
      }
    },

    async cancelar() {
      if (this.cancelando) return;
      if (!confirm('Cancelar o cadastro? O esqueleto não será salvo.')) return;
      this.cancelando = true;
      App.apiPostJson(`/api/extract/${this.processingId}/cadastro-cancelar`, {}).catch(() => {});
      if (this.pdfUrl) {
        try { URL.revokeObjectURL(this.pdfUrl); } catch {}
      }
      location.href = '/';
    },
  };
}
