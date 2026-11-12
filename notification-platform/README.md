# ДЗ №4: Notification Platform — требования и архитектурное мышление

| Файл | Что внутри |
|---|---|
| [01-requirements.md](01-requirements.md) | Задания 1–7: функциональные и нефункциональные требования, ASR, архитектурные вопросы, последствия ASR, неподходящие решения, неопределённости |
| [02-rfc-guaranteed-delivery.md](02-rfc-guaranteed-delivery.md) | Задание 8: RFC «Гарантированная доставка критичных уведомлений с кросс-канальным failover» — расчёт нагрузки, два варианта (Kafka + PostgreSQL / Temporal) с C4 и sequence-диаграммами, сравнение, выбор и компромиссы |
| [diagrams/](diagrams/) | Исходники PlantUML (`*.puml`) и отрендеренные SVG |

Перерисовать диаграммы после правки исходников:

```bash
cd notification-platform/diagrams
docker run --rm -v "$PWD":/data plantuml/plantuml:1.2024.7 -tsvg $(for f in *.puml; do echo /data/$f; done)
```
