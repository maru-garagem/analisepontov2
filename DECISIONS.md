# Decisões técnicas — PontoExtract v2

Documenta o **porquê** de escolhas não óbvias. Atualizado a cada fase.

---

## Stack

### FastAPI **sync** (não async)
Pipeline de PDF (pdfplumber, Tesseract, pdf2image) é CPU-bound e bloqueante. Async FastAPI forçaria `run_in_threadpool` em todo lugar sem ganho real de throughput. Chamadas I/O (LLM, webhook) rodam em `BackgroundTasks`. Simplicidade vence.

### PostgreSQL em prod **e** em dev (via docker-compose)
Migrations Alembic têm dialetos diferentes em SQLite vs Postgres (tipos JSON, UUID, defaults, array). Manter paridade evita surpresas no deploy. SQLite segue suportado como fallback "arranque sem Docker", mas o caminho oficial é Postgres.

### Conversão `postgres://` → `postgresql://`
Railway (e Heroku) injetam `DATABASE_URL` começando com `postgres://`, mas SQLAlchemy 2.0 espera `postgresql://`. Conversão é feita no `config.py` via `field_validator`.

### Frontend: vanilla JS + Alpine.js (CDN) só onde necessário
Login, histórico e empresas ficam 100% vanilla. Apenas a tela de cadastro assistido usa Alpine (reatividade sem build step). PDF.js renderiza o preview no browser, evitando round-trip para renderização no servidor.

### Background tasks + polling para cadastro assistido
IA Vision potente leva 20–60s. HTTP síncrono funcionaria, mas UX é muito melhor com progresso real. `POST /api/extract` retorna `processing_id` imediatamente; frontend faz polling em `GET /api/extract/{id}/status`. Mesma estrutura serve para o fluxo rápido (status vira `concluido` em ~1s).

---

## Identificação de empresa: CNPJ + fingerprint

A chave de matching é composta:
- **CNPJ** extraído do PDF (regex + fallback para razão social).
- **Fingerprint** do layout: SHA-256 sobre texto estável da 1ª página (sem dígitos, nomes e datas) + nº de colunas da maior tabela + tamanho de página.

Isto cobre:
- Empresa com múltiplas filiais (mesmo layout, CNPJs diferentes) → mesmo esqueleto.
- Empresa que trocou de sistema de ponto (mesmo CNPJ, fingerprint novo) → novo esqueleto versionado.

Design detalhado: **Fase 4**.

---

## Adiado para v2.1
- **Overlays visuais** (caixinhas coloridas sobre o PDF no cadastro). Mapear coordenadas PDF↔HTML é trabalhoso e valor marginal é baixo na primeira versão. V2 entrega validação textual + amostragem de 3 dias.
- **Multi-usuário com login individual.** V2 mantém senha global por pedido explícito do usuário. `criada_por` nos modelos armazena os últimos 8 chars do cookie HMAC como proxy — quando migrar para multi-user vira FK.

---

## Segurança e LGPD
- Nenhum PDF commitado: `.gitignore` bloqueia `*.pdf` exceto fixtures sintéticas nomeadas `synthetic_*.pdf`.
- Logs não incluem conteúdo de PDFs — apenas IDs e metadados.
- Cookies em produção: `secure=True`, `httponly=True`, `samesite=lax`.
- `/docs`, `/redoc` e `/openapi.json` desabilitados em produção via `ENV`.
- CORS restrito por `ALLOWED_ORIGINS` (default vazio = mesmo-origem).

---

## Fluxo assíncrono com polling

`POST /api/extract` retorna **imediatamente** um `processing_id` e dispara `BackgroundTask`. O frontend faz polling em `GET /api/extract/{id}/status` (intervalo inicial 1.5s) até atingir estado final (`sucesso`, `sucesso_com_aviso`, `aguardando_cadastro`, `nao_cartao_ponto`, `falhou`).

Estado intermediário `aguardando_cadastro` redireciona o frontend para a tela de cadastro assistido, que consome `GET /cadastro-proposta` e confirma via `POST /cadastro-confirmar`.

## Score de conformidade

Combinação ponderada:
- **30%** — fração de campos de cabeçalho preenchidos.
- **30%** — presença de linhas na tabela (0 ou 1).
- **40%** — fração de células `hora`/`data` que parseiam corretamente.
- Menos penalidade por avisos (até −0.2).

Limiares por env var: `SCORE_CONFORMIDADE_MIN` (default 0.85) marca como `sucesso`; abaixo disso vira `sucesso_com_aviso`. `TAXA_SUCESSO_MIN_ESQUELETO` (default 0.70) marca esqueleto como `em_revisao` — só a partir de 5 extrações no histórico, pra evitar flagar esqueletos recém-criados.

## Webhooks

`/api/extract-api` aceita `webhook_url` no form (ou usa `DEFAULT_WEBHOOK_URL`). Ao concluir, dispara POST com:
- Payload completo (resultado, metadata, score)
- Header `X-PontoExtract-Signature: sha256=<hmac>` onde o HMAC usa `SESSION_SECRET` como chave
- Retries com backoff 1s/2s/4s em erros 5xx ou de rede; 4xx não são retriados

