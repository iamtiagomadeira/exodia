# Troubleshooting (KB embutida)

O Exodia é **auto-suficiente**: quando um check ou action falha de forma bloqueante,
a mensagem de erro é comparada contra uma base de conhecimento (KB) estática embutida
no repo, e a remediação é anexada ao resultado — causa, fix genérico e o número de
SAP Note relevante.

Sem RAG, sem LLM, sem dependência de nenhum serviço externo. Tudo versionado.

## Como funciona

A KB vive em `src/exodia/knowledge/errors/*.yaml`. Cada ficheiro é uma lista de
entradas; cada entrada mapeia um padrão de erro (regex, case-insensitive) para causa,
fix e SAP Note:

```yaml
- pattern: "log backup .*missing|recovery could not be completed"
  cause: "A log backup is missing from the recovery sequence"
  fix:
    - "Verify the log backup exists in the configured backup path / backint"
    - "Copy the missing log backup from source, then resume the recovery"
  sap_note: "1642148"
```

Quando um `Result` bloqueante é produzido, `enrich()` procura a primeira entrada cujo
padrão dá match no `summary + detail` do erro e anexa `cause`, `fix` e `sap_note`.

## Áreas cobertas

| Ficheiro | Domínio |
|---|---|
| `hana_errors.yaml` | Recovery / backup de HANA |
| `ase_errors.yaml` | Load / dump de SAP ASE |
| `pipo_errors.yaml` | System copy de Java PI/PO (SLD, SECSTORE, RFC/JCo) |
| `swpm_errors.yaml` | Erros do SWPM / sapinst |

Confirma quantas entradas estão carregadas com `exodia doctor` (linha `KB error entries`).

## Adicionar uma entrada

Ver [Contribuir → Escrever entradas na KB](contributing.md#escrever-entradas-na-kb).

!!! warning "Regra de IP — não negociável"
    A KB refere **apenas números** de SAP Note e fixes de conhecimento público.
    **Nunca** reproduz o texto das SAP Notes (copyright SAP, atrás de login). E nunca
    inclui dados de clientes reais.
