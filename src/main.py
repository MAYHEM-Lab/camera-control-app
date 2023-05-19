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

import socket
import threading
import csv
from datetime import datetime
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
    max_amiga_speed = StringProperty("???")
    amiga_rate = StringProperty("???")
    pi_request = StringProperty("???")

    def __init__(self, address: str, camera_port: int, canbus_port: int, stream_every_n: int) -> None:
        super().__init__()
        # This is were we decalre variables and instantions of stuff
        self.address: str = address
        self.camera_port: int = camera_port
        self.canbus_port: int = canbus_port
        self.stream_every_n: int = stream_every_n
        
        # Received values
        self.amiga_tpdo1: AmigaTpdo1 = AmigaTpdo1()
        self.amiga_vel = 0
        # Parameters
        self.max_speed: float = 0.15
        self.max_angular_rate: float = 1.0
        self.direction = 1
        self.speed = self.max_speed
        self.QR_img = None
        self.move = 0 

        self.connected = False # BT Connection

        self.current_tag = -1
        self.recent_tag = None

        self.zero_ticks = 0
        self.passes = 0

        self.image_decoder = TurboJPEG()
        self.tasks: List[asyncio.Task] = []

        

        #ip and port of servr  
        #by default http server port is 80  
        self.server_address = ('0.0.0.0', 9000) 
        self.out_socks = [] 
        # self.possible_conn = 1

        self.log_file = None
        self.log_writer = None

        # date_stamp = today.strftime("%m_%d_%y")
        # fname = '../logs/gort_' + date_stamp  + '.csv'
        # try:
        #     self.log_file = open(fname, mode='a')

        #     fieldnames = ['time', 'node', 'data']
        #     self.log_writer = csv.DictWriter(self.log_file, fieldnames=fieldnames)
        #     if (os.path.getsize(fname) <= 0):
        #         self.log_writer.writeheader()
        # except:
        #     print("Error in creating and opening log")

    def build(self):
        return Builder.load_file("res/main.kv")

    def on_exit_btn(self) -> None:
        """Kills the running kivy application."""
        # self.in_sock.close()
        for sock in self.out_socks:
            sock[0].close()
        App.get_running_app().stop()
    
    def on_minus_btn(self) -> None:
        if self.max_speed == 0.04:
            return
        else:
            self.max_speed -= 0.01
            if self.max_speed < 0.04:
                self.max_speed = 0.04
            self.speed = self.max_speed
    
    def on_plus_btn(self) -> None:
        if self.max_speed == 0.25:
            return
        else:
            self.max_speed += 0.01
            if self.max_speed > 0.15:
                self.max_speed = 0.15
            self.speed = self.max_speed

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

        # Server Task
        # threading.Thread(target=self.pi_server).start()
        self.tasks.append(asyncio.ensure_future(self.pi_server()))
        self.tasks.append(asyncio.ensure_future(self.handle_amiga_state()))
        self.tasks.append(asyncio.ensure_future(self.handle_UI()))

        return await asyncio.gather(run_wrapper(), *self.tasks)

    async def handle_amiga_state(self):
        while self.root is None:
            await asyncio.sleep(0.01)
        while True:
            await asyncio.sleep(10)
            if self.amiga_state == "AUTO_ACTIVE" and (not self.connected) :
                self.move = 1
            else:
                self.move = 0
    
    async def handle_UI(self):
        while self.root is None:
            await asyncio.sleep(0.01)
        while True:
            await asyncio.sleep(2)
            self.max_amiga_speed = f'{self.max_speed:5.3f}'
            if self.connected:
                self.root.ids["bt_conn"].source = "assets/BT_LOGO.png"
                self.root.ids["laser"].source = "assets/Pan_Green_Circle.png"   
                
            else:
                self.root.ids["laser"].source = "assets/red_circle_2.png"
                self.root.ids["bt_conn"].source = "assets/TRANS_BC_DOWN.png"
                self.root.ids["node_1"].source = "assets/red_circle_2.png"
                self.root.ids["node_2"].source = "assets/red_circle_2.png"
                if self.move:
                    self.root.ids["transition"].source = "assets/DOUBLE_ARROW_GREEN.png"
                else:
                    self.root.ids["transition"].source = "assets/DOUBLE_ARROW2.png"
                    if(self.current_tag != -1):
                        if (self.current_tag != self.recent_tag): # We are at our target
                            if self.current_tag == 1 :
                                self.root.ids["node_2"].source = "assets/Pan_Green_Circle.png"
                                self.root.ids["node_1"].source = "assets/red_circle_2.png"
                            elif self.current_tag == 0:
                                self.root.ids["node_2"].source = "assets/red_circle_2.png"
                                self.root.ids["node_1"].source = "assets/Pan_Green_Circle.png"

    async def handle_client(self, reader, writer):
        request = None
        while request != 'quit':
            request = (await reader.read(255)).decode('utf8')
            request = str(request)            
            if request is not None:
                self.pi_request = request
                if request.startswith('Z'):
                    if len(request.split(" ")) == 3:
                        
                        zone, proximity, tag = (request.split(" "))
                        self.current_tag = int(tag)
                        if self.current_tag != self.recent_tag:
                            self.speed = 0.04
                            self.direction = -1 if int(zone.count('+')) else 1
                            if zone[-1] == '0' or (self.passes == 6):
                                self.zero_ticks += 1
                                self.move = 0
                                if self.passes < 6:
                                    self.passes += 1
                                await asyncio.sleep(0.01)
                                # print("Here")
                                if (not self.connected) and (self.zero_ticks == 100) and (self.amiga_tpdo1.meas_speed < 0.025):
                                    # if self.current_tag != 1:
                                    reply = f"Connect {self.current_tag}"
                                    self.recent_tag = self.current_tag
                                    writer.write(reply.encode('utf8'))
                                    self.connected = True

                            else:
                                self.zero_ticks = 0
                                self.move = 1

                                await asyncio.sleep(0.01)
                elif request.startswith("DONE"):
                    self.connected = False
                    reply = f"Disconnect {self.current_tag}"
                    self.direction = -1 if (self.current_tag) else 1
                    self.current_tag = None

                    # self.log_writer.close()
                    self.log_file.close()
                    self.log_file = None
                    self.log_writer = None

                    writer.write(reply.encode('utf8'))
                    self.move = 1
                    self.speed = self.max_speed
                    self.passes = 0
                    # await asyncio.sleep(0.5)
                    # self.move = 0
                elif request.startswith("FILE"): # Save Data here into a CSV
                    # print(request)
                    stamp = str(datetime.now()).split(' ')
                    date_stamp = stamp[0] + '_' + stamp[1]
                    fname = '/data/home/amiga/logs/gort_' + date_stamp + '_node' + str(self.current_tag)  + '.csv'
                    try:
                        self.log_file = open(fname, mode='a')

                        fieldnames = ['time', 'data']
                        self.log_writer = csv.DictWriter(self.log_file, fieldnames=fieldnames)
                        if (os.path.getsize(fname) <= 0):
                            self.log_writer.writeheader()
                    except Exception as e:
                        print("Error in creating and opening log", e)
                else:
                    try:
                        data = request.split(';')
                        message_data = ""
                        for i in range(len(data[1:])):
                            message_data += str(data[i+1]).strip()
                            if (i+1 != (len(data[1:]))):
                                message_data += ";"
                        new_row = {"time": data[0], "data": message_data}
                        self.log_writer.writerow(new_row)
                    except Exception as e:
                        print("Error in writing to log", e)
            
            await writer.drain()
        writer.close()

    async def pi_server(self):
        self.server = await asyncio.start_server(self.handle_client, self.server_address[0], self.server_address[1], reuse_address=True)

                
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
            for view_name in ["rgb"]:
            #     # Skip if view_name was not included in frame
                try:
            #         # print(view_name, "Added frame")
            #         # Decode the image and render it in the correct kivy texture
                    img = self.image_decoder.decode(
                        getattr(frame, view_name).image_data
                    )

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
                    self.root.ids["right"].texture = texture
                except Exception as e:
                    print(e)
            # self.counter += 1

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
        at the specified period (recommended 50hz)."""
        while self.root is None:
            await asyncio.sleep(0.01)
        while True:
            # print("Message sent", self.move)
            msg: canbus_pb2.RawCanbusMessage = make_amiga_rpdo1_proto(
                state_req=AmigaControlState.STATE_AUTO_ACTIVE,
                cmd_speed=(self.speed * self.move * self.direction),
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

    # Open data file for this run

    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(QR_Control(args.address, args.camera_port, args.canbus_port, args.stream_every_n).app_func())
    except asyncio.CancelledError:
        pass
    loop.close()
