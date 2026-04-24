# PontoExtract v2

Extração de cartões de ponto trabalhistas brasileiros via **esqueletos aprendidos por empresa**.

**Status:** v2.0 pronto para deploy — todas as 13 fases concluídas. Ver [`DECISIONS.md`](./DECISIONS.md) para o que foi e o que **não** foi testado localmente.

## Como funciona

Cada empresa tem um "esqueleto de extração" aprendido **uma única vez** (IA potente + validação humana) e reutilizado infinitamente com processamento determinístico e barato. Quando um PDF chega:

1. Sistema lê o CNPJ + gera fingerprint do layout.
2. **Empresa reconhecida** → aplica esqueleto → JSON em segundos, custo ~zero.
3. **Empresa nova** → cadastro assistido por IA → usuário valida → esqueleto salvo para o futuro.

Detalhes em [`DECISIONS.md`](./DECISIONS.md).

## Rodando localmente

Requisitos: Docker + Docker Compose.

```bash
cp .env.example .env
# Edite .env:
#   ACCESS_PASSWORD precisa ter 16+ caracteres
#   SESSION_SECRET precisa ter 32+ caracteres
#   Gere SESSION_SECRET com:
#     python -c "import secrets; print(secrets.token_urlsafe(48))"

docker-compose up --build
```

Verificar: http://localhost:8000/api/health

Em desenvolvimento, a documentação OpenAPI fica em http://localhost:8000/docs.

### Sem Docker (modo rápido)

```bash
python -m venv venv && source venv/bin/activate  # ou venv\Scripts\activate no Windows
pip install -r requirements.txt
cp .env.example .env  # e edita
# Deixe DATABASE_URL vazio no .env para usar SQLite local (apenas para smoke tests)
alembic upgrade head
uvicorn main:app --reload
```

Requer Tesseract (com pacote `por`) e Poppler instalados no sistema.

## Deploy no Railway

1. Crie um projeto Railway apontando para este repo.
2. Adicione o serviço **PostgreSQL** — Railway injeta `DATABASE_URL` automaticamente.
3. Configure as variáveis de ambiente do painel (ver `.env.example`).
4. Push → Railway builda pelo Dockerfile e aplica migrations no start.

Health check do Railway: `/api/health`.

## Estrutura

```
app/
├── config.py         Settings com validação
├── database.py       SQLAlchemy engine + Base
├── deps.py           Dependências FastAPI (get_db, auth)
├── models/           ORM (Fase 3)
├── schemas/          Pydantic DTOs
├── services/         Lógica de negócio
├── routes/           Endpoints HTTP
├── tasks/            BackgroundTasks
└── utils/            PDF, OCR, security
static/               Frontend (HTML + Tailwind CDN + JS)
migrations/           Alembic
tests/                pytest
```

## LGPD

PDFs reais **nunca** entram no git (`.gitignore` bloqueia `*.pdf`). Logs não contêm conteúdo de PDFs — apenas IDs e metadados. Cookies de sessão em produção usam `secure`, `httponly`, `samesite=lax`.

## Rodando os testes

Dentro do container Docker (ou em um venv Python 3.11):

```bash
pip install -r requirements.txt
pytest
```

Testes cobrem: normalização e whitelist do fingerprint, validação e extração de CNPJ, parsing de células (hora/data/número), cabeçalho/linhas da tabela, score de conformidade, storage em memória, assinatura e retries de webhook, auth com TestClient, identificação com DB in-memory. O que não é testado unitariamente (pdfplumber/Tesseract/LLM real) é validado manualmente ao processar um PDF real após o deploy.

## Documentação adicional

- [`DECISIONS.md`](./DECISIONS.md) — decisões técnicas, pendências e próximas melhorias.
