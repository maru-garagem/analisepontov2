# Decisões técnicas — PontoExtract v2

Documenta o **porquê** das escolhas — o que foi decidido, o contexto, quais alternativas foram consideradas e os trade-offs aceitos. Organizado por tema, não por ordem cronológica.

Se você só quer saber **como** o código funciona, veja o [`README.md`](./README.md). Se vai **contribuir**, veja o [`CONTRIBUTING.md`](./CONTRIBUTING.md). Este documento é pra quando você se pergunta **"por que foi feito assim?"**.

---

## Sumário

- [Contexto do produto](#contexto-do-produto)
- [Decisões de arquitetura](#decisões-de-arquitetura)
  - [Esqueletos por empresa](#1-esqueletos-por-empresa)
  - [Fingerprint como chave secundária](#2-fingerprint-como-chave-secundária-de-matching)
  - [Múltiplos fingerprints por esqueleto](#2b-múltiplos-fingerprints-por-esqueleto-defesa-em-profundidade)
  - [FastAPI sync](#3-fastapi-sync-não-async)
  - [BackgroundTasks em vez de Celery](#4-backgroundtasks-em-vez-de-celery)
  - [Storage efêmero em memória](#5-storage-efêmero-em-memória-não-redis)
  - [PostgreSQL em prod e dev](#6-postgresql-em-prod-e-dev-via-docker)
  - [Senha única global](#7-senha-única-global-não-multi-usuário)
- [Decisões de implementação](#decisões-de-implementação)
  - [OpenRouter](#8-openrouter-em-vez-do-sdk-da-openai)
  - [Catálogo dual de modelos](#9-catálogo-dual-potentes-vs-baratos)
  - [Cascata de fallback](#10-cascata-de-fallback-plumber--ocr--ia-barata)
  - [Score com breakdown exposto](#11-score-com-breakdown-exposto)
  - [Frontend sem build step](#12-frontend-sem-build-step)
  - [iframe em vez de PDF.js](#13-iframe-em-vez-de-pdfjs)
  - [Checkbox opt-in de webhook](#14-checkbox-opt-in-de-webhook-na-ui)
  - [Python entrypoint em vez de shell](#15-python-entrypoint-em-vez-de-shell)
  - [Nomes em português no domínio](#16-nomes-em-português-no-domínio)
- [Decisões que mudamos no caminho](#decisões-que-mudamos-no-caminho-lições-aprendidas)
- [Adiado para v2.1+](#adiado-para-v21)
- [Apêndice: histórico de fases](#apêndice-histórico-de-fases)

---

## Contexto do produto

**Problema:** existe uma v1 ("PontoExtract") que recebe PDFs de cartão de ponto e extrai com IA genérica. A assertividade é ruim — cada empresa tem um layout diferente, e redescobrir o layout a cada extração é caro e impreciso.

**Observação-chave:** cartões de ponto da **mesma empresa são sempre iguais** (mesmo layout, mesmo CNPJ, mesmo sistema gerador). Cartões de empresas diferentes são diferentes entre si.

**Hipótese:** se aprendermos o layout de uma empresa **uma vez** (com IA potente + validação humana) e salvarmos, podemos reextrair PDFs futuros dessa mesma empresa **sem IA**, de forma determinística e barata.

Essa hipótese é o pilar da v2. Se ela se verificar na prática (um esqueleto aprendido funciona para a maioria dos PDFs subsequentes daquela empresa), o sistema amortiza o custo de IA ao longo do tempo. Cada upload após o primeiro para uma empresa custa ~zero.

---

## Decisões de arquitetura

### 1. Esqueletos por empresa

**O que:** para cada empresa cadastrada, guardamos um JSON (o "esqueleto") que descreve como extrair dados dos PDFs dela — regex-âncora para cada campo do cabeçalho, nome e tipo de cada coluna da tabela, regras de parsing, e o método preferencial.

**Alternativa considerada:** IA em todas as extrações, sem estado. É o que a v1 fazia. Descartado porque é caro, lento e pouco assertivo — cada extração paga o custo de descobrir o layout do zero.

**Trade-offs aceitos:**
- Primeira extração de uma empresa é cara (IA Vision) — mas todas as seguintes são ~grátis.
- Exige **intervenção humana** no primeiro upload (cadastro assistido). A alternativa 100% automática é menos precisa.
- Layouts que mudam (empresa troca de sistema de ponto) precisam de novo esqueleto. Resolvido via versionamento: novo fingerprint → nova versão de esqueleto, anterior vira inativa.

**Quando reconsiderar:** se estudos de campo mostrarem que a maioria das empresas tem PDFs **muito variáveis** entre si (mês a mês), o paradigma não se sustenta.

---

### 2. Fingerprint como chave secundária de matching

**O que:** além do CNPJ, identificamos esqueletos por um hash SHA-256[:16] do layout do PDF.

**Por quê (duas razões):**

1. **Empresa com múltiplas filiais** — mesmo layout, CNPJs diferentes. Fingerprint igual → um esqueleto cobre todas.
2. **Empresa que troca de sistema de ponto** — mesmo CNPJ, layout diferente. Fingerprint novo → versão nova de esqueleto.

**Algoritmo (v2 — atual):**

1. Texto **acima da primeira tabela detectada** (cabeçalho do documento — não os dados das linhas).
2. Tokens da **WHITELIST de labels estruturais** (`entrada`, `saída`, `funcionário`, `matrícula`, etc).
3. Header da **MAIOR tabela** detectada (a tabela de dados — seu cabeçalho é o sinal de layout mais estável).
4. Dimensões da página + nº de colunas da maior tabela.
5. SHA-256[:16] sobre tudo isso, prefixado com `v2:` para permitir invalidações futuras.

**Por que mudou de v1 para v2 (lição aprendida no QA 25/04):**

A v1 punha no hash o texto da página inteira da pg1, com WHITELIST mais ampla (incluía `ano`, `feriado`, `folga`, `falta`, `extra`, `abono`...). Esses termos aparecem como **conteúdo** das linhas — ex: "Feriado: Ano novo" só aparece em janeiro. Resultado: o mesmo cartão de ponto da CENEGED tinha fingerprint diferente em jan/22 vs set/21, porque jan tem "Ano novo" e set não. Isso fazia a empresa virar esqueletos sucessivos a cada mês, e o usuário caía em cadastro assistido toda vez.

A v2 corrige cortando:
- texto à área **acima da primeira tabela** (deixa fora os dados),
- WHITELIST aos labels que só aparecem em headers (sem os termos que aparecem como dados),
- header só da **maior** tabela (não da 1ª — pdfplumber detecta tabelas em ordens diferentes em PDFs sutilmente diferentes, então "1ª" não é estável).

Validado nos 3 PDFs CENEGED do QA: jan/22, set/21 e jan/21 agora têm fingerprint **idêntico** (`2b19cb54e9742c90`), e os 3 outros PDFs do QA (Rede D'Or, Hallen, Itaú) têm fingerprints distintos.

**Quando reconsiderar:** colisão entre empresas diferentes com fingerprint igual (improvável pela whitelist + headers + dimensões). Reconsiderar também se aparecer caso onde a "maior tabela" do pdfplumber é instável — fallback seria voltar pra todos os headers ou usar critério estatístico.

---

### 2b. Múltiplos fingerprints por esqueleto (defesa em profundidade)

**O que:** cada esqueleto tem um fingerprint principal (`Esqueleto.fingerprint`) **e** uma lista de fingerprints adicionais aceitos (`Esqueleto.fingerprints`). O matching considera todos.

**Por quê:** mesmo com a v2 do fingerprint estabilizada, há cenários onde a heurística pode flutuar (versão nova do gerador de PDF da empresa, mudança sutil de margens, etc.). Em vez de criar nova versão de esqueleto a cada flutuação (e desativar a anterior — looping de cadastros), oferecemos ao operador a opção de **anexar** o novo fingerprint à versão atual.

**Como funciona na UX:** quando um PDF cai em cadastro assistido com CNPJ que já tem esqueleto ativo, a tela mostra dois caminhos:
- **Anexar layout à versão atual** (default, recomendado): salva o fingerprint na lista da v-ativa e atualiza a estrutura. Sem nova versão.
- **Criar nova versão**: comportamento antigo. Use quando o layout realmente mudou (empresa trocou de sistema).

**Trade-off aceito:** se o operador anexar errado (era layout diferente), a estrutura da versão ativa pode ficar mal calibrada para os PDFs antigos. Mitigação: a estrutura é sobrescrita com o que o operador acabou de validar visualmente — então o novo PDF extrai bem; PDFs antigos podem cair na cascata de fallback (OCR / IA barata).

**Quando reconsiderar:** se aparecerem casos em que múltiplos fingerprints anexados quebram a extração de versões antigas, considerar guardar uma estrutura por fingerprint dentro do esqueleto, ou voltar para o paradigma de "uma versão por fingerprint".

---

### 3. FastAPI sync, não async

**O que:** FastAPI rodando em modo síncrono, com `SessionLocal` do SQLAlchemy 2.0 (não `AsyncSession`).

**Por quê:** o pipeline crítico é **CPU-bound e bloqueante**:

- `pdfplumber.extract_tables()` — CPU intenso
- `pytesseract.image_to_data()` — ainda mais
- `pdf2image.convert_from_bytes()` — idem

Nada disso é async nativo. Se fôssemos async, teríamos que envolver tudo em `run_in_threadpool` — ganho zero de throughput, complexidade extra, traces mais confusos.

**Para I/O (LLM, webhook):** usamos `BackgroundTasks` do FastAPI. O request HTTP retorna imediatamente com `processing_id`; o trabalho real roda em thread do pool. Funciona bem para 1 réplica com os volumes atuais.

**Alternativa considerada:** FastAPI async + `asyncpg`. Rejeitado pela razão acima, e porque migrations Alembic async são mais chatas.

**Quando reconsiderar:** se o volume crescer a ponto de saturar o thread pool (40 threads default do uvicorn) com LLM/webhooks concorrentes. Antes disso, escala horizontal via réplicas.

---

### 4. BackgroundTasks em vez de Celery

**O que:** processamento assíncrono via `fastapi.BackgroundTasks`, que roda no mesmo processo do uvicorn.

**Trade-offs aceitos:**
- Se o container cair durante um task, o task é perdido. **Mitigação:** sweeper marca processamentos órfãos como `falhou` no próximo startup.
- Escala só vertical (uma réplica). **Mitigação:** Railway default é 1 réplica; upgrade quando for necessário.

**Alternativas rejeitadas:**
- **Celery/RQ + Redis** — dependência extra, infraestrutura extra. Para o volume atual (estimativa: dezenas a centenas de PDFs/dia), é overkill.
- **Task queue persistente em DB** (huey, dramatiq-pg) — viável, mas não justificado até ter volume que exija.

**Quando reconsiderar:**
- Processamento está demorando mais que o timeout do Railway (~5min)
- Volume > 1000 PDFs/dia
- Precisa garantir entrega em caso de crash (hoje aceitável re-uploadar)

---

### 5. Storage efêmero em memória (não Redis)

**O que:** `app/services/storage.py` implementa dict com TTL 1h e lock, mantendo bytes de PDFs, propostas de cadastro e metadados entre o momento do upload e a confirmação do cadastro / disparo do webhook.

**Por quê:** no Railway, o filesystem é efêmero mesmo — escrever em `/tmp` não sobrevive a restart. Disco persistente exige volume pago. Redis seria correto mas adiciona dependência.

**Ciclo de vida:**
- Upload → grava `pdf_bytes`, `metadata` (webhook_url, modelo_potente)
- Cadastro assistido → grava `proposta`
- Confirmação/cancelamento → limpa tudo
- Sweeper passa e limpa órfãos se algo escapar
- TTL 1h faz a varredura automática

**Trade-offs aceitos:**
- Múltiplas réplicas quebrariam (upload na A, confirmação bate na B sem o PDF). Hoje é 1 réplica, OK.
- Memória de produção não deve explodir: rate limit de upload (30/10min por IP) + MAX_UPLOAD_SIZE_MB (20).

**Quando reconsiderar:** ao escalar horizontalmente. Migrar pra Redis ou object storage (S3-compat).

---

### 6. PostgreSQL em prod e dev (via Docker)

**O que:** Postgres 16 em produção (Railway) e desenvolvimento (`docker-compose.yml`). SQLite fica como fallback "arranque rápido sem Docker", mas não é o caminho oficial.

**Por quê:** Alembic gera SQL diferente para SQLite vs Postgres em casos sutis — tipos JSON, UUID, defaults, ARRAY. Não testar contra Postgres em dev é receita para quebrar migrations em produção.

**Alternativa considerada:** desenvolver em SQLite. Já levamos susto com `sa.false()` que não existe em SQLite. Abandonada.

**Conversão `postgres://` → `postgresql://`:** Railway e Heroku injetam `DATABASE_URL` começando com `postgres://` (depreciado mas mantido), mas SQLAlchemy 2.0 espera `postgresql://`. Conversão automática no `app/config.py::_fix_postgres_url` — pegadinha famosa que causou horas de bug em muitos projetos.

---

### 7. Senha única global, não multi-usuário

**O que:** uma única `ACCESS_PASSWORD` no env para todos os operadores. Cookie HMAC-signed de 8h.

**Por quê:** pedido explícito do cliente na fase de planejamento — "senha global, single user". Simplifica auth, simplifica UI, simplifica esquema de banco.

**Trade-offs aceitos:**
- Não dá pra diferenciar ações por usuário. Campo `criado_por` nos modelos guarda os últimos 8 chars do cookie session — proxy útil só pra debug (cookies diferentes = operadores diferentes).
- Rotação de senha obriga todos a relogar.

**Quando reconsiderar:** primeira necessidade de auditoria por usuário, permissões diferenciadas (admin vs operador), ou team > 3-4 pessoas.

**Migração planejada:** `criado_por` vira FK para tabela `usuarios` quando migrar. Schema do banco já aceita string, não precisa de migration pra dados existentes.

---

## Decisões de implementação

### 8. OpenRouter em vez do SDK da OpenAI

**O que:** falamos direto com a API do OpenRouter via `httpx`, sem usar o SDK da OpenAI (apesar da API ser compatível).

**Por quê:**
- **Múltiplos provedores** em um endpoint (Anthropic, OpenAI, Google, xAI, DeepSeek). Trocar de modelo é só trocar uma string.
- **Sem SDK pesado** — `openai` puxaria `pydantic`, `typing-extensions`, etc. Como já usamos `httpx` pra webhook, reusar é natural.
- **Controle sobre retries e erros** — precisamos diferenciar "modelo rejeitou imagem" de erro genérico, o que exige inspecionar o body do 4xx. Mais fácil com httpx cru.

**Trade-offs aceitos:**
- Schemas de chamada mantidos à mão (messages, response_format). Não é grande problema porque usamos um subconjunto pequeno.

---

### 9. Catálogo dual (potentes vs baratos)

**O que:** dois catálogos em `app/config.py`:
- `modelos_potentes_catalogo` — usados no cadastro assistido, com Vision
- `modelos_baratos_catalogo` — usados no fallback IA em extrações futuras

Cada entrada tem `id` + `suporta_visao` (bool).

**Por quê duas listas:**
- Cadastro assistido costuma ser Vision (analisar imagem da 1ª página), então o modelo **precisa** ou idealmente suporta imagem.
- Fallback em extração posterior é sobre texto do PDF — modelo barato basta. A necessidade de visão é opcional.
- Usuário escolhe o modelo potente na tela de upload; escolhe o barato na tela de cadastro (fica salvo em `estrutura.modelo_fallback` do esqueleto).

**Por que `suporta_visao` explícito:** DeepSeek e Grok 4 fast são text-only. Se mandar imagem, retornam 404 "No endpoints found that support image input". O backend precisa saber pra escolher o caminho certo de antemão. Caso o provedor surpreenda (mudança na API), temos fallback via `LLMImageUnsupportedError`.

**Por que whitelist fechada:** evita que um usuário malicioso (se conseguir passar pelo auth) mande qualquer modelo, inclusive pagos por token caríssimos. A whitelist define o que a organização topa pagar.

**Adicionar modelo novo:** 1 linha no catálogo. Ver CONTRIBUTING.md.

---

### 10. Cascata de fallback: plumber → OCR → IA barata → completar_data

**O que:** em `app/services/extracao_esqueleto.py::aplicar_esqueleto`, três tentativas gatilhadas pela qualidade da anterior, mais uma fase de pós-processamento:

1. Método preferencial do esqueleto
2. OCR guiado — se plumber deu ruim **E** (PDF parece escaneado **OU** o diagnóstico é `linhas_em_celula_unica` / `colunas_tipadas_todas_vazias`)
3. IA barata — se ainda está ruim
4. Pós-processamento: `parsing.completar_data_do_periodo` (se configurado)

**Por que automático:** PDFs são imprevisíveis. Mesmo um layout conhecido pode vir ruim hoje (compressão diferente, escaneado por acidente, etc.). Tentar só o método declarado e falhar é ruim — tentar os outros antes de desistir é o certo.

**Como decide "está ruim"** (`_diagnostica_extracao`):

- `zero_linhas`: óbvio.
- `colunas_tipadas_todas_vazias`: plumber achou a tabela mas os valores de hora/data vieram todos vazios → sinal clássico de que a tabela foi detectada errado.
- `linhas_em_celula_unica` (novo): tabela declara ≥3 colunas mas TODAS as linhas extraídas têm 0–1 célula significativa → plumber colapsou a tabela inteira numa coluna só (caso clássico do espelho de ponto Itaú e da Rede D'Or, onde a tabela é posicional sem grade visível).
- `maioria_linhas_com_1_celula`: >70% das linhas com só 1 célula preenchida e pelo menos 3 linhas → ruído, não tabela.

**Por que OCR mesmo em PDF digital (mudança 25/04):** antes, o fallback OCR só rodava se `parece_pdf_escaneado` retornasse True. Mas PDFs digitais com tabela posicional sem grade (Itaú, Rede D'Or) também precisam de OCR — o pdfplumber lê o texto digital mas não consegue agrupar em colunas. Hoje OCR também é tentado quando `linhas_em_celula_unica` ou `colunas_tipadas_todas_vazias`.

**Anti-regressão na cascata:** se OCR ou IA traz menos/igual linhas que o método anterior, mantemos o anterior e registramos no aviso. Evita aceitar degradação.

**Preservação de cabeçalho:** se um método novo vier com cabeçalho vazio mas o anterior tinha cabeçalho populado, mantemos o anterior. Cabeçalho extraído por regex no plumber tipicamente é mais preciso que pelo OCR.

**Pós-processamento `completar_data_do_periodo` (novo):** alguns layouts (caso clássico Hallen) listam só o DIA na linha (`21, 22, 23...`) e o período completo no cabeçalho (`Período: 21/12/2015 - 20/01/2016`). A regra do esqueleto extrai os 6 grupos do período via regex, e para cada linha decide o mês/ano correto pelo dia (`>=dia_inicio` → bloco inicial; senão, bloco fim). Saída: data completa em `coluna_destino`. Configurável no JSON do esqueleto, com UI dedicada na tela de cadastro (3 inputs nomeados em vez de JSON cru).

---

### 11. Score com breakdown exposto

**O que:** `app/services/conformidade.py::calcular_score_detalhado` retorna um dataclass com cada componente + penalidade + score final. Vai pro `resultado_json.score_breakdown` no banco e é renderizado no modal de detalhes do histórico.

**Por quê:** sem breakdown, quando um score parece estranho, ninguém sabe onde perdeu ponto. Com breakdown, em 2 segundos você vê `frac_celulas: 0.0, tem_colunas_tipadas: true` e sabe que as células hora/data falharam no parse.

**Pesos atuais (depois de 2 rebalanceamentos):**

- **40%** `tem_linhas` (binário 0/1) — sinal mais forte e inequívoco
- **30%** `frac_cabecalho` (fração)
- **30%** `frac_celulas` (fração de células hora/data bem parseadas) — **1.0 se o esqueleto não declara colunas tipadas**

Mais penalidade: 0.02 por aviso, capada em 0.10.

**Evolução:** no início era 30/30/40. Um usuário reportou uma extração que visualmente estava perfeita mas deu score 45%. Diagnóstico: formato "08h30" (hora com H) não passava no regex validador do score, todas as células hora eram marcadas inválidas, 40% do score virava 0. Fix: rebalancear para 40/30/30 + tornar validador permissivo (aceita `HH:MM`, `HH:MM:SS`, `HHhMM`, `DD/MM`, `DD/MM/YYYY`, `DD-MM-YYYY`).

**Quando ajustar pesos novamente:** se um padrão de score "fora da realidade" aparecer em produção. O breakdown no banco vira histórico útil — dá pra fazer um relatório mostrando correlação entre componentes e aceitação humana das extrações.

---

### 12. Frontend sem build step

**O que:** HTML + CSS (Tailwind CDN) + JS vanilla. Alpine.js via CDN só na tela de cadastro assistido.

**Por quê:**
- Tailwind JIT no CDN é bom o suficiente pra um painel interno
- Sem Node.js, sem npm, sem bundler, sem `dist/` pra manter, sem CI de build
- Dev loop: edita arquivo → F5 no browser

**Alternativas rejeitadas:**
- React / Vue / Svelte + Vite — overkill para painel com 5 telas. Também puxaria TypeScript e uma roda de deps por tela.
- Server-rendered templates (Jinja) — menos interativo. A tela de cadastro tem estado complexo (CNPJs dinâmicos, JSON editável com validação live); em Jinja viraria spaghetti de data attributes + JS.

**Alpine só no cadastro:** é a única tela com reatividade complexa. Nas outras, os cliques disparam actions diretas. Vanilla bastou.

**Trade-offs aceitos:**
- Tailwind CDN puxa ~50kb toda vez — aceitável num painel interno com cache.
- Sem type checking — a disciplina tem que vir dos dev. JSDoc se virar necessário.

---

### 13. iframe em vez de PDF.js

**O que:** no cadastro assistido, o preview do PDF é um `<iframe>` apontando para uma blob URL gerada do fetch.

**Por que não PDF.js (a escolha original):** tentamos com PDF.js via CDN e ESM. Conseguimos fazer funcionar, mas:

- CSP precisa liberar `script-src`, `worker-src` e `connect-src` para o CDN
- O worker carrega de outro endpoint e é bloqueado silenciosamente por CSP padrão
- Import ESM via `<script type="module">` tem timing (o `window.pdfjsLib` demora a aparecer)
- Quando qualquer um desses falha, o canvas fica vazio sem feedback ao usuário

Frustrante de debugar e frágil.

**Solução:** iframe com blob URL. O navegador usa o viewer nativo (zoom, paginação, busca — tudo grátis). CSP: `frame-src 'self' blob:`. Zero dependência externa pra isso.

**Trade-off aceito:** overlays coloridos sobre o PDF (caixinhas destacando campos) ficam inviáveis com iframe. Já estavam adiados para v2.1 mesmo.

---

### 14. Checkbox opt-in de webhook na UI

**O que:** na tela de upload, aparece um checkbox "Enviar resultado para o webhook configurado" — **só aparece** se `DEFAULT_WEBHOOK_URL` estiver configurado no servidor.

**Por quê opt-in:** nem todo upload pela UI deve ir pro webhook. O operador pode querer extrair só para ver o resultado na tela.

**Por que não campo livre de URL na UI:** webhook secret é global (`SESSION_SECRET`). Permitir URL arbitrária abriria caminho para usar o HMAC signing do sistema em endpoints externos não autorizados. Opt-in em uma URL pré-configurada é a opção segura.

**Regra extra:** cadastro assistido **não dispara webhook**, nem o upload inicial, nem após a confirmação do cadastro. Justificativa: cadastro é "trabalho humano em progresso", não um evento para publicar. Se o checkbox estiver marcado e o PDF cair em cadastro, a URL é **removida do storage** para não disparar mesmo se o user confirmar depois.

**Payload (atualizado em 25/04):** inclui `nome_arquivo` (nome original do PDF) e `criado_em` (timestamp ISO 8601). Antes só carregava `processing_id`, `id_processo`, `id_documento` — operadores reportaram dificuldade pra correlar "qual PDF gerou este resultado". Adicionar `nome_arquivo` é trivial e cobre o caso. `criado_em` ajuda em integrações que organizam por janelas temporais. Ver README — Webhooks.

---

### 15. Python entrypoint em vez de shell

**O que:** `entrypoint.py` (não `entrypoint.sh`) é o `CMD` do Dockerfile.

**Por quê:** o `/bin/sh` no `debian-slim` é o `dash`, que faz full buffering de stdout quando não é TTY. Isso significa que `echo "[entrypoint] ..."` ficava preso no buffer até o processo terminar — logs não apareciam em tempo real, dificultando debug de subida.

Python, com `print(flush=True)` ou `python -u`, flushea de forma confiável. Bonus: se o import do `main` quebrar, temos traceback Python completo em vez de "comando falhou silenciosamente".

**Arquivo:** `entrypoint.py`:
1. Imprime env vars relevantes (sem valores sensíveis)
2. Roda `alembic upgrade head` via subprocess
3. Verifica que `import main` funciona
4. `os.execvp` para `python -m uvicorn main:app` (uvicorn vira PID 1 e recebe sinais)

---

### 16. Nomes em português no domínio

**O que:** modelos, métodos de serviço, campos do esqueleto: **português**. Infra HTTP, libs, tipos: **inglês**.

Exemplos:
- Tabelas: `empresas`, `esqueletos`, `processamentos`
- Modelo: `Esqueleto.versao`, `Esqueleto.estrutura`
- Enum: `StatusProcessamento.AGUARDANDO_CADASTRO`
- Service: `aplicar_esqueleto`, `gerar_proposta`, `identificar_empresa`
- Infra: `main.py`, `SessionLocal`, `get_db`, `BackgroundTasks`

**Por quê:** domínio é trabalhista brasileiro, stakeholders (operadores, clientes) falam português. "Esqueleto" em inglês seria "skeleton" — ok, mas "empresa" vira "company"? "Processamento" vira "processing"? O mapping já seria cognitivamente pesado.

**Por quê inglês na infra:** `get_database_session` em vez de `obter_sessao_banco` quebra padrões de bibliotecas Python (SQLAlchemy, FastAPI) e torna busca/documentação mais difícil.

Essa divisão é consistente no código. Ao criar coisas novas, siga.

---

## Decisões que mudamos no caminho (lições aprendidas)

Boas engenharias documentam onde erraram. Aqui está o que foi revisado.

### Fingerprint v1 → v2 (QA 25/04)

**Era assim:** SHA do texto da página inteira da pg1, com WHITELIST larga (`ano`, `feriado`, `folga`, `falta`, `extra`, `abono`, `dia`, etc), incluindo qualquer label estrutural que aparecesse no PDF.

**Problema descoberto no QA:** o mesmo cartão de ponto da CENEGED em meses diferentes gerava fingerprints diferentes. Causa: o token `ano` estava na whitelist e aparecia em "Feriado: Ano novo" — só presente em PDFs de janeiro. Mudava o set de tokens, mudava o hash. Cada upload mensal disparava cadastro assistido novo, criando v+1 e desativando a anterior. Quando o usuário subia de novo o primeiro PDF, ele não batia mais (esqueleto v1 estava INATIVO) e pedia cadastro pela 3ª vez.

**Fix em 3 frentes:**

1. **Texto restrito ao topo da página** (`_texto_acima_da_primeira_tabela`): só o cabeçalho do documento entra no hash, os dados das linhas ficam de fora.
2. **WHITELIST podada**: removidos termos que aparecem como conteúdo (`ano`, `feriado(s)`, `folga(s)`, `falta(s)`, `extra(s)`, `abono(s)`, `atestado(s)`, `desconto(s)`, `dia`, `dias`, `mes`, `mês`, `semana`, `data`, `adicional`).
3. **Header só da MAIOR tabela** (e não da 1ª): a "1ª tabela" não é estável entre PDFs de mesmo layout (pdfplumber detecta na ordem com pequenas variações). A maior é o cartão real, sempre o mesmo.
4. **Versionamento explícito**: o canonical agora começa com `v2:`. Bumps futuros invalidam fingerprints antigos sem migração.
5. **Múltiplos fingerprints por esqueleto**: defesa em profundidade — quando um PDF cai em cadastro com fingerprint diferente mas o operador valida que é o mesmo layout, anexa em vez de versionar (ver decisão 2b).

**Validação:** os 3 PDFs CENEGED do QA (jan/22, set/21, jan/21) agora têm fingerprint idêntico. Os outros 3 PDFs (Rede D'Or, Hallen, Itaú) têm fingerprints distintos sem colisão.

**Lição:** fingerprint estrutural só serve se for derivado SÓ de estrutura. Misturar conteúdo no hash é convidar instabilidade. Quando viu "tokens diferentes mês a mês", olhar com lupa para POR QUE estão diferentes — a resposta tipicamente revela uma fronteira mal posta entre estrutura e dado.

---

### `railway.toml startCommand` vs `Dockerfile CMD`

**Era assim:** `railway.toml` tinha `startCommand = "alembic upgrade head && uvicorn..."` e o `Dockerfile` tinha `CMD` equivalente.

**Problema descoberto no deploy:** Railway prioriza `startCommand` do `.toml` sobre `CMD` do Dockerfile. O `entrypoint.py` adicionado como CMD nunca era chamado. Deploys crasheavam silenciosamente porque os logs estavam no `entrypoint.py` — que nunca rodava.

**Fix:** removido `startCommand` do `.toml`. O Dockerfile é fonte única da verdade.

**Lição:** não repetir configuração em múltiplos lugares. Se o Dockerfile define o comando, não defina de novo no `.toml`. Se um override for realmente necessário, seja explícito sobre **um** ser prioritário.

---

### Limite de páginas de 50 → sem limite

**Era assim:** `MAX_PAGES_DEFAULT = 50` em `app/utils/pdf.py`. PDFs maiores eram rejeitados com erro.

**Problema:** cartões de ponto de empresas médias e grandes têm facilmente 100+ páginas (1 por funcionário). Limite arbitrário cortava uso legítimo.

**Fix:** `validar_pdf_bytes(max_pages=None)` — default sem limite. Parâmetro ainda existe pra chamadores que queiram restringir.

**Lição:** limites arbitrários protegem contra casos-limite, mas se não forem baseados em dados reais de uso, viram obstáculos. Se precisar de limite, usar env var (`MAX_UPLOAD_SIZE_MB` já existe e protege memória, que é o risco real).

---

### Score 30/30/40 → 40/30/30 + validação permissiva

**Era assim:** 30% cabeçalho, 30% linhas, 40% células tipadas. Validador de hora só aceitava `HH:MM`.

**Problema:** usuário reportou score 45% em extração visualmente perfeita. Debug revelou:
- PDF tinha horas em formato `08h30`
- `_parse_hora` do extrator caía no fallback "mantém texto original"
- Validador do score rejeitava `08h30`, marcava células como inválidas
- `frac_celulas = 0`, 40% do score sumia

**Fix triplo:**
1. Rebalancear: 40/30/30. Linhas é sinal mais forte e binário — merece peso maior.
2. Tornar validador permissivo: `HH:MM`, `HH:MM:SS`, `HHhMM`, `DD/MM`, datas com traço, etc.
3. Expor `score_breakdown` no `resultado_json` — pra próxima surpresa ser debugável.

**Lição:** quando um número parece errado, dê visibilidade antes de assumir que está certo. Breakdown exposto > "confie no algoritmo".

---

### IA barata exigia `exemplos_validados != []`

**Era assim:** `if not linhas and exemplos:` — só acionava com zero linhas e exemplos não-vazios.

**Problemas:**
1. "Zero linhas" era ingênuo. Plumber podia extrair 2 de 30 linhas — nunca caía no fallback.
2. Os exemplos eram salvos pela UI com `trecho_pdf: ''` (inútil pra few-shot).
3. Se o esqueleto fosse salvo sem exemplos (caso comum), fallback nunca rodava.

**Fix em 4 frentes:**
1. `_diagnostica_extracao` detecta extração ruim por 3 critérios (zero linhas, todas colunas hora/data vazias, maioria de linhas com 1 célula).
2. Removida a dependência de exemplos — IA barata funciona só com a estrutura declarada.
3. `cadastro-confirmar` agora preenche `trecho_pdf` com snippet real do PDF se o frontend mandar vazio.
4. Prompt reescrito: lista explicitamente os campos esperados, tipos de coluna, regras de formatação, forma exata do JSON de saída. Modelo não adivinha mais.

**Lição:** "heurística de acionamento" precisa ser auditável e permissiva. "Travar" um fallback em condições muito específicas é armadilha — o fallback é justamente pra casos que você não previu.

---

### PDF.js → iframe

Já documentado acima (decisão #13). Lição: dependências externas frágeis (CSP + ESM + CDN + worker) não compensam quando existe solução nativa (iframe).

---

### Rate limit de cadastro "por empresa"

**Ideia inicial (abandonada antes de implementar):** limitar cadastros assistidos por empresa para evitar abuso.

**Por que abandonamos:** cadastro assistido é **naturalmente raro** — só a primeira vez que vê uma empresa. Dos próximos milhares de PDFs daquela empresa, zero envolvem cadastro. Limitar por empresa seria complicar à toa. O rate limit de upload (30 por IP em 10 min) já protege contra loop acidental.

**Lição:** rate limits devem proteger o vetor de abuso real. Cadastro de empresa nova não é.

---

## Adiado para v2.1+

Coisas que conscientemente não entraram na v2.0. Ordem sugerida por prioridade de valor × esforço:

### Alta prioridade

1. **Dashboard de métricas** — taxa de acerto por esqueleto ao longo do tempo, custo acumulado de LLM, volume por empresa. Tudo que falta é visualização; dados já estão no banco (`processamentos.score_conformidade`, `processamentos.custo_estimado_usd`, `esqueletos.taxa_sucesso`, `esqueletos.total_extracoes`).

2. **Retenção automática** — cron que apaga processamentos mais antigos que N dias (configurável por empresa). Hoje a retenção é manual via `DELETE /api/history/{id}`. LGPD manda prever.

3. **Multi-usuário simples** — email + senha individual, sem perfis complexos. Migrar `criado_por` de string para FK `usuario_id`.

### Média prioridade

4. **UI de edição campo-a-campo da estrutura do esqueleto** — hoje é textarea JSON com validação. Um formulário guiado (um input por campo, dropdown de tipo de coluna, etc.) tornaria acessível a usuários não-técnicos.

5. **Overlays visuais no cadastro assistido** — caixinhas coloridas sobre o PDF destacando onde cada campo foi encontrado. Alta UX, implementação trabalhosa (coordenar pdf→html).

6. **Rollback de versão de esqueleto** — hoje nova versão desativa a anterior permanentemente. Poder voltar seria útil pra corrigir regressões.

7. **API key separada para integrações** — hoje o `/api/extract-api` usa o mesmo cookie da UI. Uma API key desassociada permitiria revogação independente.

8. **Exportação CSV/Excel** — download do histórico filtrado. Baixo esforço, alto valor para usuários que usam Excel.

### Baixa prioridade

9. **Rate limit distribuído** (Redis) — só faz sentido quando escalar pra múltiplas réplicas.

10. **Agrupamento de empresas matriz/filial** — se 5 empresas compartilham fingerprint, sugerir visualmente que talvez sejam a mesma.

11. **Celery/task queue persistente** — só se o volume ficar > 1000 PDFs/dia ou crashes de container começarem a ser problema real.

12. **Testes de integração com PDFs reais** — fixtures sintéticas geradas por `fpdf2`, testes end-to-end do pipeline. Hoje o pipeline é testado manualmente contra PDFs de produção.

### Possivelmente nunca

- **Async full** do FastAPI — só se todo o stack de PDF tiver equivalente async. Unlikely.
- **Frontend em framework** — só se a UI crescer para >15 telas com estado compartilhado complexo.

---

## Apêndice: histórico de fases

O projeto foi construído em 13 fases ordenadas. Este registro é mantido porque alguns nomes de arquivos e comentários referenciam "Fase X" no repo.

| Fase | Entrega |
|---|---|
| 1 | Setup base: Dockerfile, docker-compose, config.py, health check, Alembic configurado |
| 2 | Autenticação: login/logout, cookie HMAC, rate limit, middleware auth_gate |
| 3 | Models + primeira migration: Empresa, EmpresaCNPJ, Esqueleto, Processamento |
| 4 | Serviços base: utils/pdf, utils/ocr, services/llm, services/fingerprint |
| 5 | Classificador + identificação (CNPJ + matching) |
| 6 | Extração aplicando esqueleto (plumber_direto inicial) |
| 7 | Cadastro assistido backend: BackgroundTask, IA Vision, endpoints |
| 8 | Cadastro assistido frontend: PDF preview, Alpine, dashboard |
| 9 | Score de conformidade + drift detection |
| 10 | Endpoints de histórico, empresas, esqueletos |
| 11 | Webhooks + endpoint externo `/api/extract-api` |
| 12 | Security headers, exception handler central, LGPD delete |
| 13 | Testes (unit + integração com DB in-memory) + pyproject.toml |

Após a fase 13, várias iterações adicionaram:
- Sweeper de órfãos
- Endpoint de modelos disponíveis
- Dropdown de modelo potente/barato
- Upload múltiplo com fila persistente
- OCR guiado funcional (antes era stub)
- Reescrita da lógica de IA barata
- Checkbox opt-in de webhook
- Remoção do limite de páginas
- Rebalanceamento do score
- Troca PDF.js → iframe

Veja `git log --oneline` para o registro completo.

---

## Quando atualizar este documento

Toda vez que uma decisão **não óbvia** for tomada, adicione aqui. Não decisões mecânicas ("renomeei variável X"), mas decisões arquiteturais ou de produto onde alguém poderia razoavelmente ter escolhido diferente.

Formato recomendado para novas entradas:

```
### N. Título curto da decisão

**O que:** o que foi decidido, em 1-2 frases.

**Por quê:** contexto, problema, justificativa.

**Alternativas consideradas:** o que cogitamos e descartamos.

**Trade-offs aceitos:** o que abrimos mão.

**Quando reconsiderar:** sinais de que a decisão precisa ser revisitada.
```

Não deixe o documento virar changelog — para isso é o `git log`. Aqui é por que as coisas são do jeito que são, escrito para o próximo humano entender em 5 minutos, não 5 horas.
