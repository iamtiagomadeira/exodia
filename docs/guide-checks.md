# Guia de Checks

Um **Check** é uma validação **só-leitura**. Nunca muta o alvo, por isso é seguro
correr em qualquer sistema, a qualquer hora — incluindo produção.

## Correr checks

```bash
exodia list                                   # ver todos os checks disponíveis
exodia run core.free-space --config exodia.yaml
exodia run backup-restore.capacity --db-type hana --config exodia.yaml
exodia run <check> --json                     # saída em JSON para automação
```

Um check devolve um `Result` estruturado com um estado: `OK`, `WARN`, `FAIL`,
`ERROR` ou `SKIP`. Se um check **bloqueante** falha dentro de uma pipeline de
`prepare`, a pipeline pára imediatamente.

## Categorias de checks (exemplos)

| Módulo | Exemplos | O que valida |
|---|---|---|
| `core` | `core.free-space` | Espaço livre em disco genérico |
| `backup-restore` | `capacity`, `connectivity`, `security`, `configuration` | Pré-requisitos do restore de DB |
| `pipo` | `sld-reachable`, `secstore-present`, `rfc-jco-config`, `icm-ports` | Pré-requisitos do system copy Java PI/PO |

## Enriquecimento pela KB

Quando um check falha de forma bloqueante, o Exodia procura a mensagem de erro na
[base de conhecimento embutida](troubleshooting.md) e anexa causa, fix genérico e o
número de SAP Note relevante ao resultado. Não precisas de sair para o browser para
saber o próximo passo.

## Skipping de checks

Para saltar um check num run (ex. já validaste manualmente), usa o `escape_hatch`:

```yaml
escape_hatch:
  skip_checks:
    - hana.free-space
```

Um check saltado aparece no relatório como `SKIP` com a razão, não como sucesso.

## Escrever um check novo

Ver o guia completo em [Contribuir](contributing.md#adicionar-um-check-novo). Em resumo:
subclassa `Check`, define `name` (dotted, ex. `backup-restore.minha-validacao`),
`description`, e implementa `run(ctx) -> Result`. O registry auto-descobre-o — sem
wiring central.
