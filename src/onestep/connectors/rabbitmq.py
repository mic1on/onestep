from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from onestep.envelope import Envelope
from onestep.resilience import ConnectorOperation, ConnectorOperationError, as_connector_operation_error

from .base import Delivery, Sink, Source
from .codec import decode_envelope, encode_envelope

try:  # pragma: no cover - optional dependency
    import aio_pika
except ImportError:  # pragma: no cover - optional dependency
    aio_pika = None


class RabbitMQDelivery(Delivery):
    def __init__(self, message: Any) -> None:
        super().__init__(decode_envelope(message.body))
        self._message = message

    async def ack(self) -> None:
        await self._message.ack()

    async def retry(self, *, delay_s: float | None = None) -> None:
        if delay_s:
            await asyncio.sleep(delay_s)
        await self._message.nack(requeue=True)

    async def fail(self, exc: Exception | None = None) -> None:
        await self._message.reject(requeue=False)


@dataclass
class RabbitMQConnector:
    url: str
    options: dict[str, Any] | None = None
    _connection: Any | None = field(default=None, init=False, repr=False)
    _ref_count: int = field(default=0, init=False, repr=False)
    _lock: asyncio.Lock | None = field(default=None, init=False, repr=False)
    _loop: asyncio.AbstractEventLoop | None = field(default=None, init=False, repr=False)

    def queue(
        self,
        name: str,
        *,
        routing_key: str | None = None,
        exchange: str | None = None,
        exchange_type: str = "direct",
        bind: bool = True,
        bind_arguments: dict[str, Any] | None = None,
        durable: bool = True,
        auto_delete: bool = False,
        exclusive: bool = False,
        arguments: dict[str, Any] | None = None,
        exchange_durable: bool | None = None,
        exchange_auto_delete: bool = False,
        exchange_arguments: dict[str, Any] | None = None,
        prefetch: int = 100,
        batch_size: int = 100,
        poll_interval_s: float = 1.0,
        publisher_confirms: bool = True,
        persistent: bool = True,
    ) -> "RabbitMQQueue":
        return RabbitMQQueue(
            connector=self,
            name=name,
            routing_key=routing_key or name,
            exchange_name=exchange,
            exchange_type=exchange_type,
            bind=bind,
            bind_arguments=bind_arguments or {},
            durable=durable,
            auto_delete=auto_delete,
            exclusive=exclusive,
            arguments=arguments or {},
            exchange_durable=durable if exchange_durable is None else exchange_durable,
            exchange_auto_delete=exchange_auto_delete,
            exchange_arguments=exchange_arguments or {},
            prefetch=prefetch,
            batch_size=batch_size,
            poll_interval_s=poll_interval_s,
            publisher_confirms=publisher_confirms,
            persistent=persistent,
        )

    async def close(self) -> None:
        lock = self._runtime_lock()
        async with lock:
            await self._close_connection_locked()

    def _driver(self) -> Any:
        if aio_pika is None:
            raise RuntimeError("RabbitMQConnector requires aio-pika. Install onestep[rabbitmq].")
        return aio_pika

    def _runtime_lock(self) -> asyncio.Lock:
        current_loop = asyncio.get_running_loop()
        if self._lock is None or self._loop is not current_loop:
            self._lock = asyncio.Lock()
            self._loop = current_loop
            self._connection = None
            self._ref_count = 0
        return self._lock

    async def acquire(self) -> Any:
        lock = self._runtime_lock()
        async with lock:
            if self._connection is None or getattr(self._connection, "is_closed", False):
                self._connection = await self._driver().connect_robust(self.url, **(self.options or {}))
            self._ref_count += 1
            return self._connection

    async def release(self) -> None:
        lock = self._runtime_lock()
        async with lock:
            if self._ref_count > 0:
                self._ref_count -= 1
            if self._ref_count == 0:
                await self._close_connection_locked()

    async def _close_connection_locked(self) -> None:
        if self._connection is None:
            return
        connection = self._connection
        self._connection = None
        await connection.close()


