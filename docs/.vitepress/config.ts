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
    outline: {
      level: [2, 3],
    },
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
    socialLinks: [
      { icon: 'github', link: 'https://github.com/mic1on/onestep' },
    ],
    footer: {
      message: 'Released under the MIT License.',
      copyright: 'Copyright © 2023-present MicLon',
    },
    locales: {
      root: {
        outline: {
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
      },
      en: {
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
          { text: 'Guide', link: '/guide/' },
          { text: 'Core', link: '/core/' },
          { text: 'Connectors', link: '/broker/' },
          {
            text: 'More',
            items: [
              { text: 'Tutorial', link: '/guide/tutorial' },
              { text: 'User Cases', link: '/guide/cases/' },
              { text: 'Deploy', link: '/guide/deploy' },
              { text: 'YAML', link: '/yaml-task-definition' },
              { text: 'SKILL', link: '/skill/' },
              { text: 'Web Console', link: '/control-plane/' },
              { text: 'Agent WS Protocol', link: '/agent-ws-protocol' },
            ],
          },
        ],
        sidebar: [
          {
            text: 'Guide',
            items: [
              { text: 'Quick Start', link: '/guide/' },
              { text: 'Tutorial', link: '/guide/tutorial' },
              { text: 'Logging & Task Events', link: '/guide/logging' },
              { text: 'Features', link: '/guide/features' },
              { text: 'Production Deploy', link: '/guide/deploy' },
              { text: 'Worker Runtime Image', link: '/guide/worker-runtime-image' },
            ],
          },
          {
            text: 'User Cases',
            items: [
              { text: 'Cases Overview', link: '/guide/cases/' },
              { text: 'MySQL to Feishu Bitable Order Sync', link: '/guide/cases/mysql-feishu-order-sync' },
            ],
          },
          {
            text: 'Core',
            items: [
              { text: 'Core Concepts', link: '/core/' },
              { text: 'Connector', link: '/core/connector' },
              { text: 'Events & Lifecycle', link: '/core/middleware' },
              { text: 'Retry & Dead Letter', link: '/core/retry' },
            ],
          },
          {
            text: 'Connectors',
            items: [
              { text: 'Overview', link: '/broker/' },
              { text: 'Memory', link: '/broker/memory' },
              { text: 'Cron & Interval', link: '/broker/cron' },
              { text: 'Webhook', link: '/broker/webhook' },
              { text: 'HTTP Sink', link: '/broker/http' },
              { text: 'RabbitMQ', link: '/broker/rabbitmq' },
              { text: 'Redis Streams', link: '/broker/redis' },
              { text: 'AWS SQS', link: '/broker/sqs' },
              { text: 'MySQL', link: '/broker/mysql' },
              { text: 'PostgreSQL', link: '/broker/postgres' },
              { text: 'PostgreSQL Tracked Execution', link: '/broker/postgres-execution' },
              { text: 'MongoDB', link: '/broker/mongodb' },
              { text: 'Elasticsearch / OpenSearch', link: '/broker/elasticsearch' },
              { text: 'ClickHouse', link: '/broker/clickhouse' },
              { text: 'Kafka', link: '/broker/kafka' },
              { text: 'Feishu Bitable', link: '/broker/feishu-bitable' },
              { text: 'Custom Source/Sink', link: '/broker/custom' },
            ],
          },
          {
            text: 'Operations & Integration',
            items: [
              { text: 'YAML Task Definition', link: '/yaml-task-definition' },
              { text: 'SKILL', link: '/skill/' },
              { text: 'Core Reliability', link: '/core-reliability' },
              { text: 'Stable Instance Identity', link: '/stable-instance-identity' },
              { text: 'Agent WS Protocol', link: '/agent-ws-protocol' },
              { text: 'Control Plane', link: '/control-plane/' },
              { text: 'Connector Conformance', link: '/connector-conformance' },
              { text: 'Cross-Repo Collaboration', link: '/ws-cross-repo-collaboration' },
            ],
          },
        ],
      },
    },
  },
})
