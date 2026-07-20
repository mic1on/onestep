import { defineConfig } from 'vitepress'

export default defineConfig({
  vite: {
    configFile: false,
  },
  lang: 'zh-CN',
  title: 'OneStep',
  description: '轻量级 Python 异步任务运行时',
  cleanUrls: true,
  lastUpdated: true,
  head: [
    ['meta', { name: 'theme-color', content: '#303f9f' }],
    ['meta', {
      name: 'keywords',
      content: 'onestep, python, async task, queue, schedule, cron, webhook, http sink, rabbitmq, redis, sqs, mysql, postgresql, kafka'
    }],
    ['link', { rel: 'icon', href: '/favicon.ico' }],
    ['meta', { property: 'og:title', content: 'OneStep' }],
    ['meta', { property: 'og:description', content: '轻量级 Python 异步任务运行时' }],
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
      label: '本页目录',
    },
    search: {
      provider: 'local',
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
      { text: 'YAML', link: '/yaml-task-definition' },
      { text: 'SKILL', link: '/skill/' },
      { text: '部署', link: '/guide/deploy' },
      { text: 'Web 控制台', link: '/control-plane/' },
      {
        text: '版本',
        items: [
          { text: '当前版本', link: '/guide/' },
          { text: 'v0.5.x 归档', link: '/v0.5.x/' },
        ],
      },
    ],
    sidebar: [
      {
        text: '指南',
        items: [
          { text: '快速开始', link: '/guide/' },
          { text: '入门教程', link: '/guide/tutorial' },
          { text: '功能特性', link: '/guide/features' },
          { text: '生产部署', link: '/guide/deploy' },
          { text: 'Worker Runtime Image', link: '/guide/worker-runtime-image' },
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
          { text: '跨仓协作', link: '/ws-cross-repo-collaboration' },
        ],
      },
      {
        text: '旧版文档',
        collapsed: true,
        items: [
          { text: 'v0.5.x 入口', link: '/v0.5.x/' },
          { text: 'v0.5.x 教程', link: '/v0.5.x/guide/tutorial' },
          { text: 'v0.5.x Broker', link: '/v0.5.x/core/broker' },
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
  },
})
