# Subsea Telemetry API

API REST para telemetria de equipamentos submarinos: registro de ativos, ingestão de séries temporais, reamostragem e alertas por envelope operacional.

Projeto de estudo construído para exercitar backend em Python com FastAPI, SQLAlchemy assíncrono, PostgreSQL, Alembic, pandas e Docker, aplicando a um domínio de engenharia submarina (árvore de natal molhada, manifold, riser).

[![CI](https://github.com/amaroAdonis/subsea-telemetry-api/actions/workflows/ci.yml/badge.svg)](https://github.com/amaroAdonis/subsea-telemetry-api/actions/workflows/ci.yml)

---

## Stack

| Camada | Tecnologia |
| --- | --- |
| API | FastAPI, Pydantic v2 |
| Persistência | SQLAlchemy 2.x (async), PostgreSQL 16, asyncpg |
| Migrações | Alembic (modo async, autogenerate) |
| Processamento | pandas, NumPy |
| Testes | pytest, pytest-asyncio, httpx, pytest-cov |
| Infra | Docker, Docker Compose |
| Qualidade | ruff, GitHub Actions |

## Rodar

Tudo em um comando (sobe PostgreSQL, aplica as migrações e serve a API em `localhost:8000`):

```bash
docker compose up --build
```

Documentação interativa (OpenAPI): <http://localhost:8000/docs>

### Desenvolvimento local

```bash
make install        # cria .venv e instala dependências
make up             # sobe só o PostgreSQL
make migrate        # alembic upgrade head
make seed           # gera 7 dias de telemetria sintética para 3 ativos
make run            # uvicorn com reload
```

## Modelo de dados

```
equipment ──┬── sensor_reading     (série temporal, N por ativo)
            └── operating_limit    (envelope por sensor, 1 por ativo+sensor)
```

- **`equipment`** — ativo submarino identificado por tag (`ANM-P-012`), com tipo, campo, lâmina d'água e status.
- **`sensor_reading`** — amostra de um sensor (pressão, temperatura, vibração, vazão) com unidade e timestamp com fuso.
- **`operating_limit`** — faixa esperada de um sensor. É o que alimenta o endpoint de alertas.

Decisões que valem registro:

- **Índice composto `(equipment_id, sensor_type, recorded_at)`** no `sensor_reading`. As consultas da API são sempre "um ativo, um sensor, uma janela"; sem esse índice elas viram varredura assim que um campo acumula alguns milhões de linhas.
- **Enums persistidos pelo valor, não pelo nome.** O padrão do SQLAlchemy gravaria `WET_CHRISTMAS_TREE` numa coluna que o contrato REST descreve como `wet_christmas_tree`. Quem abrisse a tabela no `psql` veria um vocabulário diferente do da API ([`app/models.py`](app/models.py), `enum_column`).
- **`ondelete='CASCADE'` no banco, não só no ORM.** Apagar um ativo precisa levar a série junto mesmo quando a exclusão não passa pela aplicação.

## Endpoints

| Método | Rota | Descrição |
| --- | --- | --- |
| `GET` | `/health` | Liveness, com ida e volta ao banco |
| `POST` | `/equipment` | Cadastra um ativo |
| `GET` | `/equipment` | Lista com filtro por campo, tipo e status |
| `GET` | `/equipment/{id}` | Detalhe |
| `PATCH` | `/equipment/{id}` | Atualização parcial |
| `DELETE` | `/equipment/{id}` | Remove o ativo e sua série |
| `PUT` | `/equipment/{id}/limits` | Define ou substitui o envelope de um sensor |
| `GET` | `/equipment/{id}/limits` | Lista os envelopes |
| `POST` | `/equipment/{id}/readings` | Ingestão em lote |
| `GET` | `/equipment/{id}/readings` | Lista bruta, com janela e filtro de sensor |
| `GET` | `/equipment/{id}/readings/resample` | Reamostragem (pandas) |
| `GET` | `/equipment/{id}/readings/stats` | Estatísticas da série, com p50 e p95 |
| `GET` | `/equipment/{id}/readings/alerts` | Leituras fora do envelope |

### Exemplos

Ingestão em lote:

```bash
curl -X POST localhost:8000/equipment/1/readings \
  -H 'content-type: application/json' \
  -d '{"readings":[
        {"sensor_type":"pressure","value":212.4,"unit":"bar","recorded_at":"2026-05-01T12:00:00Z"},
        {"sensor_type":"pressure","value":214.1,"unit":"bar","recorded_at":"2026-05-01T12:15:00Z"}
      ]}'
```

Reamostragem para janelas de 6 horas, pegando o máximo de cada janela:

```bash
curl "localhost:8000/equipment/1/readings/resample?sensor_type=pressure&freq=6h&agg=max"
```

```json
{
    "equipment_id": 1,
    "sensor_type": "pressure",
    "freq": "6h",
    "agg": "max",
    "points": [
        { "bucket_start": "2026-09-25T00:00:00Z", "value": 225.958, "sample_count": 24 },
        { "bucket_start": "2026-09-25T06:00:00Z", "value": 220.531, "sample_count": 24 }
    ]
}
```

Alertas:

```bash
curl "localhost:8000/equipment/1/readings/alerts"
```

```json
[
    {
        "reading_id": 149,
        "sensor_type": "pressure",
        "value": 120.122,
        "unit": "bar",
        "recorded_at": "2026-09-25T02:04:00Z",
        "min_value": 180.0,
        "max_value": 240.0,
        "breach": "below"
    }
]
```

## Validação e regras

A validação está dividida em três camadas, e cada uma pega o que as outras não pegam:

1. **Pydantic** (`app/schemas.py`) — formato da tag por regex, faixa de lâmina d'água, timestamp obrigatoriamente com fuso, limites com `min < max`.
2. **Regra de aplicação** (`app/routers/readings.py`) — amostra no futuro ou velha demais é recusada, e **um item ruim derruba o lote inteiro**, para o cliente nunca ter de adivinhar qual metade entrou.
3. **Constraint no banco** (`app/models.py`) — `water_depth_m > 0`, `min_value < max_value`, unicidade de tag e de `(equipment_id, sensor_type)`.

Um sensor sem envelope configurado não gera alerta nenhum. Silêncio ali significa "não monitorado", não "saudável", e é por isso que o cadastro de envelope mora no mesmo recurso.

## Processamento de séries (pandas)

[`app/analytics.py`](app/analytics.py) não importa FastAPI nem SQLAlchemy: recebe linhas e devolve valores, o que permite testá-lo sem banco e sem HTTP.

- **`resample`** — telemetria bruta chega em intervalo irregular; o gráfico precisa dela em janelas fixas. Janela vazia é preservada com valor `null`, porque a lacuna na série é informação para quem lê o gráfico.
- **`describe`** — contagem, média, desvio, mínimo, p50, p95, máximo e extremos temporais.
- **`rolling_deviation`** — detecta amostras distantes da própria vizinhança recente, complementando o envelope fixo.

Sobre a `rolling_deviation`: z-score sozinho não funciona nesse domínio. Um sensor estacionado num valor plano tem desvio padrão móvel perto de zero, o que transforma uma oscilação de 1% num z acima de 3. A amostra só é marcada se passar por **dois critérios independentes**: estatisticamente distante da janela (`z_threshold`) **e** materialmente distante dela (`min_relative_change`, como fração da média). Os testes em [`tests/test_analytics.py`](tests/test_analytics.py) cobrem os dois casos.

## ETL / carga

[`scripts/seed.py`](scripts/seed.py) é a rota de carga em lote: lê um CSV exportado do sistema de aquisição, normaliza e grava em chunks.

```bash
python -m scripts.seed                      # dataset sintético: 3 ativos, 7 dias, 6.048 leituras
python -m scripts.seed --csv readings.csv   # export real
python -m scripts.seed --days 30            # mais histórico
```

A limpeza (`clean`) descarta linha sem campo obrigatório, converte tipos com `errors='coerce'`, remove timestamp sem fuso, filtra sensor desconhecido e elimina duplicata de `(tag, sensor, timestamp)` — o export de campo costuma trazer amostra repetida de gateway que tentou reenviar. Tratar isso na carga mantém o banco honesto, em vez de empurrar o problema para toda consulta.

## Testes

```bash
make test       # SQLite em memória, sem precisar de serviço no ar
make test-pg    # mesma suíte contra o PostgreSQL do compose
```

37 testes, 95% de cobertura. A suíte roda nos dois engines de propósito: o SQLite mantém o ciclo rápido e sem dependência, e o PostgreSQL é o que a aplicação de fato usa. O CI executa os dois.

Divisão:

- `tests/test_analytics.py` — unitários do pandas, sem banco e sem HTTP
- `tests/test_equipment.py` — CRUD, validação de schema, conflito de tag, upsert de envelope
- `tests/test_readings.py` — ingestão, rejeição de lote, janela temporal, alertas, reamostragem

## Estrutura

```
app/
├── main.py           entrypoint e health
├── config.py         settings por variável de ambiente (pydantic-settings)
├── database.py       engine async, sessão por request
├── models.py         SQLAlchemy 2.x declarativo
├── schemas.py        contrato da API em Pydantic v2
├── analytics.py      pandas, sem dependência de framework
└── routers/
    ├── equipment.py
    └── readings.py
alembic/versions/     migrações
scripts/seed.py       ETL CSV → PostgreSQL
tests/
```

## Licença

MIT.
