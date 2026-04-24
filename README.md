# PontoExtract v2

Sistema de extração estruturada de **cartões de ponto trabalhistas brasileiros**. Recebe PDFs, identifica a empresa e devolve um JSON com cabeçalho (empresa, funcionário, período) e linhas (data, entrada, saída, pausas, observações).

A grande sacada: para cada empresa, o sistema aprende **uma vez só** como ler o PDF daquela empresa e salva isso num "esqueleto". A partir daí, todos os PDFs futuros daquela mesma empresa são extraídos em segundos, sem IA, com alto grau de precisão. Só o primeiro PDF de cada empresa custa dinheiro (IA Vision).

---

## Sumário

- [Conceito central — esqueletos por empresa](#conceito-central--esqueletos-por-empresa)
- [Stack](#stack)
- [Quick start local](#quick-start-local)
- [Deploy no Railway](#deploy-no-railway)
- [Variáveis de ambiente](#variáveis-de-ambiente)
- [Arquitetura de pastas](#arquitetura-de-pastas)
- [Fluxos principais](#fluxos-principais)
- [Endpoints da API](#endpoints-da-api)
- [Métodos de extração](#métodos-de-extração)
- [Frontend](#frontend)
- [Banco de dados e migrations](#banco-de-dados-e-migrations)
- [Autenticação](#autenticação)
- [Webhooks](#webhooks)
- [LGPD e segurança](#lgpd-e-segurança)
- [Troubleshooting](#troubleshooting)
- [Documentos complementares](#documentos-complementares)

---

## Conceito central — esqueletos por empresa

**O problema que resolvemos:** IA genérica extraindo PDFs de ponto erra porque cada empresa tem um layout diferente, e redescobrir o layout a cada extração é caro e impreciso.

**Insight:** folhas de ponto da **mesma empresa são sempre iguais**. Se aprendermos o layout uma vez e guardarmos, podemos reusar infinitamente.

### O que é um esqueleto

Um esqueleto é um JSON salvo no banco que contém:

- **`metodo_preferencial`** — `plumber_direto`, `ocr_guiado` ou `ia_barata_com_exemplos`
- **`modelo_fallback`** — modelo OpenRouter a usar se cair no fallback IA
- **`cabecalho`** — regras para extrair cada campo (razão social, CNPJ, funcionário, matrícula, período). Cada campo é `{tipo: "ancora_regex", regex: "..."}` ou `{tipo: "regex_cnpj"}` ou `{tipo: "literal", valor: "..."}`
- **`tabela`** — especificação da tabela de batidas: número de colunas esperado, nome e tipo de cada coluna (`data`, `hora`, `numero`, `texto`), regex de linhas a descartar (ex: linhas de total), regex da linha de cabeçalho da tabela
- **`parsing`** — formato de hora/data, valor de célula vazia, ano default

Exemplo mínimo:

```json
{
  "metodo_preferencial": "plumber_direto",
  "modelo_fallback": "x-ai/grok-4-fast",
  "cabecalho": {
    "empresa_nome": {"tipo": "ancora_regex", "regex": "(?i)empresa[:\\s]+([A-ZÁ-Ú\\s]+)"},
    "cnpj": {"tipo": "regex_cnpj"},
    "funcionario_nome": {"tipo": "ancora_regex", "regex": "(?i)funcion[aá]rio[:\\s]+([^\\n]+)"},
    "periodo": {"tipo": "ancora_regex", "regex": "(?i)per[ií]odo[:\\s]+(.+)"}
  },
  "tabela": {
    "num_colunas_esperado": 6,
    "colunas": [
      {"nome": "data", "tipo": "data"},
      {"nome": "dia_semana", "tipo": "texto"},
      {"nome": "entrada", "tipo": "hora"},
      {"nome": "saida_pausa", "tipo": "hora"},
      {"nome": "volta_pausa", "tipo": "hora"},
      {"nome": "saida", "tipo": "hora"},
      {"nome": "observacao", "tipo": "texto"}
    ],
    "linhas_descartar_regex": ["(?i)^total", "(?i)^subtotal"],
    "header_row_regex": "(?i)data.*entrada.*sa[ií]da"
  },
  "parsing": {
    "formato_hora": "HH:MM",
    "formato_data": "DD/MM/YYYY",
    "ano_default": null,
    "celula_vazia_valor": null
  }
}
```

### Como um esqueleto é criado

1. Usuário sobe um PDF de uma empresa nova.
2. Sistema detecta que o CNPJ + fingerprint do layout são desconhecidos.
3. Usuário é levado à **tela de cadastro assistido** — PDF preview à esquerda, formulário à direita.
4. IA Vision potente (Claude Opus, GPT-5, Gemini…) analisa a primeira página e propõe o esqueleto + extrai 3–5 linhas de amostra.
5. Usuário confere visualmente (amostras batem com o PDF?) e pode editar o JSON da estrutura antes de confirmar.
6. Ao confirmar, o esqueleto é salvo no banco associado à empresa.

### Como um esqueleto é usado (fluxo rápido)

1. Novo PDF chega.
2. CNPJ extraído + fingerprint calculado.
3. Matching contra esqueletos ativos — se bate, aplica o método declarado (`plumber_direto` na maioria) e produz JSON em ~100ms.
4. Se o método principal falhar (0 linhas, colunas tipadas vazias, ruído), **cascata automática de fallback**: `plumber → ocr → ia_barata`.

---

## Stack

| Camada | Tecnologia | Versão |
|---|---|---|
| Linguagem | Python | 3.11 |
| Web framework | FastAPI | 0.115 |
| ORM | SQLAlchemy 2.0 (sync) | 2.0.36 |
| Migrations | Alembic | 1.13 |
| DB produção | PostgreSQL | 16 |
| DB dev (alternativa) | SQLite (com paridade de schema limitada) | — |
| PDF digital | pdfplumber + pypdf | 0.11 + 5.1 |
| OCR | Tesseract (pacote `por`) + pdf2image + Poppler | — |
| LLM | OpenRouter (qualquer provedor) via httpx | — |
| Frontend | HTML + Tailwind CDN + Alpine.js CDN + vanilla JS | — |
| PDF preview | iframe nativo do navegador com blob URL | — |
| Deploy | Docker + Railway | — |

**Escolhas intencionais:**
- **FastAPI sync, não async** — pipeline é CPU-bound (pdfplumber, Tesseract). Async agregaria `run_in_threadpool` sem ganho. Chamadas de LLM rodam em `BackgroundTasks`.
- **Alpine.js só onde realmente precisa** — tela de cadastro assistido. Resto é vanilla.
- **PDF via iframe, não PDF.js** — navegador renderiza com viewer nativo. Zero dependência de CDN externa pra isso.
- **Nenhum processo externo de fila** (Celery, RQ) — `BackgroundTasks` do FastAPI dá conta do volume esperado, 1 réplica no Railway.

---

## Quick start local

### Com Docker (recomendado)

Requisitos: Docker Desktop ou equivalente.

```bash
# 1. Clonar
git clone https://github.com/maru-garagem/analisepontov2.git
cd analisepontov2

# 2. Criar o .env a partir do template
cp .env.example .env

# 3. Editar o .env. Obrigatório ajustar:
#    ACCESS_PASSWORD  (mínimo 16 caracteres)
#    SESSION_SECRET   (mínimo 32 caracteres — gere com o comando abaixo)
#    OPENROUTER_API_KEY  (pode ser dummy para subir; IA só é chamada em cadastro assistido)
python -c "import secrets; print(secrets.token_urlsafe(48))"

# 4. Subir tudo (Postgres + app)
docker-compose up --build
```

Acessar: http://localhost:8000 → tela de login.

Endpoints de verificação:
- Health: http://localhost:8000/api/health
- Swagger (só em dev): http://localhost:8000/docs

### Sem Docker

Para smoke tests rápidos. Não é o caminho oficial — use o Docker pra tudo que for mais que "ver se sobe".

Requisitos no sistema:
- Python 3.11 (**não** 3.12/3.13/3.14 — wheels de pydantic-core podem não existir ainda)
- Tesseract com pacote de português (`tesseract-ocr-por`)
- Poppler (`poppler-utils`)

```bash
python -m venv venv
# Linux/Mac: source venv/bin/activate
# Windows:   venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# Deixe DATABASE_URL vazio no .env → cai em SQLite local (só pra smoke test)

alembic upgrade head
uvicorn main:app --reload
```

---

## Deploy no Railway

### Primeira vez

1. **Criar o projeto** apontando para o GitHub do repo.
2. **Adicionar serviço PostgreSQL** (botão "Add service" → "Database" → "PostgreSQL"). Railway injeta `DATABASE_URL` automaticamente.
3. **Configurar variáveis** no painel (aba "Variables"):

   Obrigatórias:
   - `ACCESS_PASSWORD` — senha de acesso ao painel, mínimo 16 caracteres
   - `SESSION_SECRET` — mínimo 32 caracteres aleatórios (`python -c "import secrets; print(secrets.token_urlsafe(48))"`)
   - `OPENROUTER_API_KEY` — sua chave do OpenRouter
   - `ENV=production`

   Opcionais (tudo tem default sensato):
   - `OPENROUTER_MODEL_POTENTE` — default `x-ai/grok-4`
   - `OPENROUTER_MODEL_BARATO` — default `x-ai/grok-4-fast`
   - `DEFAULT_WEBHOOK_URL` — URL do webhook default (se setado, o checkbox "Enviar para webhook" aparece na UI)
   - `ALLOWED_ORIGINS` — lista CSV de domínios permitidos para CORS; vazio = mesmo-origem apenas

4. **Push para o main**: Railway detecta o Dockerfile e builda. `entrypoint.py` roda `alembic upgrade head` antes de subir o uvicorn.

### Healthcheck

`/api/health` é independente de banco e LLM — usado como liveness probe. Configurado em `railway.toml`.

### Gotchas que já pegamos no deploy inicial

1. **`railway.toml` e `Dockerfile` brigam**: se tiver `startCommand` no `railway.toml`, ele sobrescreve o `CMD` do Dockerfile. Hoje o `.toml` tem só `healthcheckPath` — o Dockerfile é a fonte única da verdade de como o container sobe.
2. **SESSION_SECRET < 32 chars**: o Pydantic valida no startup e o alembic falha, com traceback incluindo o próprio secret nos logs. **Nunca debugue colando secret no chat** — se aparecer nos logs públicos, considere comprometido e gere outro.
3. **DATABASE_URL em formato `postgres://`**: Railway e Heroku injetam assim, mas SQLAlchemy 2.0 quer `postgresql://`. Conversão automática em `app/config.py` (`_fix_postgres_url`).
4. **Modelo escolhido sem suporte a imagem**: DeepSeek, Grok 4 fast e Grok 4.1 fast são text-only. O sistema detecta e manda só texto; se o PDF for escaneado e o modelo for text-only, retorna erro claro ao usuário.
5. **Shell buffering**: o container usa `entrypoint.py` (Python com flush confiável), não shell. `entrypoint.sh` do passado causava logs truncados/invisíveis em `dash`.

---

## Variáveis de ambiente

Ver `.env.example` para o template completo.

| Variável | Obrigatória | Default | Descrição |
|---|:-:|---|---|
| `ENV` | — | `production` | `development` ou `production`. Controla `/docs`, cookies `secure`, HSTS |
| `LOG_LEVEL` | — | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `PORT` | — | `8000` | Injetado pelo Railway automaticamente |
| `DATABASE_URL` | — | SQLite local | Railway injeta ao adicionar Postgres. Aceita `postgres://` (convertido) |
| `ACCESS_PASSWORD` | ✅ | — | Senha única do painel. **Mínimo 16 caracteres** |
| `SESSION_SECRET` | ✅ | — | Chave HMAC dos cookies. **Mínimo 32 caracteres** aleatórios |
| `OPENROUTER_API_KEY` | ✅ | — | Chave OpenRouter. Use dummy pra smoke tests |
| `OPENROUTER_MODEL_POTENTE` | — | `x-ai/grok-4` | Default para cadastro assistido (Vision) |
| `OPENROUTER_MODEL_BARATO` | — | `x-ai/grok-4-fast` | Default para fallback IA |
| `MAX_UPLOAD_SIZE_MB` | — | `20` | Limite de tamanho por arquivo |
| `DEFAULT_WEBHOOK_URL` | — | vazio | URL default para webhook; se setado, checkbox aparece na UI |
| `ALLOWED_ORIGINS` | — | vazio | Domínios CORS separados por vírgula. Vazio = mesmo-origem |
| `SCORE_CONFORMIDADE_MIN` | — | `0.85` | Score ≥ disso → status `sucesso` |
| `SCORE_CONFORMIDADE_ALERTA` | — | `0.70` | Score < mínimo mas ≥ alerta → `sucesso_com_aviso` |
| `TAXA_SUCESSO_MIN_ESQUELETO` | — | `0.70` | Abaixo disso, esqueleto é marcado `em_revisao` |

Settings são validadas com Pydantic no startup — **falha cedo** com mensagem clara se algo estiver inválido.

---

## Arquitetura de pastas

```
folhaPontoV2/
├── main.py                       # Entrypoint FastAPI (middlewares, routers, exception handlers)
├── entrypoint.py                 # Chamado pelo CMD do Docker; roda alembic + uvicorn
├── Dockerfile                    # Base python:3.11-slim + tesseract-ocr-por + poppler
├── docker-compose.yml            # Dev: Postgres 16 + app
├── railway.toml                  # Só healthcheck + restart policy
├── alembic.ini
├── migrations/
│   ├── env.py                    # Lê DATABASE_URL das settings, não do .ini
│   └── versions/
│       └── 0001_initial_schema.py
│
├── app/
│   ├── config.py                 # Pydantic Settings + catálogos de modelos
│   ├── database.py               # Engine + SessionLocal + Base
│   ├── deps.py                   # get_db, require_auth, session_id_short
│   │
│   ├── models/                   # ORM SQLAlchemy
│   │   ├── empresa.py            # Empresa + EmpresaCNPJ (1:N)
│   │   ├── esqueleto.py          # Esqueleto (estrutura JSON + métricas)
│   │   ├── processamento.py      # Histórico de cada extração
│   │   └── enums.py              # StatusEsqueleto, StatusProcessamento, MetodoExtracao
│   │
│   ├── schemas/                  # DTOs Pydantic (request/response)
│   │   ├── auth.py
│   │   ├── empresa.py
│   │   ├── esqueleto.py          # Shape interno da estrutura (para validação)
│   │   ├── extract.py
│   │   └── history.py
│   │
│   ├── services/                 # Lógica de negócio, não tocam HTTP
│   │   ├── fingerprint.py        # Assinatura estrutural do layout (whitelist + dim + cols)
│   │   ├── identificacao.py      # Extração de CNPJ + matching contra base
│   │   ├── classificador.py      # "Isso é cartão de ponto?" (heurística por tokens)
│   │   ├── cadastro_assistido.py # IA Vision propõe estrutura
│   │   ├── extracao_esqueleto.py # Aplica esqueleto (plumber/ocr/ia_barata + cascata)
│   │   ├── conformidade.py       # Score de qualidade + detecção de drift
│   │   ├── llm.py                # Cliente OpenRouter + LLMImageUnsupportedError
│   │   ├── webhook.py            # POST com retries e HMAC
│   │   ├── storage.py            # TTL store em memória (PDFs, propostas, metadata)
│   │   └── sweeper.py            # Limpa processamentos órfãos
│   │
│   ├── routes/                   # Endpoints HTTP
│   │   ├── health.py
│   │   ├── auth.py
│   │   ├── extract.py            # Upload + polling + cadastro + API externa
│   │   ├── empresas.py
│   │   ├── esqueletos.py
│   │   └── history.py
│   │
│   ├── tasks/
│   │   └── processamento.py      # BackgroundTask: pipeline completo + webhook
│   │
│   └── utils/
│       ├── pdf.py                # Validação, abrir_pdf, extrair_texto_todo, parece_escaneado
│       ├── ocr.py                # Tesseract + reconstrução de tabela por bbox
│       ├── security.py           # Cookie HMAC + hmac.compare_digest
│       ├── rate_limit.py         # Memória local; login e upload
│       └── errors.py             # Hierarquia de PontoExtractError
│
├── static/                       # Frontend sem build step
│   ├── index.html                # Dashboard (upload múltiplo + fila)
│   ├── login.html
│   ├── cadastro-assistido.html   # Alpine + PDF via iframe
│   ├── empresas.html             # Listagem
│   ├── empresa-detalhe.html      # Edição de CNPJs + esqueletos
│   ├── historico.html
│   └── js/
│       ├── common.js             # apiJson, toast, ensureAuthed, poll
│       ├── app.js                # Dashboard + fila + sessionStorage
│       ├── cadastro.js           # Alpine data function
│       ├── empresas.js
│       ├── empresa-detalhe.js
│       └── historico.js
│
└── tests/
    ├── conftest.py               # env vars de teste + fixture db_session
    ├── fixtures/pdfs/            # PDFs sintéticos (synthetic_*.pdf permitido no git)
    ├── test_auth.py
    ├── test_cnpj.py
    ├── test_conformidade.py
    ├── test_extracao_esqueleto.py
    ├── test_fingerprint.py
    ├── test_identificacao_db.py
    ├── test_storage.py
    └── test_webhook.py
```

---

## Fluxos principais

### Fluxo rápido (empresa já cadastrada) — ~100ms, custo zero

```
[usuário] POST /api/extract com PDF
                │
                ▼
         Validação (magic bytes, tamanho)
                │
                ▼
         Criação de Processamento(status=em_processamento)
         Storage: PDF em memória (TTL 1h)
                │
                ▼
         Response imediato: {processing_id, status}
                │                                     ← frontend começa polling
                ▼ BackgroundTask
         parece_cartao_de_ponto(pdf) ? Senão → nao_cartao_ponto
                │
                ▼
         identificar_empresa(pdf)     # CNPJ + fingerprint
                │
         ┌──────┴────────────────────────┐
         │                               │
    ident.esqueleto                 ident.esqueleto
       é None                        NÃO é None
         │                               │
         ▼                               ▼
    fluxo cadastro                 aplicar_esqueleto
    (ver abaixo)                        │
                                        ▼
                                calcular_score
                                        │
                                        ▼
                                Processamento.status = sucesso | sucesso_com_aviso
                                resultado_json preenchido
                                        │
                                        ▼
                                _disparar_webhook_se_configurado
```

### Fluxo de cadastro assistido (empresa nova)

```
BackgroundTask detecta ident.esqueleto == None
         │
         ▼
    gerar_proposta(pdf, modelo=modelo_potente_escolhido)
    # Rasteriza 1a página, envia imagem + texto pro modelo Vision
    # Modelo text-only: só texto
    # Modelo que rejeita imagem: fallback retry só texto
         │
         ▼
    storage.put_proposta({proposta, fingerprint, empresa_candidata})
    Processamento.status = aguardando_cadastro
         │
         ▼
    [frontend] polling vê status "aguardando_cadastro"
              redireciona para /cadastro-assistido.html?id=X

[usuário] abre tela → GET /api/extract/{id}/cadastro-proposta
         │
         ▼
    PDF preview (iframe) + formulário com proposta
    Usuário edita: nome empresa, CNPJs, modelo_fallback, estrutura (JSON)
         │
         ▼
    [usuário] POST /api/extract/{id}/cadastro-confirmar
         │
         ▼
    Cria Empresa + CNPJs (ou usa existente se empresa_id veio no payload)
    Próxima versão de esqueleto (desativa anterior se ativa)
    Salva estrutura + exemplos_validados (snippet do PDF + amostra.linha[0])
         │
         ▼
    aplicar_esqueleto(esqueleto_novo) ← valida que funciona já no PDF atual
         │
         ▼
    Retorna resultado_json completo
    Frontend grava em sessionStorage + location.href = '/'
    Dashboard lê sessionStorage e mostra o resultado
```

### Cascata de fallback dentro de aplicar_esqueleto

Implementada em `app/services/extracao_esqueleto.py`.

```
método preferencial (plumber_direto / ocr_guiado / ia_barata_com_exemplos)
         │
         ▼
    _diagnostica_extracao → None | "zero_linhas" | "colunas_tipadas_todas_vazias" | "maioria_linhas_com_1_celula"
         │
    Se diagnóstico != None:
         │
         ├─ Se era plumber_direto E PDF parece escaneado → OCR guiado (preserva cabeçalho)
         │        │
         │        ▼
         │    _diagnostica_extracao novamente
         │        │
         ├─ Se ainda ruim → IA barata com exemplos (reusa texto do OCR se já rodou)
                  │
                  ▼
              IA traz mais linhas que o anterior? → usa a IA
              IA traz menos? → mantém o anterior (evita regressão)
              IA traz cabeçalho vazio? → preserva cabeçalho do método anterior
```

---

## Endpoints da API

Todos os `/api/*` exigem cookie de sessão, exceto `/api/health` e `/api/auth/*`.

Ver Swagger em `/docs` quando `ENV=development`.

### Auth
- `POST /api/auth/login` — `{password}` → seta cookie `pontoextract_session`
- `POST /api/auth/logout`
- `GET /api/auth/me` — `{authenticated: bool}`

### Extração
- `POST /api/extract` — multipart form: `file`, opcional `modelo_potente`, `enviar_webhook`, `id_processo`, `id_documento`. Retorna `{processing_id, status}`
- `GET /api/extract/{id}/status` — polling do processamento
- `GET /api/extract/{id}/pdf` — serve o PDF (para preview no navegador)
- `GET /api/extract/{id}/cadastro-proposta` — só quando status `aguardando_cadastro`
- `POST /api/extract/{id}/cadastro-confirmar` — body: `{nome_empresa, cnpjs, estrutura, exemplos_validados, empresa_id?}`
- `POST /api/extract/{id}/cadastro-cancelar`
- `GET /api/extract/modelos-disponiveis` — catálogos de modelos + `webhook_disponivel`
- `POST /api/extract-api` — **integração externa**: multipart com `webhook_url` obrigatório (ou usa `DEFAULT_WEBHOOK_URL`). Resultado vai por webhook quando pronto

### Empresas
- `GET /api/empresas?q=...` — lista com busca por nome
- `GET /api/empresas/{id}` — detalhes + esqueletos
- `PATCH /api/empresas/{id}` — `{nome?, cnpjs_adicionar?, cnpjs_remover?}`

### Esqueletos
- `GET /api/esqueletos/{id}` — estrutura + métricas
- `PATCH /api/esqueletos/{id}` — `{estrutura?, exemplos_validados?}`
- `POST /api/esqueletos/{id}/desativar`
- `POST /api/esqueletos/{id}/reativar` — desativa outras ativas da mesma empresa

### Histórico
- `GET /api/history?limit=&offset=&empresa_id=&status=&data_inicio=&data_fim=` — paginação e filtros; inclui `pode_retomar` para items em `aguardando_cadastro` cujo PDF ainda está no storage
- `GET /api/history/{id}` — inclui `resultado_json` completo (com `score_breakdown`)
- `DELETE /api/history/{id}` — LGPD

---

## Métodos de extração

| Método | Quando usar | Custo | Requisitos |
|---|---|---|---|
| `plumber_direto` | PDFs digitais bem estruturados (padrão) | ~zero | Tabela detectável pelo pdfplumber |
| `ocr_guiado` | PDFs escaneados sem texto digital | baixo (CPU) | Tesseract instalado (no container já está) |
| `ia_barata_com_exemplos` | Fallback quando os outros falham, ou layouts muito irregulares | baixo ($) | Modelo barato no `modelo_fallback` do esqueleto |

### Critérios automáticos de fallback (`_diagnostica_extracao`)

O sistema aciona fallback se pelo menos **um** destes sinais aparecer:

- **0 linhas** extraídas
- **Todas as células de colunas hora/data estão vazias** — plumber provavelmente quebrou a detecção de tabela
- **>70% das linhas têm só 1 célula preenchida** (é ruído, não tabela)

### Modelos de IA

**Potentes (visão)** — usados no cadastro assistido, escolhidos pelo usuário no dropdown da tela de upload:

| Modelo | Suporta imagem? |
|---|:-:|
| `anthropic/claude-opus-4.7` | ✅ |
| `anthropic/claude-sonnet-4.6` | ✅ |
| `openai/gpt-5.4` | ✅ |
| `openai/gpt-5.4-mini` | ✅ |
| `google/gemini-3-flash-preview` | ✅ |
| `deepseek/deepseek-v4-pro` | — |
| `x-ai/grok-4.1-fast` | — |
| `x-ai/grok-4-fast` | — |

**Baratos (fallback)** — usados quando plumber/OCR não dão conta. Escolhidos pelo usuário na tela de cadastro (fica salvo em `estrutura.modelo_fallback` do esqueleto):

- `x-ai/grok-4.1-fast`
- `x-ai/grok-4-fast`
- `deepseek/deepseek-v4-pro`
- `google/gemini-3-flash-preview`

Catálogos em `app/config.py` (`modelos_potentes_catalogo`, `modelos_baratos_catalogo`). Para adicionar modelo novo, ver o CONTRIBUTING.

---

## Frontend

Servido como arquivos estáticos por FastAPI (`app.mount("/", StaticFiles(...))`). Nenhum build step — edita o arquivo e atualiza ao recarregar.

**Páginas:**

- `/login.html` — login vanilla
- `/` (`index.html`) — dashboard: upload drag-and-drop múltiplo, dropdown de modelo, checkbox de webhook, fila de processamento em cards com polling independente
- `/cadastro-assistido.html?id=X` — Alpine component com PDF à esquerda (iframe) e formulário à direita (nome empresa, CNPJs, dropdown de modelo fallback, JSON da estrutura editável com validação live, botões de confirmar/cancelar)
- `/empresas.html` — lista
- `/empresa-detalhe.html?id=X` — renomear empresa, adicionar/remover CNPJs, listar esqueletos com ações (ver/editar/desativar/reativar)
- `/historico.html` — lista com filtros, modal de detalhes, botão apagar (LGPD)

**`common.js`** expõe `window.App`:

- `App.api(path, opts)` — fetch que redireciona para login em 401
- `App.apiJson(path, opts)` — retorna JSON, lança Error em 4xx/5xx com status anexado
- `App.apiPostJson(path, payload)`
- `App.poll(urlFn, {intervalMs, maxMs, shouldStop})` — polling com backoff
- `App.toast(msg, tipo, ms)` — `info|success|warn|error`
- `App.ensureAuthed()` — redireciona se não autenticado
- `App.logout()`

**Estado de fila no dashboard** persiste em `sessionStorage` com a chave `fila_processamentos` — sobrevive a F5 e ao retorno da tela de cadastro.

---

## Banco de dados e migrations

**Schema** (tabelas principais):

- `empresas` (UUID, nome, timestamps, criada_por)
- `empresa_cnpjs` (FK empresas, cnpj UNIQUE — dígitos-only)
- `esqueletos` (FK empresas, versão, status, fingerprint, `estrutura` JSON, `exemplos_validados` JSON, taxa_sucesso, total_extracoes)
- `processamentos` (FK empresas/esqueletos nullable, id_processo, id_documento, nome_arquivo, metodo_usado, score, status, `resultado_json` JSON, webhook_enviado, criado_em)

**UUIDs** são gerados client-side (Python `uuid.uuid4()`) — portável SQLite/Postgres.

**Timestamps** com `DateTime(timezone=True)`.

### Rodar migrations

- **Local (docker-compose)**: rodam automaticamente na subida do container (via `entrypoint.py`).
- **Railway**: idem — parte do start command.
- **Manual**: `alembic upgrade head` dentro do container ou venv.

### Criar nova migration

Ver CONTRIBUTING.md.

---

## Autenticação

**Senha única global**, armazenada como `ACCESS_PASSWORD`. Ao fazer login, comparação em tempo constante (`hmac.compare_digest`), e um cookie HMAC-signed de 8h é setado.

- **Cookie**: `pontoextract_session` com `httponly`, `samesite=lax`, `secure` em produção
- **Rate limit**: 5 tentativas erradas em 15 min por IP → HTTP 429. Login correto **reseta** o contador
- **Upload rate limit**: 30 uploads / 10 min por IP, separado do login

Middleware `auth_gate` em `main.py` protege todas as rotas `/api/*` exceto `/api/health` e `/api/auth/*`. Arquivos estáticos passam livremente — o JS do frontend redireciona para login ao detectar 401.

---

## Webhooks

### Via UI (dashboard)

Checkbox "Enviar resultado para o webhook configurado no servidor" aparece **apenas se** `DEFAULT_WEBHOOK_URL` está setado. Quando marcado:

- Dispara para `DEFAULT_WEBHOOK_URL` apenas em **extrações automáticas** (fluxo rápido, sucesso/aviso/falhou/não-é-cartão)
- **Não dispara** para uploads que caíram em cadastro assistido — nem após o usuário confirmar

### Via API externa (`POST /api/extract-api`)

Requer `webhook_url` no form **ou** `DEFAULT_WEBHOOK_URL` configurado. Comportamento idêntico ao fluxo da UI no resto.

### Payload

```json
{
  "processing_id": "uuid",
  "status": "sucesso",
  "id_processo": "opcional-do-cliente",
  "id_documento": "opcional-do-cliente",
  "empresa_id": "uuid|null",
  "esqueleto_id": "uuid|null",
  "metodo_usado": "esqueleto_plumber",
  "score_conformidade": 0.95,
  "resultado_json": {
    "cabecalho": {...},
    "linhas": [...],
    "avisos": [...],
    "score_breakdown": {...}
  },
  "tempo_processamento_ms": 342
}
```

### Assinatura HMAC

Header `X-PontoExtract-Signature: sha256=<hex>` com HMAC-SHA256 do body usando `SESSION_SECRET` como chave. Validação no receptor:

```python
import hmac, hashlib
expected = "sha256=" + hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()
assert hmac.compare_digest(expected, request.headers["X-PontoExtract-Signature"])
```

### Retries

- 1s → 2s → 4s (backoff exponencial)
- Retenta em 5xx e erros de rede
- **Não retenta** em 4xx (responsabilidade do receptor)

---

## LGPD e segurança

- **`.gitignore`** bloqueia `*.pdf` (exceto `tests/fixtures/pdfs/synthetic_*.pdf`). PDFs reais **nunca** entram no repo.
- **Logs** nunca contêm texto de PDFs — só IDs de processamento e metadados.
- **`DELETE /api/history/{id}`** para remoção por solicitação do titular.
- **Security headers** aplicados a todas as respostas: CSP, X-Frame-Options DENY, X-Content-Type-Options nosniff, Referrer-Policy, Permissions-Policy, HSTS em produção.
- **`/docs`, `/redoc`, `/openapi.json`** desabilitados em produção via `ENV=production`.
- **CORS** fechado por default. Só abre para domínios em `ALLOWED_ORIGINS`.

---

## Troubleshooting

### "Healthcheck failed" no deploy do Railway

1. Abra os **Deploy Logs** (não Build Logs nem Healthcheck Logs) do deploy que falhou.
2. Procure por `[entrypoint]` — os logs devem mostrar cada passo:
   ```
   [entrypoint] iniciando
   [entrypoint] PORT=8080
   [entrypoint] rodando alembic upgrade head...
   [entrypoint] alembic OK
   [entrypoint] importando main.py...
   [entrypoint] main importado (hasattr app: True)
   [entrypoint] iniciando uvicorn em 0.0.0.0:8080...
   INFO: Uvicorn running on ...
   ```
3. Se parar em algum passo, a mensagem seguinte é o erro. Causas comuns:
   - **`ValidationError: SESSION_SECRET precisa ter pelo menos 32 caracteres`** — aumente a variável
   - **`ValidationError: ACCESS_PASSWORD precisa ter pelo menos 16 caracteres`** — idem
   - **`could not translate host name "db" to address`** — `DATABASE_URL` apontando para host errado (provavelmente ficou o do docker-compose em vez do Postgres do Railway)

### "PDF expirou (TTL 1h)"

PDFs ficam em memória pelo storage com TTL de 1 hora. Se o usuário deixa a aba do cadastro aberta por mais de 1h, precisa reenviar.

### Score de extração está baixo e eu acho que deveria ser alto

Abra o histórico → botão "Detalhes" do processamento → dentro de `resultado_json` está o `score_breakdown` com cada componente (cabeçalho, tem_linhas, frac_celulas, penalidade_avisos). Isso mostra exatamente onde o score perdeu pontos.

### IA barata nunca é acionada

Antes de revisar, confirme:
1. O método principal retornou resultado ruim? Veja `resultado_json.avisos` — procure por `"_tentando_ia_barata"` ou `zero_linhas`/`colunas_tipadas_todas_vazias`
2. O modelo escolhido está na whitelist em `app/config.py`?
3. O PDF tem texto extraível (digital ou via OCR)? IA barata não usa Vision.

### Modelo X rejeita imagem

Alguns modelos na whitelist são text-only (Grok 4 fast, Grok 4.1 fast, DeepSeek v4 pro). O frontend marca como "sem visão — só PDF digital". O backend faz fallback automático para só-texto quando possível. Se o PDF for escaneado e o modelo for text-only, retorna erro claro: escolha outro modelo ou envie PDF digital.

### Processamento ficou preso em "em_processamento" para sempre

Se o container reinicia durante um BackgroundTask, o processamento fica órfão. Resolução:

1. **Automática**: o `sweeper` marca como `falhou` no próximo startup ou próxima abertura do histórico processamentos em:
   - `em_processamento` há mais de 10 min
   - `aguardando_cadastro` há mais de 1h
2. **Manual**: `DELETE /api/history/{id}`

---

## Documentos complementares

- [`CONTRIBUTING.md`](./CONTRIBUTING.md) — como contribuir: setup, testes, padrões, how-tos comuns
- [`DECISIONS.md`](./DECISIONS.md) — decisões técnicas, pendências e próximas melhorias

---

## Licença

Sem licença pública definida. Repositório privado de uso interno.
