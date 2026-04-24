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

## Histórico de atualizações

- **Fase 1 (setup):** decisões iniciais registradas acima.
