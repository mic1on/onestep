import { defineConfig } from 'vitepress'

export default defineConfig({
  vite: {
    configFile: false,
  },
  locales: {
    root: {
      label: '简体中文',
      lang: 'zh-CN',
      title: 'OneStep',
      description: '轻量级 Python 异步任务运行时',
    },
    en: {
      label: 'English',
      lang: 'en-US',
      title: 'OneStep',
      description: 'Lightweight Python async task runtime',
      themeConfig: {
        outline: {
          label: 'On this page',
        },
        editLink: {
          pattern: 'https://github.com/mic1on/onestep/edit/main/docs/:path',
          text: 'Edit this page',
        },
        lastUpdated: {
          text: 'Last updated',
          formatOptions: {
            dateStyle: 'medium',
            timeStyle: 'short',
          },
        },
        nav: [
          { text: 'Guide', link: '/en/guide/' },
          { text: 'Core', link: '/en/core/' },
          { text: 'Connectors', link: '/en/broker/' },
          { text: 'User Cases', link: '/en/guide/cases/' },
          { text: 'Deploy', link: '/en/guide/deploy' },
          {
            text: 'More',
            items: [
              { text: 'YAML', link: '/en/yaml-task-definition' },
              { text: 'SKILL', link: '/en/skill/' },
              { text: 'Web Console', link: '/en/control-plane/' },
              { text: 'Agent WS Protocol', link: '/en/agent-ws-protocol' },
            ],
          },
        ],
        sidebar: [
          {
            text: 'Guide',
            items: [
              { text: 'Quick Start', link: '/en/guide/' },
              { text: 'Tutorial', link: '/en/guide/tutorial' },
              { text: 'Logging & Task Events', link: '/en/guide/logging' },
              { text: 'Features', link: '/en/guide/features' },
              { text: 'Production Deploy', link: '/en/guide/deploy' },
              { text: 'Worker Runtime Image', link: '/en/guide/worker-runtime-image' },
              { text: 'Migrate to onestep-sql', link: '/en/guide/migrate-to-onestep-sql' },
            ],
          },
          {
            text: 'User Cases',
            items: [
              { text: 'Cases Overview', link: '/en/guide/cases/' },
              { text: 'MySQL to Feishu Bitable Order Sync', link: '/en/guide/cases/mysql-feishu-order-sync' },
            ],
          },
          {
            text: 'Core',
            items: [
              { text: 'Core Concepts', link: '/en/core/' },
              { text: 'Connector', link: '/en/core/connector' },
              { text: 'Events & Lifecycle', link: '/en/core/middleware' },
              { text: 'Retry & Dead Letter', link: '/en/core/retry' },
            ],
          },
          {
            text: 'Connectors',
            items: [
              { text: 'Overview', link: '/en/broker/' },
              { text: 'Memory', link: '/en/broker/memory' },
              { text: 'Cron & Interval', link: '/en/broker/cron' },
              { text: 'Webhook', link: '/en/broker/webhook' },
              { text: 'HTTP Sink', link: '/en/broker/http' },
              { text: 'RabbitMQ', link: '/en/broker/rabbitmq' },
              { text: 'Redis Streams', link: '/en/broker/redis' },
              { text: 'AWS SQS', link: '/en/broker/sqs' },
              { text: 'Cloudflare Queues', link: '/en/broker/cf-queues' },
              { text: 'MySQL', link: '/en/broker/mysql' },
              { text: 'PostgreSQL', link: '/en/broker/postgres' },
              { text: 'PostgreSQL Tracked Execution', link: '/en/broker/postgres-execution' },
              { text: 'MongoDB', link: '/en/broker/mongodb' },
              { text: 'Elasticsearch / OpenSearch', link: '/en/broker/elasticsearch' },
              { text: 'ClickHouse', link: '/en/broker/clickhouse' },
              { text: 'Kafka', link: '/en/broker/kafka' },
              { text: 'Feishu Bitable', link: '/en/broker/feishu-bitable' },
              { text: 'Custom Source/Sink', link: '/en/broker/custom' },
            ],
          },
          {
            text: 'Operations & Integration',
            items: [
              { text: 'YAML Task Definition', link: '/en/yaml-task-definition' },
              { text: 'SKILL', link: '/en/skill/' },
              { text: 'Core Reliability', link: '/en/core-reliability' },
              { text: 'Stable Instance Identity', link: '/en/stable-instance-identity' },
              { text: 'Agent WS Protocol', link: '/en/agent-ws-protocol' },
              { text: 'Control Plane', link: '/en/control-plane/' },
              { text: 'Connector Conformance', link: '/en/connector-conformance' },
              { text: 'Cross-Repo Collaboration', link: '/en/ws-cross-repo-collaboration' },
            ],
          },
        ],
      },
    },
  },
  cleanUrls: true,
  srcExclude: [
    'superpowers/plans/2026-08-14-feishu-insert-incremental-sync.md',
    'superpowers/specs/2026-08-14-feishu-insert-incremental-sync-design.md',
  ],
  lastUpdated: true,
  head: [
    ['meta', { name: 'theme-color', content: '#303f9f' }],
    ['meta', {
      name: 'keywords',
      content: 'onestep, python, async task, queue, schedule, cron, webhook, http sink, rabbitmq, redis, sqs, mysql, postgresql, kafka'
    }],
    ['link', { rel: 'icon', href: '/favicon.ico' }],
    ['meta', { property: 'og:title', content: 'OneStep' }],
    ['meta', { property: 'og:description', content: 'Lightweight Python async task runtime' }],
    ['meta', { property: 'og:url', content: 'https://onestep.code05.com/' }],
    ['meta', { property: 'og:image', content: 'https://onestep.code05.com/og.png' }],
    ['meta', { name: 'twitter:card', content: 'summary_large_image' }],
  ],
  markdown: {
    theme: {
      light: 'github-light',
      dark: 'github-dark',
    },
  },
  themeConfig: {
    logo: '/logo-3.svg',
    search: {
      provider: 'local',
      options: {
        locales: {
          root: {
            translations: {
              button: {
                buttonText: '搜索文档',
                buttonAriaLabel: '搜索文档',
              },
              modal: {
                noResultsText: '未找到相关结果',
                resetButtonTitle: '清除搜索条件',
                footer: {
                  selectText: '选择',
                  navigateText: '切换',
                  closeText: '关闭',
                },
              },
            },
          },
          en: {
            translations: {
              button: {
                buttonText: 'Search',
                buttonAriaLabel: 'Search docs',
              },
              modal: {
                noResultsText: 'No results found',
                resetButtonTitle: 'Clear search',
                footer: {
                  selectText: 'to select',
                  navigateText: 'to navigate',
                  closeText: 'to close',
                },
              },
            },
          },
        },
      },
    },
    nav: [
      { text: '指南', link: '/guide/' },
      { text: '核心', link: '/core/' },
      { text: '连接器', link: '/broker/' },
      { text: '实战', link: '/guide/cases/' },
      { text: '部署', link: '/guide/deploy' },
      {
        text: '集成',
        items: [
          { text: 'YAML', link: '/yaml-task-definition' },
          { text: 'SKILL', link: '/skill/' },
          { text: 'Web 控制台', link: '/control-plane/' },
          { text: 'Agent WS 协议', link: '/agent-ws-protocol' },
        ],
      },
    ],
    sidebar: [
      {
        text: '指南',
        items: [
          { text: '快速开始', link: '/guide/' },
          { text: '入门教程', link: '/guide/tutorial' },
          { text: '日志与任务事件', link: '/guide/logging' },
          { text: '功能特性', link: '/guide/features' },
          { text: '生产部署', link: '/guide/deploy' },
          { text: 'Worker Runtime Image', link: '/guide/worker-runtime-image' },
          { text: '迁移到 onestep-sql', link: '/guide/migrate-to-onestep-sql' },
        ],
      },
      {
        text: '用户案例 / 实战篇',
        items: [
          { text: '案例总览', link: '/guide/cases/' },
          { text: '订单流水同步到飞书多维表格', link: '/guide/cases/mysql-feishu-order-sync' },
        ],
      },
      {
        text: '核心',
        items: [
          { text: '核心概念', link: '/core/' },
          { text: 'Connector', link: '/core/connector' },
          { text: '事件与生命周期', link: '/core/middleware' },
          { text: '重试与死信', link: '/core/retry' },
        ],
      },
      {
        text: '连接器',
        items: [
          { text: '概览', link: '/broker/' },
          { text: 'Memory', link: '/broker/memory' },
          { text: 'Cron & Interval', link: '/broker/cron' },
          { text: 'Webhook', link: '/broker/webhook' },
          { text: 'HTTP Sink', link: '/broker/http' },
          { text: 'RabbitMQ', link: '/broker/rabbitmq' },
          { text: 'Redis Streams', link: '/broker/redis' },
          { text: 'AWS SQS', link: '/broker/sqs' },
          { text: 'Cloudflare Queues', link: '/broker/cf-queues' },
          { text: 'MySQL', link: '/broker/mysql' },
          { text: 'PostgreSQL', link: '/broker/postgres' },
          { text: 'PostgreSQL Tracked Execution', link: '/broker/postgres-execution' },
          { text: 'MongoDB', link: '/broker/mongodb' },
          { text: 'Elasticsearch / OpenSearch', link: '/broker/elasticsearch' },
          { text: 'ClickHouse', link: '/broker/clickhouse' },
          { text: 'Kafka', link: '/broker/kafka' },
          { text: 'Feishu Bitable', link: '/broker/feishu-bitable' },
          { text: '自定义 Source/Sink', link: '/broker/custom' },
        ],
      },
      {
        text: '运行与集成',
        items: [
          { text: 'YAML 任务定义', link: '/yaml-task-definition' },
          { text: 'SKILL', link: '/skill/' },
          { text: '核心可靠性', link: '/core-reliability' },
          { text: '稳定实例身份', link: '/stable-instance-identity' },
          { text: 'Agent WS 协议', link: '/agent-ws-protocol' },
          { text: 'Control Plane', link: '/control-plane/' },
          { text: 'Connector Conformance', link: '/connector-conformance' },
          { text: '跨仓协作', link: '/ws-cross-repo-collaboration' },
        ],
      },
    ],
    socialLinks: [
      { icon: 'github', link: 'https://github.com/mic1on/onestep' },
    ],
    footer: {
      message: 'Released under the MIT License.',
      copyright: 'Copyright © 2023-present MicLon',
    },
    outline: {
      level: [2, 3],
      label: '本页目录',
    },
    editLink: {
      pattern: 'https://github.com/mic1on/onestep/edit/main/docs/:path',
      text: '编辑此页',
    },
    lastUpdated: {
      text: '最后更新',
      formatOptions: {
        dateStyle: 'medium',
        timeStyle: 'short',
      },
    },
  },
})
