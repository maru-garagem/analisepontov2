# Contribuindo com o PontoExtract v2

Guia prático para quem precisa mexer no código. Assume familiaridade com Python, FastAPI, SQLAlchemy e Docker. Se você já leu o [README.md](./README.md), pode ir direto para a seção que interessa.

## Sumário

- [Setup de desenvolvimento](#setup-de-desenvolvimento)
- [Rodando os testes](#rodando-os-testes)
- [Padrões de código](#padrões-de-código)
- [Como funciona internamente](#como-funciona-internamente)
  - [Fingerprint](#fingerprint)
  - [Cascata de fallback](#cascata-de-fallback)
  - [Score de conformidade](#score-de-conformidade)
  - [Storage em memória](#storage-em-memória)
  - [Sweeper de órfãos](#sweeper-de-órfãos)
- [How-tos](#how-tos)
  - [Adicionar novo modelo de IA](#adicionar-novo-modelo-de-ia)
  - [Adicionar novo método de extração](#adicionar-novo-método-de-extração)
  - [Criar migration do banco](#criar-migration-do-banco)
  - [Adicionar campo ao Esqueleto](#adicionar-campo-ao-esqueleto)
  - [Debugar extração com score baixo](#debugar-extração-com-score-baixo)
  - [Editar estrutura de esqueleto existente](#editar-estrutura-de-esqueleto-existente)
  - [Adicionar novo endpoint protegido](#adicionar-novo-endpoint-protegido)
  - [Alterar regras do sweeper](#alterar-regras-do-sweeper)
- [Commits e Pull Requests](#commits-e-pull-requests)
- [Checklist antes do deploy](#checklist-antes-do-deploy)
- [Coisas que NÃO fazer](#coisas-que-não-fazer)

---

## Setup de desenvolvimento

### Primeira vez

```bash
git clone https://github.com/maru-garagem/analisepontov2.git
cd analisepontov2
cp .env.example .env
# Edite o .env — no mínimo ACCESS_PASSWORD (16+ chars) e SESSION_SECRET (32+ chars)
docker-compose up --build
```

Acesse http://localhost:8000. Login com a senha que colocou no `ACCESS_PASSWORD`.

### Loop de desenvolvimento

O `docker-compose.yml` monta o repo como volume (`.:/app`). Edições Python são refletidas ao reiniciar o container:

```bash
docker-compose restart app
```

Logs em tempo real:

```bash
docker-compose logs -f app
```

Se mudou `requirements.txt` ou Dockerfile, precisa rebuildar:

```bash
docker-compose up --build app
```

### Rodando sem Docker

Só use isso pra teste de sintaxe ou executar testes. A produção é o container.

```bash
python3.11 -m venv venv
source venv/bin/activate  # ou venv\Scripts\activate no Windows
pip install -r requirements.txt
```

Requer Tesseract (com `por`) e Poppler no sistema operacional.

### Python 3.11 é obrigatório?

Para o container: **sim**, é isso que o Dockerfile usa.

Para desenvolvimento local: **recomendado**. Versões mais novas (3.12+) podem funcionar, mas `pydantic-core` nem sempre tem wheels disponíveis — aí você precisa do toolchain Rust pra compilar. 3.11 evita esse problema.

### Acessar o Postgres local

```bash
docker-compose exec db psql -U ponto -d pontoextract
```

Ou via cliente GUI: `localhost:5432`, user `ponto`, senha `ponto_dev`, db `pontoextract`.

---

## Rodando os testes

Dentro do container:

```bash
docker-compose exec app pytest
```

Em venv local (Python 3.11):

```bash
pytest
```

Com cobertura:

```bash
pytest --cov=app --cov-report=term-missing
```

Rodar um arquivo específico:

```bash
pytest tests/test_conformidade.py -v
```

Rodar um teste específico:

```bash
pytest tests/test_conformidade.py::TestCalcularScore::test_extracao_perfeita_da_1 -v
```

### O que os testes cobrem

| Arquivo | O que testa |
|---|---|
| `test_auth.py` | Login, logout, rate limit, middleware auth_gate |
| `test_cnpj.py` | Validação DV, extração de texto, formatação |
| `test_fingerprint.py` | Normalização, whitelist, determinismo, layouts |
| `test_extracao_esqueleto.py` | Parsers puros (hora/data/número), cabeçalho, linhas |
| `test_conformidade.py` | Score e breakdown, formatos permissivos |
| `test_storage.py` | TTL, PDFs, propostas, metadata |
| `test_webhook.py` | 4xx sem retry, 5xx com backoff, HMAC |
| `test_identificacao_db.py` | Matching (cnpj+fingerprint) com DB in-memory |

### O que NÃO é testado

- pdfplumber real com PDFs reais (validação é manual)
- OCR com Tesseract (validação é manual)
- Chamadas reais à LLM (mockadas)
- Renderização no browser

Quando for tocar em `services/cadastro_assistido.py` ou `services/extracao_esqueleto.py`, teste manualmente com um PDF real após o deploy.

### conftest.py

`tests/conftest.py`:

- Define env vars de teste ANTES de qualquer import de `app/`
- Limpa `SessionLocal` e `login_limiter` entre testes automaticamente
- Oferece fixture `db_session` com SQLite in-memory + schema recriado por teste

Quando for adicionar um teste que toca DB, use `db_session`:

```python
def test_minha_coisa(db_session):
    empresa = Empresa(nome="Teste")
    db_session.add(empresa)
    db_session.commit()
    # ...
```

---

## Padrões de código

### Estilo

- **4 espaços**, não tabs
- **Type hints** em funções públicas (não precisa em helpers locais)
- **`from __future__ import annotations`** no topo dos arquivos Python (já padrão em tudo que escrevemos)
- **Docstrings** em módulos e funções não-triviais, explicando o **porquê** (o **quê** deveria ser óbvio pelo nome)
- **Logs** com `logger = logging.getLogger(__name__)`, nunca `print`

### Nomes em português

Por escolha deliberada, os nomes no domínio de negócio (modelos, serviços, variáveis dos campos do esqueleto) estão em português: `empresa`, `esqueleto`, `cabecalho`, `linhas`, `aguardando_cadastro`. Identificadores de código estrutural (classes HTTP, configs) ficam em inglês: `config.py`, `get_settings`, `BackgroundTask`.

Ao criar coisas novas, siga a mesma divisão.

### Exceções

Herdam de `PontoExtractError` (`app/utils/errors.py`). Todo erro de domínio tem `http_status` e `code`. O handler global em `main.py` converte automaticamente em JSON `{detail, code}`.

```python
from app.utils.errors import PontoExtractError

class MeuErroCustom(PontoExtractError):
    http_status = 400
    code = "meu_erro"
```

Nunca levante `HTTPException` direto em `services/` — isso acopla lógica de negócio ao HTTP. Use `PontoExtractError` e deixe a conversão pro handler.

### Dependências

- `requirements.txt` pinado exato (`==`)
- Ao adicionar, escolha a versão mais recente estável e pina
- Evite deps pesadas; o container já tem ~140MB, cada libra conta

### LGPD nos logs

**Nunca** logue:
- Conteúdo de PDFs (texto, linhas extraídas)
- Senha, `SESSION_SECRET`, `OPENROUTER_API_KEY`
- CNPJ/CPF do funcionário sendo processado (exceto em debug temporário que você remove antes de commitar)

Pode logar:
- UUIDs de processamento, empresa, esqueleto
- Status, métrica, método
- Fingerprint hash
- Códigos de erro

---

## Como funciona internamente

### Fingerprint

`app/services/fingerprint.py` — peça central do matching de layout.

**Ideia:** dois PDFs da mesma empresa no mesmo sistema de ponto geram **o mesmo hash**; PDFs de layouts diferentes geram hashes diferentes.

**Algoritmo:**

1. Extrai texto da 1ª página com pdfplumber.
2. Normaliza: NFKC, lowercase, remove dígitos.
3. Tokeniza em palavras ≥3 chars.
4. Mantém apenas tokens na `WHITELIST` — labels estruturais de cartão de ponto (`entrada`, `saída`, `jornada`, `matrícula`, `funcionário`, etc.).
5. Ordena e deduplica.
6. Concatena + dimensões da página + nº colunas da maior tabela.
7. SHA-256, pega primeiros 16 chars.

**Por que uma whitelist, não o texto todo?** Porque senão o hash dependeria do nome do funcionário, das datas, do departamento — variáveis. A whitelist captura só os "rótulos" do layout, que são estáveis entre PDFs da mesma empresa.

**Quando modificar:** se começar a ver colisões de fingerprint entre empresas diferentes OU diferenças de fingerprint entre PDFs que deveriam ter o mesmo, ajuste a `WHITELIST` em `fingerprint.py`.

### Cascata de fallback

`app/services/extracao_esqueleto.py::aplicar_esqueleto`

Três tentativas, cada uma gatilhada pela qualidade da anterior:

1. **Método preferencial** declarado no esqueleto.
2. **OCR guiado** — se método era plumber, resultado ruim E PDF parece escaneado.
3. **IA barata** — se ainda ruim.

"Resultado ruim" é decidido por `_diagnostica_extracao`:

- `zero_linhas`: não extraiu nada
- `colunas_tipadas_todas_vazias`: todas as células das colunas declaradas como `hora` ou `data` vieram vazias
- `maioria_linhas_com_1_celula`: pelo menos 3 linhas, e >70% delas com só 1 célula preenchida — é ruído

Chave: a cascata **preserva o cabeçalho** do método anterior se o novo vier vazio, e **não regride** linhas (se IA traz menos que plumber já tinha, mantém o plumber).

### Score de conformidade

`app/services/conformidade.py::calcular_score_detalhado`

3 componentes ponderados (total = 1.0):

| Componente | Peso | Descrição |
|---|:-:|---|
| `tem_linhas` | 40% | 1.0 se há pelo menos 1 linha; 0.0 se vazio |
| `frac_cabecalho` | 30% | fração de campos do cabeçalho preenchidos |
| `frac_celulas` | 30% | fração de células `hora`/`data` bem parseadas; **1.0 se o esqueleto não declara colunas tipadas** |

Mais penalidade: `0.02 por aviso`, capada em `0.10`.

**Validação permissiva** de célula: aceita `HH:MM`, `HH:MM:SS`, `HHhMM`, `DD/MM`, `DD/MM/YYYY`, `DD-MM-YYYY`. Antes era restrita demais e derrubava score de extrações corretas.

O breakdown vai pro `resultado_json.score_breakdown` — visível no modal de detalhes do histórico. Quando um score parecer estranho, ali mostra qual componente puxou pra baixo.

### Storage em memória

`app/services/storage.py` — guarda efêmero com TTL 1h, thread-safe, 3 namespaces:

- **`put_pdf / get_pdf`** — bytes do PDF durante processamento
- **`put_proposta / get_proposta`** — JSON proposto pela IA no cadastro assistido
- **`put_metadata / get_metadata`** — info extra do upload (webhook_url, modelo_potente)

Limpeza preguiçosa (`_gc`) a cada leitura. Também `clear_all_for_tests` pra fixtures.

**Não é distribuído.** Se escalar pra múltiplas réplicas no Railway, trocar por Redis ou object storage.

### Sweeper de órfãos

`app/services/sweeper.py::varrer_orfaos`

Marca processamentos como `falhou`:

- `em_processamento` há mais de 10 min (provavelmente container reiniciou durante task)
- `aguardando_cadastro` há mais de 1h (TTL do storage expirou; PDF sumiu)

Roda no startup (em `main.py`) e lazy antes de listar histórico (em `routes/history.py`). Também remove bytes/proposta/metadata do storage.

---

## How-tos

### Adicionar novo modelo de IA

**Para cadastro assistido (Vision):**

1. Abra `app/config.py` → `modelos_potentes_catalogo`
2. Adicione a entrada:
   ```python
   {"id": "provider/model-name", "suporta_visao": True},
   ```
   `suporta_visao=True` se o modelo aceita `image_url` no content. `False` se é text-only. Em caso de dúvida, marque True — o backend faz fallback automático se o provedor rejeitar imagem (captura `LLMImageUnsupportedError`).

**Para fallback (texto):**

1. Abra `app/config.py` → `modelos_baratos_catalogo`
2. Adicione a entrada do mesmo jeito.

**Pronto.** Não precisa mudar mais nada:
- O endpoint `/api/extract/modelos-disponiveis` já expõe catálogos completos.
- O frontend popula dropdowns dinamicamente.
- Whitelist de segurança em `modelos_potentes_permitidos` / `modelos_baratos_permitidos` é derivada dos catálogos.

### Adicionar novo método de extração

1. Adicione o valor no enum em `app/models/enums.py::MetodoExtracao`:
   ```python
   ESQUELETO_MEU_METODO = "esqueleto_meu_metodo"
   ```
2. Adicione no `Literal` em `app/schemas/esqueleto.py::MetodoPreferencial`.
3. Implemente a função em `app/services/extracao_esqueleto.py`:
   ```python
   def _meu_metodo(pdf_bytes, estrutura, avisos) -> dict[str, Any]:
       # retorna {"cabecalho": {...}, "linhas": [...]}
   ```
4. Acrescente um branch em `aplicar_esqueleto`:
   ```python
   elif metodo_preferencial in (MetodoExtracao.ESQUELETO_MEU_METODO.value, "meu_metodo"):
       dados = _meu_metodo(pdf_bytes, estrutura, avisos)
       metodo_efetivo = MetodoExtracao.ESQUELETO_MEU_METODO.value
   ```
5. Se o método puder se encaixar na cascata, ajuste a lógica de fallback em `aplicar_esqueleto`.

6. Teste manualmente com PDFs reais (os casos positivos e de fallback).

### Criar migration do banco

Use Alembic. Dentro do container:

```bash
# Gerar migration automática (detecta mudanças nos modelos)
docker-compose exec app alembic revision --autogenerate -m "descrição curta"

# Revisar o arquivo gerado em migrations/versions/ — autogenerate erra às vezes
# (muda tipo JSON em SQLite vs Postgres, renames viram drop+create, etc.)

# Aplicar
docker-compose exec app alembic upgrade head
```

**Convenções**:
- Arquivo nomeado `NNNN_descricao.py`, ID `"NNNN_descricao"` (string estável, não hash).
- `down_revision` aponta pra migration anterior.
- Sempre implemente `downgrade()` — mesmo se não for "perfeito", pelo menos `op.drop_table/drop_column`.

**Cuidado com dialetos**: SQLite e Postgres diferem. Valide rodando migrations contra Postgres (docker-compose). `sa.Uuid()`, `sa.JSON()`, `sa.DateTime(timezone=True)` funcionam em ambos; tipos nativos (`sa.ARRAY`, enums nativos) só em Postgres.

### Adicionar campo ao Esqueleto

Tem duas abordagens:

**a) Campo estrutural** (indexável, consultável): adiciona coluna no modelo.

1. `app/models/esqueleto.py` — adicione `mapped_column(...)`.
2. Gere migration: `alembic revision --autogenerate -m "add campo_x em esqueleto"`.
3. Revise e ajuste a migration.
4. Atualize `app/schemas/empresa.py::EsqueletoDetail` com o campo novo.
5. Atualize `app/routes/esqueletos.py::_serializar` para incluí-lo na resposta.
6. Se deve ser editável via UI, adicione em `EsqueletoUpdateRequest` e em `patch_esqueleto`.

**b) Campo dentro da estrutura JSON** (mais flexível, sem migration): adicione no schema Pydantic.

1. `app/schemas/esqueleto.py::EstruturaEsqueleto` — adicione o campo.
2. Use em `services/extracao_esqueleto.py` via `estrutura.get("novo_campo")`.
3. Frontend do cadastro pode expor dropdown/input e injetar na `estrutura` antes do POST.

Exemplo já presente: `modelo_fallback` é campo de estrutura (b). Serve quando não precisa de query/filtro SQL por esse valor.

### Debugar extração com score baixo

1. Abra o histórico → clique em "Detalhes" no processamento.
2. No modal, o JSON `resultado_json` inclui `score_breakdown` com cada componente:
   ```json
   {
     "frac_cabecalho": 0.5,
     "tem_linhas": 1.0,
     "frac_celulas": 0.0,
     "tem_colunas_tipadas": true,
     "num_avisos": 2,
     "penalidade_avisos": 0.04,
     "score_final": 0.41
   }
   ```
3. Também veja `resultado_json.avisos` — lista os fallbacks acionados:
   ```
   ["esqueleto_plumber_colunas_tipadas_todas_vazias_tentando_ia_barata"]
   ```
4. Se `frac_celulas=0`, a tabela foi detectada mas os valores não casam com formato hora/data. Possível causa:
   - Regex do parser (`_RE_HORA_PERMISSIVA`) não reconhece o formato — amplie em `app/services/conformidade.py`
   - Esqueleto declarou o tipo errado — edite via UI em `/empresa-detalhe.html`
5. Se `frac_cabecalho` baixo, a regex do cabeçalho está falhando — reveja no PATCH do esqueleto.

Nos logs do Railway, você vê o score de cada extração:
```
score esqueleto=uuid final=0.410 cabecalho=0.500 linhas=1.0 celulas=0.000 avisos=2 penal=0.040
```

### Editar estrutura de esqueleto existente

**Via UI (recomendado pra usuários):**

1. `/empresas.html` → clica na empresa
2. Na lista de esqueletos, botão "Ver/Editar"
3. Modal com textarea JSON + validação live → Salvar → `PATCH /api/esqueletos/{id}`

**Via API:**

```bash
curl -X PATCH https://.../api/esqueletos/UUID \
  -H "Content-Type: application/json" \
  -H "Cookie: pontoextract_session=..." \
  -d '{"estrutura": {...}, "exemplos_validados": [...]}'
```

**Diretamente no DB** (último recurso):

```sql
UPDATE esqueletos
SET estrutura = '{...}'::jsonb
WHERE id = '...';
```

### Adicionar novo endpoint protegido

1. Crie o arquivo em `app/routes/`:
   ```python
   from fastapi import APIRouter, Depends
   from app.deps import get_db, require_auth

   router = APIRouter(prefix="/minhacoisa", tags=["minhacoisa"])

   @router.get("")
   def listar(auth: dict = Depends(require_auth), db=Depends(get_db)):
       ...
   ```
2. Registre em `main.py`:
   ```python
   from app.routes import minhacoisa
   app.include_router(minhacoisa.router, prefix="/api")
   ```
3. O middleware `auth_gate` em `main.py` já protege automaticamente `/api/*`, exceto `health` e `auth`.

Se for endpoint público, adicione em `_PUBLIC_API_PATHS` ou `_PUBLIC_API_PREFIXES` em `main.py`. Pense bem antes.

### Alterar regras do sweeper

`app/services/sweeper.py`:

- `LIMITE_EM_PROCESSAMENTO_MIN` (10 min) — tempo após o qual um processamento stuck em `em_processamento` é marcado como `falhou`
- `LIMITE_AGUARDANDO_CADASTRO_MIN` (60 min) — equivalente ao TTL do storage

Se aumentar o TTL do storage (`app/services/storage.py::DEFAULT_TTL_SECONDS`), sincronize o sweeper.

---

## Commits e Pull Requests

### Mensagens de commit

Estilo adotado:

```
<tipo>: <assunto curto em minúscula> (fase/contexto opcional)

<corpo explicando o porquê, quando não for óbvio>

Co-Authored-By: ...
```

Tipos:
- `feat`: nova funcionalidade
- `fix`: correção de bug
- `chore`: manutenção (reorg, deps, .gitignore)
- `test`: adicionar/ajustar testes
- `docs`: só documentação

Exemplo real do histórico:

```
fix: ia_barata_com_exemplos — correção profunda da lógica de acionamento

Problemas encontrados e corrigidos:
1. Acionamento travado: só rodava com linhas=[] E exemplos!=[].
   Agora dispara por "zero_linhas" OU "colunas_tipadas_todas_vazias"
   OU "maioria_linhas_com_1_celula".
2. ...
```

**Mensagens ruins** para evitar:
- `wip`, `fix`, `tudo pronto`, `atualizações`
- Mensagens sem o "porquê" em mudanças não triviais

### Commits atômicos

Cada commit deve ser uma unidade lógica. Não misture refatoração com bugfix. Não commit de 40 arquivos "alterações diversas".

### Pull Requests

Se o projeto for para um fluxo de PRs:

- Branch nomeada: `feat/...`, `fix/...`, `chore/...`
- Descreva **o que** e **o porquê**, com screenshots de tela se for frontend
- Linke issue/ticket se houver
- Rode `pytest` local antes
- Aguarde CI passar (se existir)

---

## Checklist antes do deploy

1. [ ] `pytest` passa
2. [ ] Se mudou o schema do DB, migration criada e testada contra Postgres local
3. [ ] Se mudou `requirements.txt`, rebuildou o container local com sucesso
4. [ ] Se adicionou variável de ambiente, atualizou:
   - `.env.example`
   - `README.md` (tabela de variáveis)
   - `app/config.py` (definição + validação)
5. [ ] Se adicionou endpoint novo: documentou no README e garantiu que está sob auth (se devido)
6. [ ] Se mexeu no frontend: testou no browser (pelo menos em 1 navegador)
7. [ ] Logs não contêm PII de PDF (grep `print`, `logger.info` com conteúdo)
8. [ ] Nenhuma chave ou senha no diff
9. [ ] Commits atômicos com mensagens descritivas

Depois do push:

- [ ] Railway: deploy foi para `Active`
- [ ] `/api/health` responde 200
- [ ] Teste de fumaça: login + upload de 1 PDF conhecido
- [ ] Checar que migrations aplicaram (se havia)

---

## Coisas que NÃO fazer

- **Commitar PDFs reais** — `.gitignore` bloqueia, mas vale o lembrete. PDFs sintéticos de teste em `tests/fixtures/pdfs/` precisam se chamar `synthetic_*.pdf`.
- **Commitar `.env`** — só `.env.example` vai pro repo.
- **Logar conteúdo de PDF** ou qualquer PII.
- **Skip-ar hooks** (`--no-verify` no commit, `--force` no push) sem autorização explícita.
- **Amendar commits que já foram pusheados** — reescreve o histórico compartilhado.
- **Mexer em `app/config.py` removendo validações** sem entender o porquê elas estão lá.
- **Adicionar dependência pesada** só pra usar 5 linhas. Tem algo que já temos (`httpx`, `pydantic`)?
- **Criar um `async` gratuito** em `services/` — o pipeline é sync de propósito.
- **Colocar lógica de negócio em `routes/`** — routes são só thin HTTP adapters. Serviços têm a lógica, e rodam sem FastAPI.
- **Instalar múltiplos drivers de DB** (asyncpg + psycopg2). Um só, `psycopg2-binary`.
- **Duplicar a lista de modelos** em vários lugares. Catálogo único em `app/config.py`, consumido via endpoint.
- **Colocar segredos em `railway.toml`** ou qualquer arquivo commitado — só no painel de Variables do Railway.

---

## Dúvidas?

- Detalhes técnicos e decisões de arquitetura: [`DECISIONS.md`](./DECISIONS.md)
- Visão geral, conceitos, fluxos: [`README.md`](./README.md)
- Histórico do repo: `git log --oneline` — os commits têm contexto detalhado
