# Exodia

> Executor CLI *stateless* para operações de migração SAP — checks e actions para
> backup/restore de HANA & ASE, tenant copy, HANA System Replication (HSR) e system
> copy de Java PI/PO.

Exodia é uma ferramenta de linha de comandos leve e plugável que automatiza as partes
repetitivas e propensas a erro das migrações SAP. Corre em qualquer servidor Linux,
não precisa de base de dados própria, e nunca *phone-home*. Pensa nele como
`ansible --check` a encontrar um runbook de SAP Basis: valida pré-requisitos e depois
executa passos de migração com dry-run, confirmação, verificação e rollback documentado.

## O que é

- **Checks** — validações só-leitura. Seguras de correr em qualquer sítio, a qualquer hora.
- **Actions** — operações que mudam estado, sempre **guardadas**: pré-checks → dry-run →
  confirmação → execução → verificação → rollback documentado.

## Porquê

As migrações SAP (backup/restore, tenant copy, setup de HSR, system copy de PI/PO) são
hoje largamente manuais — consultores acompanham ecrãs do `sapinst` durante horas e
correm checks de pré-requisitos à mão. O Exodia transforma isso em automação repetível,
monitorizada e auditável, mantendo o humano no controlo das decisões que importam.

## Princípios

- **Stateless** — corre e sai, sem memória.
- **Dois tipos, um modelo de segurança** — checks só-leitura, actions guardadas.
- **Seguro por construção** — comandos são listas de argumentos, nunca `shell=True`.
  Segredos nunca vão para o log. SSH usa verificação de *host key*.
- **Plugável** — larga um módulo em `exodia/modules/` e é auto-descoberto.
- **Auto-suficiente** — uma KB de troubleshooting embutida mapeia erros para causa,
  fix genérico e o número de SAP Note relevante.
- **Defaults + escape hatch** — defaults opinativos para os 80% do caminho standard,
  mais config/hooks para os 20% de casos especiais.

## Por onde começar

- [Getting Started](getting-started.md) — instalação e o primeiro `exodia doctor`.
- [Guia de Checks](guide-checks.md) — como correr e escrever validações.
- [Guia de Actions](guide-actions.md) — o fluxo guardado de execução.
- [Caso Java PI/PO + HANA](pipo-hana-case.md) — uma migração end-to-end.
- [Troubleshooting (KB)](troubleshooting.md) — a base de conhecimento embutida.
- [Arquitetura](architecture.md) — o modelo Head + Limbs por dentro.
- [Contribuir](contributing.md) — setup de dev, gates e regras.

## Licença

MIT © Tiago Madeira
