import secrets
from typing import Self

import grpc

from .._rpc import controller_pb2_grpc as rpc
from .config import RpcServerConfig
from .controller import RpcController, EventHandler


class RpcServer:
    def __init__(self,
                 server: grpc.aio.Server,
                 controller: RpcController,
                 *,
                 token: str,
                 port: int,
                 startup_timeout: float | None = None,
                 stop_timeout: float | None = None,
                 on_event: EventHandler):
        self._server = server
        self._controller = controller
        self._on_event = on_event
        self._token = token
        self._endpoint = f"127.0.0.1:{port}"
        self._startup_timeout = startup_timeout
        self._stop_timeout = stop_timeout
        self._closed = False

    @staticmethod
    def create_grpc_server(max_message_bytes: int) -> grpc.aio.Server:
        return grpc.aio.server(
            options=(
                ("grpc.max_send_message_length", max_message_bytes),
                ("grpc.max_receive_message_length", max_message_bytes),
                ("grpc.so_reuseport", 0),
            )
        )

    @classmethod
    async def create(cls, settings: RpcServerConfig, *, on_event: EventHandler) -> Self:
        token = secrets.token_urlsafe(32)
        server = cls.create_grpc_server(settings.max_message_bytes)
        rpc_controller = RpcController(token, settings, on_event)
        rpc.add_ControllerServicer_to_server(rpc_controller, server)

        try:
            port = server.add_insecure_port("127.0.0.1:0")
            if not port:
                raise RuntimeError("could not bind controller port")
            await server.start()
        except BaseException:
            await server.stop(None)
            raise

        return cls(server,
                   rpc_controller,
                   token=token,
                   port=port,
                   startup_timeout=settings.startup_timeout,
                   stop_timeout=settings.stop_timeout,
                   on_event=on_event)

    @property
    def token(self) -> str:
        return self._token

    @property
    def endpoint(self) -> str:
        return self._endpoint

    async def wait_ready(self):
        if self._closed: raise RuntimeError("server already closed")
        await self._controller.wait_ready(self._startup_timeout)

    async def aclose(self):
        if self._closed: return
        self._closed = True
        try:
            await self._controller.stop(self._stop_timeout)
        finally:
            await self._server.stop(None)
