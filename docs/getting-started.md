# Getting Started

## Instalação

Requer Python ≥ 3.11.

```bash
# a partir do código-fonte:
git clone https://github.com/iamtiagomadeira/exodia.git
cd exodia
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[tui]"
```

## O primeiro `exodia doctor`

O `doctor` é um self-check: confirma que o core está saudável e que o registry
descobriu os módulos.

```console
$ exodia doctor
exodia 0.1.0
  discovered checks : 23
  discovered actions: 7
  KB error entries  : 35
✅ core healthy
```

Se vires os checks e actions descobertos e `✅ core healthy`, estás pronto.

## Listar operações

```bash
exodia list          # mostra todos os checks (só-leitura) e actions (guardadas)
```

## Correr um check

Checks são só-leitura — seguros a qualquer hora:

```bash
exodia run core.free-space --config exodia.yaml
```

## Correr uma action

Actions são guardadas e fazem **dry-run por omissão** (nada executa):

```bash
# dry-run (default): mostra o que faria, sem mudar nada
exodia run backup-restore.restore-db --db-type hana --source PRD --target QAS

# executar mesmo: precisa de --execute E --yes
exodia run backup-restore.restore-db --db-type hana --execute --yes
```

Exit codes são amigáveis para automação: `0` = nada bloqueante, `1` = falha bloqueante.

## Configuração via ficheiro

Em vez de muitas flags, usa um `exodia.yaml` validado (schema Pydantic):

```yaml
db_type: hana
sid: PRD
source: PRD
target: QAS
dry_run: true
escape_hatch:
  extra_params:
    userstore_key: SYSTEMDB
```

```bash
exodia run backup-restore.restore-db --config exodia.yaml
```

As flags do CLI têm prioridade sobre os valores do ficheiro. O ficheiro é validado
no carregamento — um `db_type` inválido, por exemplo, dá uma mensagem clara e amigável
em vez de um traceback. Todos os campos estão documentados em `exodia.example.yaml`
na raiz do repo. TOML (`exodia.toml`) também é suportado.

## Config: campos tipados + escape hatch

O schema `ExodiaConfig` tem campos tipados para os parâmetros comuns (`db_type`,
`sid`, `source`, `target`, `instance_number`, `inifile`, `product_id`, ...) mais um
bloco `escape_hatch` flexível para os 20% de casos especiais:

| Campo do escape_hatch | Para quê |
|---|---|
| `custom_recover_sql` | SQL de recovery à medida |
| `pre_hooks` / `post_hooks` | Comandos antes/depois da operação |
| `skip_checks` | Nomes de checks a saltar |
| `extra_params` | Qualquer input específico de módulo, via `ctx.get(...)` |
