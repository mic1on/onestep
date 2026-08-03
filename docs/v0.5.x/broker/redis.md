---
outline: deep
title: Redis
---


## RedisBroker

`RedisBroker` 是基于 `Redis` 的[Stream](https://redis.io/docs/data-types/streams/) 实现的 `Broker`。

::: warning ⚠️版本
Redis只有5.0版本以上才支持Stream，如果需要使用RedisBroker，请确保Redis版本在5.0以上。
:::
::: details 为什么不使用Redis 发布订阅 (pub/sub)？
Redis 本身是有一个 Redis 发布订阅 (pub/sub) 来实现消息队列的功能，但它有个缺点就是消息无法持久化，如果出现网络断开、Redis 宕机等，消息就会被丢弃。

Redis Stream 提供了消息的持久化和主备复制功能，可以让任何客户端访问任何时刻的数据，并且能记住每一个客户端的访问位置，还能保证消息不丢失。
:::