class RabbitMQQueue(Source, Sink):
    def __init__(
        self,
        *,
        connector: RabbitMQConnector,
        name: str,
        routing_key: str,
        exchange_name: str | None,
        exchange_type: str,
        bind: bool,
        bind_arguments: dict[str, Any],
        durable: bool,
        auto_delete: bool,
        exclusive: bool,
        arguments: dict[str, Any],
        exchange_durable: bool,
        exchange_auto_delete: bool,
        exchange_arguments: dict[str, Any],
        prefetch: int,
        batch_size: int,
        poll_interval_s: float,
        publisher_confirms: bool,
        persistent: bool,
    ) -> None:
        Source.__init__(self, name)
        Sink.__init__(self, name)
        self.connector = connector
        self.routing_key = routing_key
        self.exchange_name = exchange_name
        self.exchange_type = exchange_type
        self.bind = bind
        self.bind_arguments = bind_arguments
        self.durable = durable
        self.auto_delete = auto_delete
        self.exclusive = exclusive
        self.arguments = arguments
        self.exchange_durable = exchange_durable
        self.exchange_auto_delete = exchange_auto_delete
        self.exchange_arguments = exchange_arguments
        self.prefetch = prefetch
        self.batch_size = batch_size
        self.poll_interval_s = poll_interval_s
        self.publisher_confirms = publisher_confirms
        self.persistent = persistent
        self._receive_channel: Any | None = None
        self._publish_channel: Any | None = None
        self._queue: Any | None = None
        self._exchange: Any | None = None
        self._opened = False

    async def open(self) -> None:
        if self._opened:
            return
        acquired = False
        try:
            connection = await self.connector.acquire()
            acquired = True
            self._receive_channel = await connection.channel()
            await self._receive_channel.set_qos(prefetch_count=self.prefetch)
            self._publish_channel = await connection.channel(publisher_confirms=self.publisher_confirms)
            self._queue = await self._receive_channel.declare_queue(
                self.name,
                durable=self.durable,
                auto_delete=self.auto_delete,
                exclusive=self.exclusive,
                arguments=self.arguments,
            )
            if self.exchange_name:
                receive_exchange = await self._receive_channel.declare_exchange(
                    self.exchange_name,
                    self.exchange_type,
                    durable=self.exchange_durable,
                    auto_delete=self.exchange_auto_delete,
                    arguments=self.exchange_arguments,
                )
                if self.bind:
                    await self._queue.bind(
                        receive_exchange,
                        routing_key=self.routing_key,
                        arguments=self.bind_arguments or None,
                    )
                self._exchange = await self._publish_channel.declare_exchange(
                    self.exchange_name,
                    self.exchange_type,
                    durable=self.exchange_durable,
                    auto_delete=self.exchange_auto_delete,
                    arguments=self.exchange_arguments,
                )
            else:
                self._exchange = self._publish_channel.default_exchange
            self._opened = True
        except Exception as exc:
            if acquired:
                await self._reset_transport_state(release_connection=True)
            connector_error = as_connector_operation_error(
                backend="rabbitmq",
                operation=ConnectorOperation.OPEN,
                exc=exc,
                source_name=self.name,
                retry_delay_s=self.poll_interval_s,
            )
            if connector_error is None:
                raise
            raise connector_error from exc

    async def close(self) -> None:
        if not self._opened:
            return
        try:
            if self._publish_channel is not None:
                await self._publish_channel.close()
            if self._receive_channel is not None:
                await self._receive_channel.close()
        finally:
            self._publish_channel = None
            self._receive_channel = None
            self._queue = None
            self._exchange = None
            self._opened = False
            await self.connector.release()

    async def fetch(self, limit: int) -> list[Delivery]:
        try:
            await self.open()
            if self._queue is None:
                return []
            deliveries: list[Delivery] = []
            fetch_limit = max(1, min(limit, self.batch_size))
            for index in range(fetch_limit):
                timeout = self.poll_interval_s if index == 0 else 0
                try:
                    message = await self._queue.get(fail=False, timeout=timeout)
                except asyncio.TimeoutError:
                    break
                if message is None:
                    break
                deliveries.append(RabbitMQDelivery(message))
            return deliveries
        except ConnectorOperationError:
            raise
        except Exception as exc:
            connector_error = as_connector_operation_error(
                backend="rabbitmq",
                operation=ConnectorOperation.FETCH,
                exc=exc,
                source_name=self.name,
                retry_delay_s=self.poll_interval_s,
            )
            if connector_error is None:
                raise
            await self._reset_transport_state(release_connection=True)
            raise connector_error from exc

    async def send(self, envelope: Envelope) -> None:
        try:
            await self.open()
            if self._exchange is None:
                raise RuntimeError("RabbitMQ queue is not open")
            driver = self.connector._driver()
            message_kwargs = {
                "body": encode_envelope(envelope),
                "content_type": "application/json",
            }
            delivery_mode = getattr(getattr(driver, "DeliveryMode", None), "PERSISTENT", None)
            if self.persistent and delivery_mode is not None:
                message_kwargs["delivery_mode"] = delivery_mode
            message = driver.Message(**message_kwargs)
            await self._exchange.publish(message, routing_key=self.routing_key)
        except ConnectorOperationError:
            raise
        except Exception as exc:
            connector_error = as_connector_operation_error(
                backend="rabbitmq",
                operation=ConnectorOperation.SEND,
                exc=exc,
                source_name=self.name,
                retry_delay_s=self.poll_interval_s,
            )
            if connector_error is None:
                raise
            await self._reset_transport_state(release_connection=True)
            raise connector_error from exc

    async def _reset_transport_state(self, *, release_connection: bool) -> None:
        try:
            if self._publish_channel is not None:
                await self._publish_channel.close()
            if self._receive_channel is not None:
                await self._receive_channel.close()
        except Exception:
            pass
        finally:
            self._publish_channel = None
            self._receive_channel = None
            self._queue = None
            self._exchange = None
            self._opened = False
            if release_connection:
                await self.connector.release()
