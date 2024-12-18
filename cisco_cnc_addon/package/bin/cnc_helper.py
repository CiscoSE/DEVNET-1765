# Author: Steven Barth <stbarth@cisco.com>
# Copyright (c) 2024 Cisco and/or its affiliates.
#
# This software is licensed to you under the terms of the Cisco Sample
# Code License, Version 1.1 (the "License"). You may obtain a copy of the
# License at
#
#               https://developer.cisco.com/docs/licenses
#
# All use of the material herein must be in accordance with the terms of
# the License. All rights not expressly granted by the License are
# reserved. Unless required by applicable law or agreed to separately in
# writing, software distributed under the License is distributed on an "AS
# IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
# or implied.

import asyncio
import aiohttp
import hashlib
import json
import logging
import os
import pickle

import import_declare_test # type: ignore
from datetime import datetime
from solnlib import conf_manager, log
from splunklib import modularinput as smi


ADDON_NAME = "cisco_cnc_addon"

def logger_for_input(input_name: str) -> logging.Logger:
    return log.Logs().get_logger(f"{ADDON_NAME.lower()}_{input_name}")

async def exit_on_ppid_change() -> None:
    ppid: int = os.getppid()
    while ppid == os.getppid():
        await asyncio.sleep(1)
    asyncio.get_running_loop().stop()

def validate_input(definition: smi.ValidationDefinition):
    return

def stream_events(inputs: smi.InputDefinition, event_writer: smi.EventWriter):
    # inputs.inputs is a Python dictionary object like:
    # {
    #   "cnc://<input_name>": {
    #     "account": "<account_name>",
    #     "disabled": "0",
    #     "host": "$decideOnStartup",
    #     "index": "<index_name>",
    #     "interval": "<interval_value>",
    #     "python.version": "python3",
    #   },
    # }
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(exit_on_ppid_change())

    clients: list[CrossworkClient] = []
    for input_name, input_item in inputs.inputs.items():
        name = input_name.split("/")[-1]
        logger = logger_for_input(name)
        try:
            session_key = inputs.metadata["session_key"]
            log_level = conf_manager.get_log_level(
                logger=logger,
                session_key=session_key,
                app_name=ADDON_NAME,
                conf_name="cisco_cnc_addon_settings",
            )
            logger.setLevel(log_level)
            log.modular_input_start(logger, name)

            url = input_item.get("url")
            username = input_item.get("username")
            password = input_item.get("password")
            verify = input_item.get("verify_tls") is not None 
            index = input_item.get("index")
            interval = float(input_item.get("interval"))
            
            cnc = CrossworkClient(input_name, url, username, password, verify, logger, event_writer, index)
            clients.append(cnc)
            loop.create_task(cnc.start_collect_topology(interval))
            loop.create_task(cnc.start_collect_inventory(interval))
        except Exception as e:
            log.log_exception(logger, e, "cisco_cnc_addon error", msg_before="Exception raised while ingesting data for cisco_cnc_addon: ")

    try:
        loop.run_forever()
    except KeyboardInterrupt:  # pragma: no branch
        pass

    for cnc in clients:
        loop.run_until_complete(cnc.stop())

    loop.close()