## Segurança

- **Cookies de sessão**: HMAC via `itsdangerous`, TTL 8h, `httponly`, `samesite=lax`, `secure` em produção.
- **Rate limit**: 5 tentativas de login em 15 min por IP. Memória local (não distribuído).
- **Security headers** em todas as respostas: CSP (permite CDNs que usamos), X-Frame-Options DENY, X-Content-Type-Options nosniff, Referrer-Policy strict-origin-when-cross-origin, Permissions-Policy fechado, HSTS em produção.
- **Exception handler central**: `PontoExtractError` → JSON com `{detail, code}` e status HTTP apropriado.
- **LGPD**: `DELETE /api/history/{id}` para retenção; apagar Processamento não apaga Empresa/Esqueleto (metadados de layout, não dados pessoais).

## O que NÃO foi testado localmente (honesto)

A máquina de desenvolvimento tem Python 3.14, sem Docker e sem wheels de `pydantic-core` para a versão 3.14. O trabalho foi feito e validado com:
- `python -m py_compile` em todos os arquivos `.py` criados ou modificados.
- Validação estática de configs (TOML, YAML, Dockerfile, .env.example).
- Design review de cada módulo.

**Não rodei**:
- `docker-compose up` de ponta a ponta. Primeira subida real é responsabilidade do operador.
- `alembic upgrade head` contra Postgres real. Deve rodar limpo (migration manualmente testada em sintaxe), mas pode revelar algum detalhe de tipo.
- `pytest` — os testes são válidos sintaticamente e logicamente, mas não foram executados nesta máquina. Rodar `pytest` no ambiente Docker (Python 3.11) é o próximo passo.
- Chamadas reais à OpenRouter / Tesseract / pdf2image / Poppler. O pipeline está estruturalmente completo; o primeiro upload com PDF real vai confirmar se prompts, parsing e OCR funcionam como projetado.
- Frontend renderizado no browser. PDF.js via ESM e Alpine via CDN foram escolhidos por serem estáveis, mas o layout da tela de cadastro assistido merece tuning ao ver um PDF real dentro.

## Pendências para a primeira revisão pós-deploy

1. Criar um diretório `tests/fixtures/pdfs/` com 1 ou 2 PDFs **sintéticos** (gerados por fpdf2) nomeados `synthetic_*.pdf` — o `.gitignore` permite esses por exceção.
2. Rodar o fluxo completo manualmente com um PDF real (os "verde - *.pdf" que você colocou na raiz) e ajustar:
   - Qualidade da proposta do LLM potente (prompt em `services/cadastro_assistido.py`).
   - Regex padrão de CNPJ/labels para os layouts reais.
   - Presença/ausência de tabelas quando pdfplumber varia.
3. Confirmar que o custo estimado do cadastro assistido (`_custo_estimado` em `services/cadastro_assistido.py`) bate com a tabela real do OpenRouter na data.
4. Revisar se o token TTL do cookie (8h) atende a expectativa de uso do operador.

## Próximas melhorias (v2.1+)

Fora do escopo desta v2.0, mas valem planejamento:

- **Overlays visuais** (caixinhas coloridas sobre o PDF no cadastro). Alta complexidade, alto valor de UX.
- **Multi-usuário** com emails individuais e permissões (admin vs operador).
- **Rate limit distribuído** via Redis (hoje é memória local — se escalar para múltiplas réplicas, cada processo tem seu contador).
- **Versionamento de esqueletos com rollback**: hoje nova versão desativa a anterior; poderia permitir voltar.
- **UI de edição campo-a-campo da estrutura**: hoje o usuário edita JSON no textarea; formulário guiado seria mais acessível.
- **Fallback automático plumber → OCR guiado**: hoje só existe fallback para IA barata. Para PDFs escaneados sem exemplos, OCR guiado direto no esqueleto seria mais barato.
- **Dashboard de métricas**: taxa de acerto por esqueleto ao longo do tempo, custo acumulado, volume por empresa.
- **Agrupamento por "empresa matriz"**: quando várias empresas compartilham fingerprint, sugerir vinculação.
- **Retenção automática**: cron que apaga processamentos mais antigos que N dias (configurável).
- **Exportação**: download do histórico filtrado em CSV/Excel.

## Histórico de atualizações

- **Fases 1-4**: decisões iniciais da stack + identificação/fingerprint.
- **Fases 5-6**: esquema de extração via esqueleto + estrutura JSON formalizada.
- **Fases 7-8**: cadastro assistido com Vision + UI com PDF.js + Alpine.
- **Fases 9-11**: score refinado, drift, endpoints de gestão, webhooks.
- **Fase 12**: security headers, exception handler central, endpoint LGPD de deleção.
- **Fase 13**: testes (unitários puros + DB in-memory + mocks de webhook/LLM) + pyproject.toml.
