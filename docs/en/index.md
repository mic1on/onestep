---
layout: home
sidebar: false

title: OneStep
titleTemplate: Lightweight Python async task runtime

hero:
  name: OneStep
  text: Lightweight Python async task runtime
  tagline: Connect queues, timers, webhooks, databases, and task handlers with a single app object.
  image:
    src: /logo-3.svg
    alt: OneStep
  actions:
    - theme: brand
      text: Quick Start
      link: /guide/
    - theme: alt
      text: View Connectors
      link: /broker/
    - theme: alt
      text: GitHub
      link: https://github.com/mic1on/onestep

features:
  - title: One Runtime
    details: OneStepApp handles task registration, resource opening, Source loop execution, and graceful shutdown.
  - title: Multiple Sources / Sinks
    details: Built-in Memory, Interval, Cron, Webhook and HTTP Sink, with plugin support for RabbitMQ, Redis Streams, AWS SQS, MySQL, PostgreSQL, MongoDB, Elasticsearch/OpenSearch, ClickHouse, Kafka and Feishu Bitable.
  - title: Composable Pipelines
    details: Task return values can be sent to one or more Sinks, chaining processing workflows through queues, databases, or custom interfaces.
  - title: Production Ready
    details: Concurrency, timeout, retry, dead letter, lifecycle hooks, structured events, YAML configuration, worker packaging and control-plane telemetry.
---