class CrossworkClient:
    _name: str
    _ticket: str
    _token: str
    _ssl: bool
    _uri: str
    _username: str
    _password: str
    _logger: logging.Logger
    _writer: smi.EventWriter
    _index: str
    _published: int
    _tasks: list[asyncio.Task]
    _memory: dict

    def __init__(self, name: str, uri: str, username: str, password: str, tls_verify: bool, logger: logging.Logger, writer: smi.EventWriter, index: str):
        self._name = name
        self._uri = uri
        self._username = username
        self._password = password
        self._ticket = None
        self._token = ""
        self._ssl = tls_verify
        self._logger = logger
        self._writer = writer
        self._index = index
        self._published = 0
        self._tasks = []
        self._memory = {}

    def publish(self, sourcetype: str, data: dict, host: str, fields: dict = {}, time: float = None) -> None:
        for k, v in fields.items():
            data[k] = v
        
        event = smi.Event(
            index = self._index,
            sourcetype = sourcetype,
            data = json.dumps(data, ensure_ascii=False, default=str),
            host = host,
            time = time
        )

        if time is not None:
            hash = hashlib.sha256(pickle.dumps(event)).digest()
            if hash in self._memory:
                return

            self._memory[hash] = time

        self._writer.write_event(event)
        self._published += 1

    def log_published(self, sourcetype: str) -> None:
        if self._published > 0:
            log.events_ingested(
                self._logger,
                self._name,
                sourcetype,
                self._published,
                self._index,
            )
        self._published = 0

    async def login(self) -> None:
        if self._ticket is not None:
            await self.logout()

        async with aiohttp.ClientSession() as session:
            uri = self._uri + "/crosswork/sso/v1/tickets"
            headers = {"Accept": "text/plain"}
            data = {"username": self._username, "password": self._password}
            async with session.post(uri, data = data, headers = headers, ssl = self._ssl) as resp:
                if resp.status == 201:
                    self._ticket = await resp.text()
                else:
                    raise Exception("Failed to get ticket: "+str(resp.status))

            uri = self._uri + "/crosswork/sso/v1/tickets/" + self._ticket
            data = {"service": self._uri+"/app-dashboard"}
            async with session.post(uri, data = data, headers = headers, ssl = self._ssl) as resp:
                if resp.status == 200:
                    self._token = "Bearer " + await resp.text()
                else:
                    raise Exception("Failed to get token: "+str(resp.status))

    async def logout(self) -> None:
        if self._ticket is None:
            return
        
        async with aiohttp.ClientSession() as session:
            uri = self._uri + "/crosswork/sso/v1/tickets/" + self._ticket
            headers = {"Authorization": self._token}
            async with session.delete(uri, headers = headers, ssl = self._ssl) as resp:
                self._ticket = None
                self._token = None

    async def request_json(self, method: str, location: str, json: dict = None) -> dict:
        async with aiohttp.ClientSession() as session:
            uri = self._uri + location
            headers = {
                "Authorization": self._token,
                "Accept": "application/json"
            }

            if json is not None:
                headers["Content-Type"] = "application/json"

            async with session.request(method, uri, headers = headers, ssl = self._ssl, json = json) as resp:
                if 200 <= resp.status <= 206:
                    self._logger.debug("Crosswork %s request to %s: %d", method, location, resp.status)
                    return await resp.json()
                elif resp.status == 401 or resp.status == 403:
                    await self.login()
                    return await self.request_json(method, location, json)
                else:
                    raise Exception("Crosswork request " + method + " to " + location + " failed: " + str(resp.status))

    async def get_topology(self) -> dict:
        request = {
            "mapType": "LOGICAL",
            "params": {
                "overlays": "{\"topoMetrics\":{\"type\":\"Utilization\",\"thresholds\":[true,true,true,true]},\"topoBase\":{\"showAggregatedLink\":true,\"colorLinkDown\":true,\"showDeviceStatus\":true,\"deviceLabel\":\"hostName\",\"alarmsShow\":true},\"powerPlugin\":{\"power_load_enabled\":true,\"thresholds\":[true,true,true,true]}}"
            }
        }
        return await self.request_json("POST", "/crosswork/topology/v1/topology-service/topology/data", request)
    
    async def get_inventory(self) -> dict:
        request = {
            "filterData": {
                "PageSize": 1000000
            }
        }
        return await self.request_json("POST", "/crosswork/inventory/v1/nodes/query", request)
    
    async def collect_inventory(self) -> None:
        sourcetype = "crosswork-inventory-node"
        data = await self.get_inventory()
        for node in data.get("data", []):
            if "host_name" not in node:
                    continue

            self.publish(sourcetype, node, node["host_name"])        
        self.log_published(sourcetype)

    async def collect_topology(self) -> None:
        names = {}
        sourcetype = "crosswork-topology-node"
        data = await self.get_topology()
        self._logger.debug("Topology: %s", json.dumps(data))
        for node in data.get("nodes", []):
            fields = {}
            uuid = node.get("uuid")
            name = node.get("name")

            if uuid is not None and name is not None:
                names[uuid] = name
                fields["itsiDrilldownURI"] = self._uri + "/crosswork-home/#/ems/inventory-details/" + uuid + "/" + name

            self.publish(sourcetype, node, name, fields)
        self.log_published(sourcetype)

        sourcetype = "crosswork-topology-edge"
        for edge in data.get("edges", []):
            fields = {}
            name = names.get(edge.get("sourceNode", ""), None)
            target = names.get(edge.get("targetNode", ""), None)

            if target is not None:
                fields["target"] = target

            self.publish(sourcetype, edge, name, fields)
        self.log_published(sourcetype)

    async def start_collect_inventory(self, interval: float) -> None:
        async def coroutine() -> None:
            while True:
                try:
                    await self.collect_inventory()
                except Exception as e:
                    log.log_exception(self._logger, e, "cisco_cnc_addon error", msg_before="failed to get crosswork inventory: ")
                await asyncio.sleep(interval)
        self._tasks.append(asyncio.create_task(coroutine()))

    async def start_collect_topology(self, interval: float) -> None:
        async def coroutine() -> None:
            while True:
                try:
                    await self.collect_topology()
                except Exception as e:
                    log.log_exception(self._logger, e, "cisco_cnc_addon error", msg_before="failed to get crosswork topology: ")
                await asyncio.sleep(interval)
        self._tasks.append(asyncio.create_task(coroutine()))

    async def stop(self):
        await self.logout()
        for task in self._tasks:
            task.cancel()
        log.modular_input_end(self._logger, self._name.split("/")[-1])

        

