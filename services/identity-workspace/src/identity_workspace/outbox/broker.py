"""RabbitMQ test/deployment adapter: persistent, mandatory, confirmed delivery."""

import logging
from pathlib import Path
import ssl
from urllib.parse import urlsplit

import pika

from identity_workspace.config import ConfigurationError


class PublishFailure(RuntimeError):
    """Safe error category only; never includes broker diagnostics or a URL."""


def broker_url(settings) -> str:
    raw = None
    try:
        if settings.broker_url is not None and settings.broker_url_file is not None:
            raise ValueError()
        if settings.broker_url_file is not None:
            with Path(settings.broker_url_file).open("rb") as source:
                data = source.read(4097)
            if len(data) > 4096:
                raise ValueError()
            raw = data.decode("utf-8").removesuffix("\n").removesuffix("\r")
        elif settings.broker_url is not None:
            raw = settings.broker_url.get_secret_value()
        parts = urlsplit(raw or "")
        if (not raw or parts.scheme not in {"amqp", "amqps"} or not parts.hostname
            or not parts.username or not parts.password or parts.query or parts.fragment
            or any(c.isspace() or ord(c) < 32 for c in raw) or len(raw) > 4096
            or (parts.port is not None and not 1 <= parts.port <= 65535)
            or (settings.environment == "production" and parts.scheme != "amqps")):
            raise ValueError()
        return raw
    except (OSError, UnicodeError, ValueError):
        pass
    raise ConfigurationError("Invalid IDENTITY_BROKER_URL or IDENTITY_BROKER_URL_FILE; values omitted.") from None


class RabbitPublisher:
    def __init__(self, settings, *, connection_factory=pika.BlockingConnection):
        self._url = broker_url(settings)
        self.exchange = settings.broker_exchange
        self.connection_factory = connection_factory
        # Pika diagnostics may include connection/credential information. This
        # worker uses only its closed application logger for delivery failures.
        logging.getLogger("pika").disabled = True
        logging.getLogger("pika").propagate = False
        logging.getLogger("pika").addHandler(logging.NullHandler())
        for name in tuple(logging.Logger.manager.loggerDict):
            if name.startswith("pika."):
                logging.getLogger(name).disabled = True

    def publish(self, payload: dict, body: bytes):
        connection = None
        failure = None
        try:
            parameters = pika.URLParameters(self._url)
            parameters.heartbeat = 5
            parameters.socket_timeout = 3
            parameters.stack_timeout = 8
            parameters.blocked_connection_timeout = 5
            parameters.connection_attempts = 1
            if parameters.ssl_options is not None:
                parameters.ssl_options = pika.SSLOptions(ssl.create_default_context(), parameters.host)
            connection = self.connection_factory(parameters)

            def deadline():
                raise TimeoutError()

            timer = connection.call_later(10, deadline)
            channel = connection.channel()
            channel.confirm_delivery()
            channel.basic_publish(
                exchange=self.exchange, routing_key=payload["type"], body=body, mandatory=True,
                properties=pika.BasicProperties(
                    content_type="application/json", content_encoding="utf-8", delivery_mode=2,
                    message_id=payload["id"], type=payload["type"], app_id="identity-workspace",
                    correlation_id=payload["request_id"],
                ),
            )
            connection.remove_timeout(timer)
        except pika.exceptions.UnroutableError:
            failure = "unroutable"
        except pika.exceptions.NackError:
            failure = "nack"
        except Exception:
            failure = "broker_unavailable"
        finally:
            if connection is not None:
                try:
                    if connection.is_open:
                        connection.call_later(5, lambda: (_ for _ in ()).throw(TimeoutError()))
                        connection.close()
                except Exception:
                    pass  # Close failure cannot undo a positive publish confirm.
        if failure is not None:
            raise PublishFailure(failure) from None
