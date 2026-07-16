# Contribuir para o Exodia

Obrigado pelo interesse. O Exodia é um executor CLI *stateless* para operações de
migração SAP. Este guia cobre setup de dev, os gates de qualidade, e como adicionar
capacidades novas sem partir a espinha dorsal de segurança.

!!! info
    Esta página é o espelho do [`CONTRIBUTING.md`](https://github.com/iamtiagomadeira/exodia/blob/main/CONTRIBUTING.md)
    na raiz do repositório.

## Setup de desenvolvimento

Requer Python ≥ 3.11.

```bash
git clone https://github.com/iamtiagomadeira/exodia.git
cd exodia
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,tui]"
exodia doctor   # confirma que o core está saudável e descobre os módulos
```

## Gates de qualidade (correr antes de cada commit)

Tudo tem de passar limpo:

```bash
ruff check src/ tests/     # lint + import sorting
mypy src/exodia            # type-checking estrito
pytest                     # toda a suite tem de passar
```

Se mexeres na documentação:

```bash
pip install -e ".[docs]"
mkdocs build --strict      # sem warnings
mkdocs serve               # pré-visualização local em http://127.0.0.1:8000
```

## A regra dura: nunca `shell=True`

Todos os comandos são executados como **listas de argumentos** (`list[str]`),
passadas diretamente ao processo. **Nunca** uses `shell=True`, `os.system`, nem
construas strings de shell. Isto elimina injeção de shell como classe inteira de bug
— foi a maior falha da ferramenta interna predecessora. Usa sempre `ctx.runner()`
(`Runner` local ou `SSHRunner` remoto). Segredos passam por stdin, nunca por argv,
e nunca vão para o log.

## Adicionar um Check novo

Um **Check** é uma validação só-leitura. Nunca muta o alvo.

1. Cria um ficheiro sob o módulo certo, ex. `src/exodia/modules/backup_restore/checks/meu_check.py`.
2. Subclassa `Check`, define `name`, `description`, e implementa `run(ctx) -> Result`:

```python
from exodia.core import Check, Context, Result


class MinhaValidacao(Check):
    name = "backup-restore.minha-validacao"
    description = "Valida que X está pronto para o restore."
    blocking = True  # um FAIL aborta a pipeline de prepare imediatamente

    def run(self, ctx: Context) -> Result:
        valor = ctx.get("meu_param", "default")
        cr = ctx.runner().run(["comando", "--flag", valor])
        if not cr.ok:
            return Result.fail(self.name, "X não está pronto", detail=cr.stderr)
        return Result.ok(self.name, "X pronto")
```

3. O registry **auto-descobre** o check — não é preciso registá-lo em lado nenhum.
   Confirma com `exodia list` e `exodia doctor`.

## Adicionar uma Action nova

Uma **Action** muda estado e é *guardada*. Subclassa `Action` e implementa
`dry_run()`, `execute()`, `verify()` (e opcionalmente `rollback()`). Declara os
checks que têm de passar antes via `requires_checks`:

```python
from exodia.core import Action, Context, Result


class MinhaAction(Action):
    name = "backup-restore.minha-action"
    description = "Faz Y de forma guardada."
    requires_checks = ["backup-restore.minha-validacao"]

    def dry_run(self, ctx: Context) -> Result:
        return Result.ok(self.name + ".dry-run", "faria Y com estes parâmetros: ...")

    def execute(self, ctx: Context) -> Result:
        cr = ctx.runner().run(["comando-que-muda-estado"])
        return Result.ok(self.name, "Y feito") if cr.ok else Result.fail(self.name, cr.stderr)

    def verify(self, ctx: Context) -> Result:
        return Result.ok(self.name + ".verify", "Y confirmado")
```

O fluxo guardado (pré-checks → dry-run → confirmação → execute → verify → rollback)
é aplicado automaticamente pelo runner. Ver [Arquitetura](architecture.md).

## Convenção de nomes

Nomes de operações são **dotted**, no formato `modulo.categoria.nome` (ou
`modulo.nome`), em minúsculas com hífenes. Exemplos:

- `core.free-space`
- `backup-restore.restore-db`
- `pipo.postcopy`

## Parâmetros: config tipado + escape hatch

Os parâmetros comuns têm um schema Pydantic formal em `src/exodia/core/config.py`
(`ExodiaConfig`). Para o 20% de casos especiais, usa o bloco `escape_hatch`
(`extra_params`, `pre_hooks`, `post_hooks`, `skip_checks`, `custom_recover_sql`), que
fica acessível nos módulos via `ctx.get("<chave>")`. Ver `exodia.example.yaml` na raiz.

Ao adicionar um parâmetro comum e estável, prefere modelá-lo em `ExodiaConfig`. Só
usa `extra_params` para inputs verdadeiramente específicos de um módulo.

## Escrever entradas na KB

A KB de troubleshooting vive em `src/exodia/knowledge/errors/*.yaml`. Cada entrada
mapeia um padrão de erro para causa, fix genérico e número de SAP Note:

```yaml
- pattern: "regex.*case-insensitive do erro"
  cause: "Explicação curta da causa raiz"
  fix:
    - "Passo genérico de remediação 1"
    - "Passo genérico de remediação 2"
  sap_note: "1642148"
```

## Regras de propriedade intelectual (IP)

**Crítico, não negociável:**

- Refere **apenas números** de SAP Note (ex. `sap_note: "1642148"`).
- **Nunca** reproduzas o texto das SAP Notes — é copyright da SAP e está atrás de login.
- **Nunca** incluas dados de clientes reais (SIDs, hostnames, dumps) em código, testes ou docs.
  Usa placeholders como `PRD`, `QAS`, `HDB`, `/hana/data`.

## Conventional Commits

Usamos [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(config): add Pydantic config schema with escape hatch
fix(hana): handle missing log backup in recovery sequence
docs: add architecture diagram
```

Tipos comuns: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `ci`.

## Antes de abrir um PR

1. Todos os gates passam (`ruff`, `mypy`, `pytest`, `mkdocs build --strict` se mexeste em docs).
2. Testes novos cobrem o comportamento novo.
3. Commits seguem Conventional Commits.
4. Sem `shell=True`, sem segredos no log, sem texto de SAP Note, sem dados de clientes.
