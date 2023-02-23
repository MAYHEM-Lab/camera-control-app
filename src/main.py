# Copyright (c) farm-ng, inc. Amiga Development Kit License, Version 0.1
import argparse
import asyncio
import os
from typing import List

from simple_qr import ops
from simple_qr import simple_qr

import grpc
from farm_ng.oak import oak_pb2
from farm_ng.oak.camera_client import OakCameraClient
from farm_ng.service import service_pb2
from farm_ng.service.service_client import ClientConfig
from turbojpeg import TurboJPEG

# import internal libs

# Must come before kivy imports
os.environ["KIVY_NO_ARGS"] = "1"

# gui configs must go before any other kivy import
from kivy.config import Config  # noreorder # noqa: E402

Config.set("graphics", "resizable", False)
Config.set("graphics", "width", "1280")
Config.set("graphics", "height", "800")
Config.set("graphics", "fullscreen", "false")
Config.set("input", "mouse", "mouse,disable_on_activity")
Config.set("kivy", "keyboard_mode", "systemanddock")

# kivy imports
from kivy.app import App  # noqa: E402
from kivy.lang.builder import Builder  # noqa: E402
from kivy.graphics.texture import Texture  # noqa: E402


class SimpleQR(App):
    """Base class for the main Kivy app."""

    def __init__(self, address: str, port: int, stream_every_n: int) -> None:
        super().__init__()

        self.address = address
        self.port = port
        self.stream_every_n = stream_every_n

        self.image_decoder = TurboJPEG()

        self.async_tasks: List[asyncio.Task] = []

    def build(self):
        return Builder.load_file("res/main.kv")

    def on_exit_btn(self) -> None:
        """Kills the running kivy application."""
        App.get_running_app().stop()

    async def app_func(self):
        async def run_wrapper():
            # we don't actually need to set asyncio as the lib because it is
            # the default, but it doesn't hurt to be explicit
            await self.async_run(async_lib="asyncio")
            for task in self.async_tasks:
                task.cancel()


        #config camera client
        config = ClientConfig(address=self.address, port=self.port)
        client = OakCameraClient(config)

        # Placeholder task
        self.async_tasks.append(asyncio.ensure_future(self.stream_qr_camera(client)))

        return await asyncio.gather(run_wrapper(), *self.async_tasks)

    async def stream_qr_camera(self, client: OakCameraClient) -> None:
        while self.root is None:
            await asyncio.sleep(0.01)

        response_stream = None

        while True:
            state = await client.get_state()

            if state.value not in [
                service_pb2.ServiceState.IDLE,
                service_pb2.ServiceState.RUNNING,
            ]:
                # Cancel existing stream, if it exists
                if response_stream is not None:
                    response_stream.cancel()
                    response_stream = None
                print("Camera service is not streaming or ready to stream")
                await asyncio.sleep(0.1)
                continue

            if response_stream is None:
                response_stream = client.stream_frames(every_n=self.stream_every_n)

            try:
                # try/except so app doesn't crash on killed service
                response: oak_pb2.StreamFramesReply = await response_stream.read()
                assert response and response != grpc.aio.EOF, "End of stream"
            except Exception as e:
                print(e)
                response_stream.cancel()
                response_stream = None
                continue

            # get the sync frame
            frame: oak_pb2.OakSyncFrame = response.frame

            try:
                img = self.image_decoder.decode(
                    getattr(frame, "rgb").image_data
                )

                # simple_qr.find_qr(img)

                texture = Texture.create(
                    size=(img.shape[1], img.shape[0]), icolorfmt="bgr"
                )
                texture.flip_vertical()
                texture.blit_buffer(
                    img.tobytes(),
                    colorfmt="bgr",
                    bufferfmt="ubyte",
                    mipmap_generation=False,
                )
                self.root.ids["rgb"].texture = texture

            except Exception as e:
                print(e)
                
            # get image and show



if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="camera_control_app")
    parser.add_argument("--port", type=int, required=True, help="The camera port.")
    parser.add_argument(
        "--address", type=str, default="localhost", help="The camera address"
    )
    parser.add_argument(
        "--stream-every-n", type=int, default=1, help="Streaming frequency"
    )
    args = parser.parse_args()

    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(
            SimpleQR(args.address, args.port, args.stream_every_n).app_func()
        )
    except asyncio.CancelledError:
        pass
    loop.close()
