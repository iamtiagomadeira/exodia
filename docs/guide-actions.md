# Guia de Actions

Uma **Action** muda estado. Por isso é sempre **guardada** por um fluxo de segurança
de seis passos — nunca executa "à séria" sem passar por eles.

## O fluxo guardado de 6 passos

1. **Pré-checks** — os checks declarados em `requires_checks` da action têm de passar.
   Se algum bloqueia, a action é abortada.
2. **Dry-run** — descreve exatamente o que `execute()` faria, sem o fazer. É **sempre**
   mostrado. Em modo dry-run (o **default**), pára aqui.
3. **Confirmação** — sem `--yes`, pára e aguarda confirmação explícita.
4. **Execução** — corre `execute()`. Se falhar, enriquece com a KB e **não** verifica.
5. **Verificação** — `verify()` confirma que a action atingiu o objetivo.
6. **Rollback** — reversão *best-effort*; por omissão documentada (aponta runbook/SAP Note).

Ver o diagrama completo em [Arquitetura](architecture.md#4-fluxo-guardado-de-6-passos).

## Correr uma action

```bash
# dry-run (default) — mostra o plano, não muda nada:
exodia run backup-restore.restore-db --db-type hana --source PRD --target QAS

# executar mesmo — precisa de AMBOS --execute e --yes:
exodia run backup-restore.restore-db --db-type hana --execute --yes --config exodia.yaml
```

!!! danger "Dry-run é o default por design"
    Nada muda estado a menos que passes explicitamente `--execute`. E mesmo com
    `--execute`, sem `--yes` a action pára no passo de confirmação. Isto é
    intencional — segurança por omissão.

## Actions disponíveis (exemplos)

| Action | Requires checks | O que faz |
|---|---|---|
| `backup-restore.restore-db` | capacity, connectivity, ... | Restore de DB via ferramentas nativas |
| `backup-restore.swpm-system-copy` | vários | Orquestra o SWPM system copy |
| `pipo.postcopy` | secstore, rfc-jco, ... | Passos de post-copy do Java PI/PO |

## Rollback

O `rollback()` por omissão é *documented-only*: aponta para o runbook ou SAP Note com
os passos manuais, em vez de tentar uma reversão automática arriscada. Actions
específicas podem sobrepor-se com rollback real quando é seguro fazê-lo.

## Escrever uma action nova

Ver o guia completo em [Contribuir](contributing.md#adicionar-uma-action-nova).
Subclassa `Action`, implementa `dry_run()`, `execute()`, `verify()` (e opcionalmente
`rollback()`), e declara `requires_checks`. O fluxo guardado é aplicado
automaticamente pelo runner.
