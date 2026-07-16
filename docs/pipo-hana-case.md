# Caso: Java PI/PO + HANA (migração end-to-end)

Este é o caso de referência que motivou o Exodia: um **system copy de um SAP PI/PO
(stack Java)** com base de dados **HANA**, feito por **backup/restore**. Aqui mostra-se
o fluxo completo — checks → restore → post-copy — usando placeholders (`PRD` → `QAS`),
nunca dados de clientes reais.

## O cenário

- Origem: sistema PI/PO produtivo `PRD` (Java, HANA).
- Alvo: novo sistema `QAS`, mesma release.
- Método: backup/restore da DB HANA + post-copy do stack Java (SLD, SECSTORE, RFC/JCo, UME).

## Config

```yaml
# exodia.yaml
db_type: hana
system_type: java
sid: QAS
source: PRD
target: QAS
instance_number: "00"
dry_run: true
escape_hatch:
  extra_params:
    userstore_key: SYSTEMDB
    data_backup_prefix: COMPLETE_DATA_BACKUP
    sld_host: sldhost.example
    sld_port: "50000"
    target_ashost: qas-ashost.example
```

## Passo 1 — Checks de pré-requisitos (só-leitura)

Antes de tocar em nada, valida o alvo:

```bash
exodia run backup-restore.capacity      --config exodia.yaml
exodia run backup-restore.connectivity  --config exodia.yaml
exodia run backup-restore.security      --config exodia.yaml
exodia run pipo.sld-reachable           --config exodia.yaml
exodia run pipo.secstore-present        --config exodia.yaml
```

Cada check devolve `OK` / `WARN` / `FAIL`. Um `FAIL` bloqueante traz causa + fix +
SAP Note da [KB](troubleshooting.md). Resolve tudo antes de avançar.

## Passo 2 — Restore da base de dados HANA (action guardada)

Primeiro em dry-run (o default) para ver o plano:

```bash
exodia run backup-restore.restore-db --config exodia.yaml
```

O dry-run mostra o comando de recovery que seria executado (data backup + catálogo +
log backups até ao ponto pedido). Quando estiveres confiante:

```bash
exodia run backup-restore.restore-db --config exodia.yaml --execute --yes
```

O fluxo guardado corre os pré-checks, executa o recovery, e depois **verifica** que a
DB está online e consistente antes de reportar sucesso.

## Passo 3 — Post-copy do stack Java PI/PO (action guardada)

Depois da DB restaurada, o stack Java precisa de post-copy: re-registo no SLD,
recriação do SECSTORE, e reconfiguração de RFC/JCo para apontar ao novo alvo:

```bash
# dry-run: ver os passos de post-copy
exodia run pipo.postcopy --config exodia.yaml

# executar
exodia run pipo.postcopy --config exodia.yaml --execute --yes
```

## Passo 4 — Verificação final

Corre os checks de novo contra o alvo já migrado para confirmar o estado saudável:

```bash
exodia run pipo.as-java-up      --config exodia.yaml
exodia run pipo.rfc-jco-config  --config exodia.yaml
```

## Resultado

Uma migração PI/PO + HANA repetível e auditável: cada passo é só-leitura ou guardado,
cada falha traz remediação com número de SAP Note, e nada muda estado sem dry-run +
confirmação explícita.

!!! note "IP e dados"
    Todos os identificadores acima (`PRD`, `QAS`, `*.example`) são placeholders.
    Nunca coloques SIDs, hostnames ou dumps de clientes reais em configs versionadas.
