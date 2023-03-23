# Copyright (c) farm-ng, inc. Amiga Development Kit License, Version 0.1
import argparse
import asyncio
import os
from typing import List
from typing import Optional

import grpc
from farm_ng.canbus import canbus_pb2
from farm_ng.canbus.canbus_client import CanbusClient
from farm_ng.canbus.packet import AmigaControlState
from farm_ng.canbus.packet import AmigaTpdo1
from farm_ng.canbus.packet import make_amiga_rpdo1_proto
from farm_ng.canbus.packet import parse_amiga_tpdo1_proto
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
from kivy.properties import StringProperty  # noqa: E402


class QR_Control(App):
    """Base class for the main Kivy app."""
    amiga_state = StringProperty("???")
    amiga_speed = StringProperty("???")
    amiga_rate = StringProperty("???")

    def __init__(self, address: str, camera_port: int, canbus_port: int, stream_every_n: int) -> None:
        super().__init__()
        # This is were we decalre variables and instantions of stuff
        self.address: str = address
        self.camera_port: int = camera_port
        self.canbus_port: int = canbus_port
        self.stream_every_n: int = stream_every_n
        
        # Received values
        self.amiga_tpdo1: AmigaTpdo1 = AmigaTpdo1()

        # Parameters
        self.max_speed: float = 1.0
        self.max_angular_rate: float = 1.0

        self.image_decoder = TurboJPEG()
        self.tasks: List[asyncio.Task] = []

        self.qr_model = cv2.QRCodeDetector()
        self.new_data = 0
        self.counter = 0
        self.QR_img = None
        self.move = 0

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
        camera_config = ClientConfig(address=self.address, port=self.camera_port)
        camera_client = OakCameraClient(camera_config)

        # configure the canbus client
        canbus_config: ClientConfig = ClientConfig(
            address=self.address, port=self.canbus_port
        )
        canbus_client: CanbusClient = CanbusClient(canbus_config)

        # Camera Task
        self.tasks.append(asyncio.ensure_future(self.stream_camera(camera_client)))

        # canbus Tasks
        # Canbus task(s)
        self.tasks.append(
            asyncio.ensure_future(self.stream_canbus(canbus_client))
        )
        self.tasks.append(
            asyncio.ensure_future(self.send_can_msgs(canbus_client))
        )

        # QR Detection Task
        # self.tasks.append(asyncio.ensure_future(self.detect_QR()))

        return await asyncio.gather(run_wrapper(), *self.tasks)

    async def classify(self):
        return self.qr_model.detectAndDecode(self.QR_img)

    async def detect_QR(self) -> None:

        while self.root is None:
            await asyncio.sleep(0.01)

        # print("Help me")
        if(self.new_data and (self.QR_img is not None)):

            decodedText, points, qr =  await self.classify() 

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
                # print(decodedText, str(decodedText))
                if(type(decodedText) == str and decodedText is not None):
                    self.root.ids["qr_text"].text = str(decodedText)
                    self.move = 1
                self.root.ids["qr"].texture = qr_texture
            else:
                print("QR code not detected")
                self.move = 0
            
            self.new_data = 0
                
    async def stream_canbus(self, client: CanbusClient) -> None:
        """This task:

        - listens to the canbus client's stream
        - filters for AmigaTpdo1 messages
        - extracts useful values from AmigaTpdo1 messages
        """
        while self.root is None:
            await asyncio.sleep(0.01)

        response_stream = None

        while True:
            # check the state of the service
            state = await client.get_state()

            if state.value not in [
                service_pb2.ServiceState.IDLE,
                service_pb2.ServiceState.RUNNING,
            ]:
                if response_stream is not None:
                    response_stream.cancel()
                    response_stream = None

                print("Canbus service is not streaming or ready to stream")
                await asyncio.sleep(0.1)
                continue

            if (
                response_stream is None
                and state.value != service_pb2.ServiceState.UNAVAILABLE
            ):
                # get the streaming object
                response_stream = client.stream()

            try:
                # try/except so app doesn't crash on killed service
                response: canbus_pb2.StreamCanbusReply = await response_stream.read()
                assert response and response != grpc.aio.EOF, "End of stream"
            except Exception as e:
                print(e)
                response_stream.cancel()
                response_stream = None
                continue

            for proto in response.messages.messages:
                amiga_tpdo1: Optional[AmigaTpdo1] = parse_amiga_tpdo1_proto(proto)
                if amiga_tpdo1:
                    # Store the value for possible other uses
                    self.amiga_tpdo1 = amiga_tpdo1

                    # Update the Label values as they are received
                    self.amiga_state = AmigaControlState(amiga_tpdo1.state).name[6:]
                    self.amiga_speed = str(amiga_tpdo1.meas_speed)
                    self.amiga_rate = str(amiga_tpdo1.meas_ang_rate)
    
    async def stream_camera(self, client: OakCameraClient) -> None:
        """Placeholder forever loop."""
        while self.root is None:
            await asyncio.sleep(0.01)

        response_stream = None
        while True:
             # check the state of the service
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

            # Create the stream
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

            # get image and show
            for view_name in ["rgb", "disparity", "left", "right"]:
                # Skip if view_name was not included in frame
                try:
                    # print(view_name, "Added frame")
                    # Decode the image and render it in the correct kivy texture
                    img = self.image_decoder.decode(
                        getattr(frame, view_name).image_data
                    )
                    if((view_name == "right") and (self.counter == 3)): # Save every 10th frame
                        self.counter = 0
                        self.QR_img = img
                        self.new_data = 1
                        # print("New QR Data")
                        await self.detect_QR()
                        # print("Processed or awaited?")

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

    async def send_can_msgs(self, client: CanbusClient) -> None:
        """This task ensures the canbus client sendCanbusMessage method has the pose_generator it will use to send
        messages on the CAN bus to control the Amiga robot."""
        while self.root is None:
            await asyncio.sleep(0.01)

        response_stream = None
        while True:
            # check the state of the service
            state = await client.get_state()

            # Wait for a running CAN bus service
            if state.value != service_pb2.ServiceState.RUNNING:
                # Cancel existing stream, if it exists
                if response_stream is not None:
                    response_stream.cancel()
                    response_stream = None
                print("Waiting for running canbus service...")
                await asyncio.sleep(0.1)
                continue

            if response_stream is None:
                print("Start sending CAN messages")
                response_stream = client.stub.sendCanbusMessage(self.pose_generator())

            try:
                async for response in response_stream:
                    # Sit in this loop and wait until canbus service reports back it is not sending
                    assert response.success
            except Exception as e:
                print(e)
                response_stream.cancel()
                response_stream = None
                continue

            await asyncio.sleep(0.1)

    async def pose_generator(self, period: float = 0.02):
        """The pose generator yields an AmigaRpdo1 (auto control command) for the canbus client to send on the bus
        at the specified period (recommended 50hz) based on the onscreen joystick position."""
        while self.root is None:
            await asyncio.sleep(0.01)

        while True:
            msg: canbus_pb2.RawCanbusMessage = make_amiga_rpdo1_proto(
                state_req=AmigaControlState.STATE_AUTO_ACTIVE,
                cmd_speed=(-self.max_speed * 0.05 * self.move),
                cmd_ang_rate=0,
            )
            yield canbus_pb2.SendCanbusMessageRequest(message=msg)
            await asyncio.sleep(period)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="QR_Control")

    # Add additional command line arguments here

    parser.add_argument("--camera-port", type=int, required=True, help="The camera port.")
    parser.add_argument(
        "--address", type=str, default="localhost", help="The camera address"
    )
    parser.add_argument(
        "--stream-every-n", type=int, default=1, help="Streaming frequency"
    )
    parser.add_argument(
        "--canbus-port",
        type=int,
        required=True,
        help="The grpc port where the canbus service is running.",
    )
    args = parser.parse_args()

    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(QR_Control(args.address, args.camera_port, args.canbus_port, args.stream_every_n).app_func())
    except asyncio.CancelledError:
        pass
    loop.close()
