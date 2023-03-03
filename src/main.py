# Copyright (c) farm-ng, inc. Amiga Development Kit License, Version 0.1
import argparse
import asyncio
import os
from typing import List

from camera_control.simple_qr import find_qr

import grpc
from farm_ng.oak import oak_pb2
from farm_ng.oak.camera_client import OakCameraClient
from farm_ng.service import service_pb2
from farm_ng.service.service_client import ClientConfig
from turbojpeg import TurboJPEG
from PIL import Image 
import cv2

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



class CameraControlApp(App):
    """Base class for the main Kivy app."""

    def __init__(self, address: str, port: int, stream_every_n: int) -> None:
        super().__init__()
        # This is were we decalre variables and instantions of stuff
        self.address = address
        self.port = port
        self.stream_every_n = stream_every_n
        
        self.response_stream = None
        self.image_decoder = TurboJPEG()
        self.tasks: List[asyncio.Task] = []

        self.qr_model = cv2.QRCodeDetector()
        self.new_data = 0
        self.counter = 0
        self.QR_img = None

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
            for task in self.tasks:
                task.cancel()

        # configure the camera client
        config = ClientConfig(address=self.address, port=self.port)
        client = OakCameraClient(config)

       # Stream camera frames
        self.tasks.append(asyncio.ensure_future(self.template_function(client)))
        # self.tasks.append(asyncio.ensure_future(self.recieve_stream_frame(client)))
        self.tasks.append(asyncio.ensure_future(self.detect_QR()))

        return await asyncio.gather(run_wrapper(), *self.tasks)


    async def detect_QR(self) -> None:

        while self.root is None:
            await asyncio.sleep(0.01)

        # print("Help me")
        if(self.new_data and (self.QR_img is not None)):

            decodedText, points = self.qr_model.detect(self.QR_img) # make these class memebr variables

            # print(points)
            # Make the points array an array of ints (.astype(int)) to draw lines
            if points is not None:

                points = points[0].astype(int)
                # print(points)
                nrOfPoints = len(points) # Is actually of shape [[[][][][]]] so use points[0] as actual array of points
            
                for i in range(nrOfPoints):
                    nextPointIndex = (i+1) % nrOfPoints
                    cv2.line(self.QR_img, tuple(points[i]), tuple(points[nextPointIndex]), (255,0,0), 5)      

                qr_texture = Texture.create(
                size=(self.QR_img.shape[1], self.QR_img.shape[0]), icolorfmt="bgr"
                )
            
                qr_texture.flip_vertical()
                qr_texture.blit_buffer(
                    self.QR_img.tobytes(),
                    colorfmt="bgr",
                    bufferfmt="ubyte",
                    mipmap_generation=False,
                )

                self.root.ids["qr"].texture = qr_texture
            else:
                print("QR code not detected")
            
            self.new_data = 0
                

    
    async def template_function(self, client: OakCameraClient) -> None:
        """Placeholder forever loop."""
        while self.root is None:
            await asyncio.sleep(0.01)

        # response_stream = None
        while True:
             # check the state of the service
            state = await client.get_state()

            if state.value not in [
                service_pb2.ServiceState.IDLE,
                service_pb2.ServiceState.RUNNING,
            ]:
                # Cancel existing stream, if it exists
                if self.response_stream is not None:
                    self.response_stream.cancel()
                    self.response_stream = None
                print("Camera service is not streaming or ready to stream")
                await asyncio.sleep(0.1)
                continue

            # Create the stream
            if self.response_stream is None:
                self.response_stream = client.stream_frames(every_n=self.stream_every_n)

            try:
                # try/except so app doesn't crash on killed service
                response: oak_pb2.StreamFramesReply = await self.response_stream.read()
                assert response and response != grpc.aio.EOF, "End of stream"
            except Exception as e:
                print(e)
                self.response_stream.cancel()
                self.response_stream = None
                continue

            # get the sync frame
            frame: oak_pb2.OakSyncFrame = response.frame

            # get image and show
            for view_name in ["rgb", "disparity", "left", "right"]:
                # Skip if view_name was not included in frame
                try:
                    # print(view_name, "Added frame")
                    # Decode the image and render it in the correct kivy texture
                    img = self.image_decoder.decode(
                        getattr(frame, view_name).image_data
                    )
                    if((view_name == "right") and (self.counter == 10)): # Save every 10th frame
                        self.counter = 0
                        self.QR_img = img
                        self.new_data = 1
                        await self.detect_QR()
                        # print("New QR data")

                    # IMG 
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
                    
                    self.root.ids[view_name].texture = texture

                except Exception as e:
                    print(e)
            self.counter += 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="camera-control-app")

    # Add additional command line arguments here

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
        loop.run_until_complete(CameraControlApp(args.address, args.port, args.stream_every_n).app_func())
    except asyncio.CancelledError:
        pass
    loop.close()
