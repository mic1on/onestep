---
layout: home
sidebar: false

title: OneStep
titleTemplate: 轻量级 Python 异步任务运行时

hero:
  name: OneStep
  text: 轻量级 Python 异步任务运行时
  tagline: 用一个应用对象连接队列、定时器、Webhook、数据库和任务处理函数。
  image:
    src: /logo-3.svg
    alt: OneStep
  actions:
    - theme: brand
      text: 快速开始
      link: /guide/
    - theme: alt
      text: 查看连接器
      link: /broker/
    - theme: alt
      text: GitHub
      link: https://github.com/mic1on/onestep

features:
  - title: 一个运行时
    details: OneStepApp 负责注册任务、打开资源、运行 Source 循环并管理关闭流程。
  - title: 多种 Source/Sink
    details: 内置 Memory、Interval、Cron、Webhook 和 HTTP Sink，并通过插件支持 RabbitMQ、Redis Streams、AWS SQS、MySQL、PostgreSQL、Kafka 和 Feishu Bitable。
  - title: 可组合流水线
    details: 任务返回值可以发送到一个或多个 Sink，用队列、数据库或自定义接口串联处理流程。
  - title: 生产友好
    details: 支持并发、超时、重试、死信、生命周期钩子、结构化事件、YAML 配置、worker 打包和控制面遥测。
---
