#!/usr/bin/env python3
# -*- coding: utf-8 -*-

##
# Copyright 2015 Telefónica Investigación y Desarrollo, S.A.U.
# This file is part of openmano
# All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.
#
# For those usages not covered by the Apache License, Version 2.0 please
# contact with: nfvlabs@tid.es
##

"""
asyncio RO python client to interact with RO-server
"""

import asyncio
import aiohttp

import json
import yaml
import logging
import sys
from urllib.parse import quote
from uuid import UUID
from copy import deepcopy

__author__ = "Alfonso Tierno, Pablo Montes"
__date__ = "$09-Jan-2018 09:09:48$"
__version__ = "0.1.0-r470"
version_date = "Jan 2018"
requests = None

class ROClientException(Exception):
    def __init__(self, message, http_code=400):
        self.http_code = http_code
        Exception.__init__(self, message)
    """Common Exception for all openmano client exceptions"""


def remove_envelop(item, indata=None):
    """
    Obtain the useful data removing the envelop. It goes through the vnfd or nsd catalog and returns the
    vnfd or nsd content
    :param item: can be 'tenant', 'vim', 'vnfd', 'nsd', 'ns'
    :param indata: Content to be inspected
    :return: the useful part of indata (a reference, not a new dictionay) plus boolean indicating if it was enveloped
    """
    clean_indata = indata
    enveloped = False
    if not indata:
        return {}, False
    if item == "vnfd":
        if clean_indata.get('vnfd:vnfd-catalog'):
            enveloped = True
            clean_indata = clean_indata['vnfd:vnfd-catalog']
        elif clean_indata.get('vnfd-catalog'):
            enveloped = True
            clean_indata = clean_indata['vnfd-catalog']
        if clean_indata.get('vnfd'):
            if not isinstance(clean_indata['vnfd'], list) or len(clean_indata['vnfd']) != 1:
                raise ROClientException("'vnfd' must be a list only one element")
            clean_indata = clean_indata['vnfd'][0]
    elif item == "nsd":
        if clean_indata.get('nsd:nsd-catalog'):
            enveloped = True
            clean_indata = clean_indata['nsd:nsd-catalog']
        elif clean_indata.get('nsd-catalog'):
            enveloped = True
            clean_indata = clean_indata['nsd-catalog']
        if clean_indata.get('nsd'):
            if not isinstance(clean_indata['nsd'], list) or len(clean_indata['nsd']) != 1:
                raise ROClientException("'nsd' must be a list only one element")
            clean_indata = clean_indata['nsd'][0]
    elif item == "tenant":
        if len(indata) == 1 and "tenant" in indata:
            enveloped = True
            clean_indata = indata["tenant"]
    elif item == "vim" or item == "datacenter":
        if len(indata) == 1 and "datacenter" in indata:
            enveloped = True
            clean_indata = indata["datacenter"]
    elif item == "ns" or item == "instances":
        if len(indata) == 1 and "instance" in indata:
            enveloped = True
            clean_indata = indata["instance"]
    else:
        assert False, "remove_envelop with unknown item {}".format(item)

    return clean_indata, enveloped


class ROClient:
    headers_req = {'Accept': 'application/yaml', 'content-type': 'application/yaml'}
    client_to_RO = {'tenant': 'tenants', 'vim': 'datacenters', 'vnfd': 'vnfs', 'nsd': 'scenarios',
                    'ns': 'instances'}
    mandatory_for_create = {
        'tenant': ("name", ),
        'vim': ("name", "vim_url"),
        'vnfd': ("name", "id", "connection-point", "vdu"),
        'nsd': ("name", "id", "constituent-vnfd"),
        'ns': ("name", "scenario", "datacenter"),
    }
    timeout_large = 120
    timeout_short = 30

    def __init__(self, loop, endpoint_url, **kwargs):
        self.loop = loop
        self.endpoint_url = endpoint_url

        self.username = kwargs.get("username")
        self.password = kwargs.get("password")
        self.tenant_id_name = kwargs.get("tenant")
        self.tenant = None
        self.datacenter_id_name = kwargs.get("datacenter")
        self.datacenter = None
        logger_name = kwargs.get('logger_name', 'ROClient')
        self.logger = logging.getLogger(logger_name)
        if kwargs.get("loglevel"):
            self.logger.setLevel(kwargs["loglevel"])
        global requests
        requests = kwargs.get("TODO remove")

    def __getitem__(self, index):
        if index == 'tenant':
            return self.tenant_id_name
        elif index == 'datacenter':
            return self.datacenter_id_name
        elif index == 'username':
            return self.username
        elif index == 'password':
            return self.password
        elif index == 'endpoint_url':
            return self.endpoint_url
        else:
            raise KeyError("Invalid key '%s'" %str(index))
        
    def __setitem__(self,index, value):
        if index == 'tenant':
            self.tenant_id_name = value
        elif index == 'datacenter':
            self.datacenter_id_name = value
        elif index == 'username':
            self.username = value
        elif index == 'password':
            self.password = value
        elif index == 'endpoint_url':
            self.endpoint_url = value
        else:
            raise KeyError("Invalid key '{}'".format(index))
        self.tenant = None      # force to reload tenant with different credentials
        self.datacenter = None  # force to reload datacenter with different credentials
    
    def _parse(self, descriptor, descriptor_format, response=False):
        #try yaml
        if descriptor_format and descriptor_format != "json" and descriptor_format != "yaml":
            raise  ROClientException("'descriptor_format' must be a 'json' or 'yaml' text")
        if descriptor_format != "json":
            try:
                return yaml.load(descriptor)
            except yaml.YAMLError as exc:
                error_pos = ""
                if hasattr(exc, 'problem_mark'):
                    mark = exc.problem_mark
                    error_pos = " at line:{} column:{}s".format(mark.line+1, mark.column+1)
                error_text = "yaml format error" + error_pos
        elif descriptor_format != "yaml":
            try:
                return json.loads(descriptor) 
            except Exception as e:
                if response:
                    error_text = "json format error" + str(e)

        if response:
            raise ROClientException(error_text)
        raise  ROClientException(error_text)
    
    def _parse_yaml(self, descriptor, response=False):
        try:
            return yaml.load(descriptor)
        except yaml.YAMLError as exc:
            error_pos = ""
            if hasattr(exc, 'problem_mark'):
                mark = exc.problem_mark
                error_pos = " at line:{} column:{}s".format(mark.line+1, mark.column+1)
            error_text = "yaml format error" + error_pos
            if response:
                raise ROClientException(error_text)
            raise  ROClientException(error_text)

    @staticmethod
    def check_if_uuid(uuid_text):
        """
        Check if text correspond to an uuid foramt
        :param uuid_text:
        :return: True if it is an uuid False if not
        """
        try:
            UUID(uuid_text)
            return True
        except (ValueError, TypeError):
            return False

    @staticmethod
    def _create_envelop(item, indata=None):
        """
        Returns a new dict that incledes indata with the expected envelop
        :param item: can be 'tenant', 'vim', 'vnfd', 'nsd', 'ns'
        :param indata: Content to be enveloped
        :return: a new dic with {<envelop>: {indata} } where envelop can be e.g. tenant, datacenter, ...
        """
        if item == "vnfd":
            return {'vnfd-catalog': {'vnfd': [indata]}}
        elif item == "nsd":
            return {'nsd-catalog': {'nsd': [indata]}}
        elif item == "tenant":
            return {'tenant': indata}
        elif item == "vim" or item == "datacenter":
            return {'datacenter': indata}
        elif item == "ns" or item == "instances":
            return {'instance': indata}
        else:
            assert False, "_create_envelop with unknown item {}".format(item)

    @staticmethod
    def check_ns_status(ns_descriptor):
        """
        Inspect RO instance descriptor and indicates the status
        :param ns_descriptor: instance descriptor obtained with self.show("ns", )
        :return: status, message: status can be BUILD,ACTIVE,ERROR, message is a text message
        """
        net_total = 0
        vm_total = 0
        net_done = 0
        vm_done = 0

        for net in ns_descriptor["nets"]:
            net_total += 1
            if net["status"] == "ERROR":
                return "ERROR", net["error_msg"]
            elif net["status"] == "ACTIVE":
                net_done += 1
        for vnf in ns_descriptor["vnfs"]:
            for vm in vnf["vms"]:
                vm_total += 1
                if vm["status"] == "ERROR":
                    return "ERROR", vm["error_msg"]
                elif vm["status"] == "ACTIVE":
                    vm_done += 1

        if net_total == net_done and vm_total == vm_done:
            return "ACTIVE", "VMs {}, networks: {}".format(vm_total, net_total)
        else:
            return "BUILD", "VMs: {}/{}, networks: {}/{}".format(vm_done, vm_total, net_done, net_total)

    @staticmethod
    def get_ns_vnf_ip(ns_descriptor):
        """
        Get a dict with the IPs of every vnf.
        :param ns_descriptor: instance descriptor obtained with self.show("ns", )
        :return: dict iwth key member_vnf_index, value ip_address
        """
        ns_ip={}
        for vnf in ns_descriptor["vnfs"]:
            ns_ip[str(vnf["member_vnf_index"])] = vnf["ip_address"]
            #uuid  sce_vnf_id
            # vnf[mgmt_access]: '{interface_id: cf3cbf00-385c-49b4-9a3f-b400b7b15dc6, vm_id: d0dd22a9-91ef-46f1-8e8f-8cf4b2d5b2d7}'
            # vnf[vms]
        return ns_ip

    async def get_list(self, item, all_tenants=False, filter_by=None):
        """
        Obtain a list of items filtering by the specigy filter_by.
        :param item: can be 'tenant', 'vim', 'vnfd', 'nsd', 'ns'
        :param all_tenants: True if not filtering by tenant. Only allowed for admin
        :param filter_by: dictionary with filtering
        :return: a list of dict. It can be empty. Raises ROClientException on Error,
        """
        try:
            if item not in self.client_to_RO:
                raise ROClientException("Invalid item {}".format(item))
            if item == 'tenant':
                all_tenants = None
            with aiohttp.ClientSession(loop=self.loop) as session:
                content = await self._list_item(session, self.client_to_RO[item], all_tenants=all_tenants,
                                                filter_dict=filter_by)
            if isinstance(content, dict):
                if len(content) == 1:
                    for _, v in content.items():
                        return v
                    return content.values()[0]
                else:
                    raise ROClientException("Output not a list neither dict with len equal 1", http_code=500)
                return content
        except aiohttp.errors.ClientOSError as e:
            raise ROClientException(e, http_code=504)

    async def _get_item_uuid(self, session, item, item_id_name, all_tenants=False):
        if all_tenants:
            tenant_text = "/any"
        elif all_tenants is None:
            tenant_text = ""
        else:
            if not self.tenant:
                await self._get_tenant(session)
            tenant_text = "/" + self.tenant

        item_id = 0
        url = "{}{}/{}".format(self.endpoint_url, tenant_text, item)
        if self.check_if_uuid(item_id_name):
            item_id = item_id_name
            url += "/" + item_id_name
        elif item_id_name and item_id_name.startswith("'") and item_id_name.endswith("'"):
            item_id_name = item_id_name[1:-1]
        self.logger.debug("openmano GET %s", url)
        with aiohttp.Timeout(self.timeout_short):
            async with session.get(url, headers=self.headers_req) as response:
                response_text = await response.read()
                self.logger.debug("GET {} [{}] {}".format(url, response.status, response_text[:100]))
                if response.status == 404:  # NOT_FOUND
                    raise ROClientException("No {} found with id '{}'".format(item[:-1], item_id_name),
                                                    http_code=404)
                if response.status >= 300:
                    raise ROClientException(response_text, http_code=response.status)
            content = self._parse_yaml(response_text, response=True)

        if item_id:
            return item_id
        desc = content[item]
        assert isinstance(desc, list), "_get_item_uuid get a non dict with a list inside {}".format(type(desc))
        uuid = None
        for i in desc:
            if item_id_name and i["name"] != item_id_name:
                continue
            if uuid:  # found more than one
                raise ROClientException(
                    "Found more than one {} with name '{}'. uuid must be used".format(item, item_id_name),
                    http_code=404)
            uuid = i["uuid"]
        if not uuid:
            raise ROClientException("No {} found with name '{}'".format(item[:-1], item_id_name), http_code=404)
        return uuid

    async def _get_item(self, session, item, item_id_name, all_tenants=False):
        if all_tenants:
            tenant_text = "/any"
        elif all_tenants is None:
            tenant_text = ""
        else:
            if not self.tenant:
                await self._get_tenant(session)
            tenant_text = "/" + self.tenant

        if self.check_if_uuid(item_id_name):
            uuid = item_id_name
        else:
            # check that exist
            uuid = self._get_item_uuid(session, item, item_id_name, all_tenants)
        
        url = "{}{}/{}/{}".format(self.endpoint_url, tenant_text, item, uuid)
        self.logger.debug("GET %s", url )
        with aiohttp.Timeout(self.timeout_short):
            async with session.get(url, headers=self.headers_req) as response:
                response_text = await response.read()
                self.logger.debug("GET {} [{}] {}".format(url, response.status, response_text[:100]))
                if response.status >= 300:
                    raise ROClientException(response_text, http_code=response.status)

        return self._parse_yaml(response_text, response=True)

    async def _get_tenant(self, session):
        if not self.tenant:
            self.tenant = await self._get_item_uuid(session, "tenants", self.tenant_id_name, None)
        return self.tenant
    
    async def _get_datacenter(self, session):
        if not self.tenant:
            await self._get_tenant(session)
        if not self.datacenter:
            self.datacenter = await self._get_item_uuid(session, "datacenters", self.datacenter_id_name, True)
        return self.datacenter

    async def _create_item(self, session, item, descriptor, all_tenants=False):
        if all_tenants:
            tenant_text = "/any"
        elif all_tenants is None:
            tenant_text = ""
        else:
            if not self.tenant:
                await self._get_tenant(session)
            tenant_text = "/" + self.tenant
        payload_req = yaml.safe_dump(descriptor)

        api_version_text = ""
        if item == "vnfs":
            # assumes version v3 only
            api_version_text = "/v3"
            item = "vnfd"
        elif item == "scenarios":
            # assumes version v3 only
            api_version_text = "/v3"
            item = "nsd"

        #print payload_req

        url = "{}{apiver}{tenant}/{item}".format(self.endpoint_url, apiver=api_version_text, tenant=tenant_text,
                                                        item=item)
        self.logger.debug("openmano POST %s %s", url, payload_req)
        with aiohttp.Timeout(self.timeout_large):
            async with session.post(url, headers=self.headers_req, data=payload_req) as response:
                response_text = await response.read()
                self.logger.debug("POST {} [{}] {}".format(url, response.status, response_text[:100]))
                if response.status >= 300:
                    raise ROClientException(response_text, http_code=response.status)

        response_desc = self._parse_yaml(response_text, response=True)
        desc, _ = remove_envelop(item, response_desc)
        return desc

    async def _del_item(self, session, item, item_id_name, all_tenants=False):
        if all_tenants:
            tenant_text = "/any"
        elif all_tenants is None:
            tenant_text = ""
        else:
            if not self.tenant:
                await self._get_tenant(session)
            tenant_text = "/" + self.tenant
        if not self.check_if_uuid(item_id_name):
            # check that exist
            uuid = await self._get_item_uuid(session, item, item_id_name, all_tenants)
        else:
            uuid = item_id_name
        
        url = "{}{}/{}/{}".format(self.endpoint_url, tenant_text, item, uuid)
        self.logger.debug("DELETE %s", url)
        with aiohttp.Timeout(self.timeout_short):
            async with session.delete(url, headers=self.headers_req) as response:
                response_text = await response.read()
                self.logger.debug("DELETE {} [{}] {}".format(url, response.status, response_text[:100]))
                if response.status >= 300:
                    raise ROClientException(response_text, http_code=response.status)
        return self._parse_yaml(response_text, response=True)

    async def _list_item(self, session, item, all_tenants=False, filter_dict=None):
        if all_tenants:
            tenant_text = "/any"
        elif all_tenants is None:
            tenant_text = ""
        else:
            if not self.tenant:
                await self._get_tenant(session)
            tenant_text = "/" + self.tenant
        
        url = "{}{}/{}".format(self.endpoint_url, tenant_text, item)
        separator = "?"
        if filter_dict:
            for k in filter_dict:
                url += separator + quote(str(k)) + "=" + quote(str(filter_dict[k])) 
                separator = "&"
        self.logger.debug("openmano GET %s", url)
        with aiohttp.Timeout(self.timeout_short):
            async with session.get(url, headers=self.headers_req) as response:
                response_text = await response.read()
                self.logger.debug("GET {} [{}] {}".format(url, response.status, response_text[:100]))
                if response.status >= 300:
                    raise ROClientException(response_text, http_code=response.status)
        return self._parse_yaml(response_text, response=True)

    async def _edit_item(self, session, item, descriptor, item_id_name, all_tenants=False):
        if all_tenants:
            tenant_text = "/any"
        elif all_tenants is None:
            tenant_text = ""
        else:
            if not self.tenant:
                await self._get_tenant(session)
            tenant_text = "/" + self.tenant

        if not uuid:
            #check that exist
            uuid = self._get_item_uuid(session, "tenants", item_id_name, all_tenants)
        
        payload_req = yaml.safe_dump(descriptor)
            
        #print payload_req
            
        url = "{}{}/{}/{}".format(self.endpoint_url, tenant_text, item, uuid)
        self.logger.debug("openmano PUT %s %s", url, payload_req)
        with aiohttp.Timeout(self.timeout_large):
            async with session.put(url, headers=self.headers_req, data=payload_req) as response:
                response_text = await response.read()
                self.logger.debug("PUT {} [{}] {}".format(url, response.status, response_text[:100]))
                if response.status >= 300:
                    raise ROClientException(response_text, http_code=response.status)
        return self._parse_yaml(response_text, response=True)

    async def show(self, item, item_id_name=None, all_tenants=False):
        """
        Obtain the information of an item from its id or name
        :param item: can be 'tenant', 'vim', 'vnfd', 'nsd', 'ns'
        :param item_id_name: RO id or name of the item. Raise and exception if more than one found
        :param all_tenants: True if not filtering by tenant. Only allowed for admin
        :return: dictionary with the information or raises ROClientException on Error, NotFound, found several
        """
        try:
            if item not in self.client_to_RO:
                raise ROClientException("Invalid item {}".format(item))
            if item == 'tenant':
                all_tenants = None

            with aiohttp.ClientSession(loop=self.loop) as session:
                content = await self._get_item(session, self.client_to_RO[item], item_id_name, all_tenants=all_tenants)
            if len(content) == 1:
                return content.values()[0]
            else:
                return content
        except aiohttp.errors.ClientOSError as e:
            raise ROClientException(e, http_code=504)

    async def delete(self, item, item_id_name=None, all_tenants=False):
        """
        Delete  the information of an item from its id or name
        :param item: can be 'tenant', 'vim', 'vnfd', 'nsd', 'ns'
        :param item_id_name: RO id or name of the item. Raise and exception if more than one found
        :param all_tenants: True if not filtering by tenant. Only allowed for admin
        :return: dictionary with the information or raises ROClientException on Error, NotFound, found several
        """
        try:
            if item not in self.client_to_RO:
                raise ROClientException("Invalid item {}".format(item))
            if item == 'tenant':
                all_tenants = None

            with aiohttp.ClientSession(loop=self.loop) as session:
                if item == 'vim':
                    # check that exist
                    item_id = await self._get_item_uuid(session, "datacenters", item_id_name, all_tenants=True)
                    all_tenants = None
                return await self._del_item(session, self.client_to_RO[item], item_id_name, all_tenants=all_tenants)
        except aiohttp.errors.ClientOSError as e:
            raise ROClientException(e, http_code=504)

    async def create(self, item, descriptor=None, descriptor_format=None, **kwargs):
        """
        Creates an item from its descriptor
        :param item: can be 'tenant', 'vim', 'vnfd', 'nsd', 'ns'
        :param descriptor: can be a dict, or a yaml/json text. Autodetect unless descriptor_format is provided
        :param descriptor_format: Can be 'json' or 'yaml'
        :param kwargs: Overrides descriptor with values as name, description, vim_url, vim_url_admin, vim_type
               keys can be a dot separated list to specify elements inside dict
        :return: dictionary with the information or raises ROClientException on Error
        """
        try:
            if isinstance(descriptor, str):
                descriptor = self._parse(descriptor, descriptor_format)
            elif descriptor:
                pass
            else:
                descriptor = {}

            if item not in self.client_to_RO:
                raise ROClientException("Invalid item {}".format(item))
            desc, enveloped = remove_envelop(item, descriptor)

            # Override descriptor with kwargs
            if kwargs:
                desc = deepcopy(desc)  # do not modify original descriptor
                try:
                    for k, v in kwargs.items():
                        update_content = desc
                        kitem_old = None
                        klist = k.split(".")
                        for kitem in klist:
                            if kitem_old is not None:
                                update_content = update_content[kitem_old]
                            if isinstance(update_content, dict):
                                kitem_old = kitem
                            elif isinstance(update_content, list):
                                kitem_old = int(kitem)
                            else:
                                raise ROClientException(
                                    "Invalid query string '{}'. Descriptor is not a list nor dict at '{}'".format(k, kitem))
                        if v == "__DELETE__":
                            del update_content[kitem_old]
                        else:
                            update_content[kitem_old] = v
                except KeyError:
                    raise ROClientException(
                        "Invalid query string '{}'. Descriptor does not contain '{}'".format(k, kitem_old))
                except ValueError:
                    raise ROClientException("Invalid query string '{}'. Expected integer index list instead of '{}'".format(
                        k, kitem))
                except IndexError:
                    raise ROClientException(
                        "Invalid query string '{}'. Index '{}' out of  range".format(k, kitem_old))

            for mandatory in self.mandatory_for_create[item]:
                if mandatory not in desc:
                    raise ROClientException("'{}' is mandatory parameter for {}".format(mandatory, item))

            all_tenants = False
            if item in ('tenant', 'vim'):
                all_tenants = None

            if not enveloped:
                create_desc = self._create_envelop(item, desc)
            else:
                create_desc = descriptor

            with aiohttp.ClientSession(loop=self.loop) as session:
                return await self._create_item(session, self.client_to_RO[item], create_desc, all_tenants)
        except aiohttp.errors.ClientOSError as e:
            raise ROClientException(e, http_code=504)

    def edit_tenant(self, uuid=None, name=None, descriptor=None, descriptor_format=None, new_name=None, new_description=None):
        """Edit the parameters of a tenant
        Params: must supply a descriptor or/and a new_name or new_description
            uuid or/and name. If only name is supplied, there must be only one or an exception is raised
            descriptor: with format {'tenant':{params to change info}}
                must be a dictionary or a json/yaml text.
            name: the tenant name. Overwrite descriptor name if any
            description: tenant descriptor.. Overwrite descriptor description if any
        Return: Raises an exception on error, not found or found several
                Obtain a dictionary with format {'tenant':{newtenant_info}}
        """
        try:
            # TODO revise
            if isinstance(descriptor, str):
                descriptor = self.parse(descriptor, descriptor_format)
            elif descriptor:
                pass
            elif new_name or new_description:
                descriptor={"tenant": {}}
            else:
                raise ROClientException("Missing descriptor")

            if 'tenant' not in descriptor or len(descriptor)!=1:
                raise ROClientException("Descriptor must contain only one 'tenant' field")
            if new_name:
                descriptor['tenant']['name'] = new_name
            if new_description:
                descriptor['tenant']['description'] = new_description

            return self._edit_item("tenants", descriptor, uuid, name, all_tenants=None)
        except aiohttp.errors.ClientOSError as e:
            raise ROClientException(e, http_code=504)

    #DATACENTERS

    def edit_datacenter(self, uuid=None, name=None, descriptor=None, descriptor_format=None, all_tenants=False, **kwargs):
        """Edit the parameters of a datacenter
        Params: must supply a descriptor or/and a parameter to change
            uuid or/and name. If only name is supplied, there must be only one or an exception is raised
            descriptor: with format {'datacenter':{params to change info}}
                must be a dictionary or a json/yaml text.
            parameters to change can be supplyied by the descriptor or as parameters:
                new_name: the datacenter name
                vim_url: the datacenter URL
                vim_url_admin: the datacenter URL for administrative issues
                vim_type: the datacenter type, can be openstack or openvim.
                public: boolean, available to other tenants
                description: datacenter description
        Return: Raises an exception on error, not found or found several
                Obtain a dictionary with format {'datacenter':{new_datacenter_info}}
        """

        if isinstance(descriptor, str):
            descriptor = self.parse(descriptor, descriptor_format)
        elif descriptor:
            pass
        elif kwargs:
            descriptor={"datacenter": {}}
        else:
            raise ROClientException("Missing descriptor")

        if 'datacenter' not in descriptor or len(descriptor)!=1:
            raise ROClientException("Descriptor must contain only one 'datacenter' field")
        for param in kwargs:
            if param=='new_name':
                descriptor['datacenter']['name'] = kwargs[param]
            else:
                descriptor['datacenter'][param] = kwargs[param]
        return self._edit_item("datacenters", descriptor, uuid, name, all_tenants=None)
    
    def attach_datacenter(self, uuid_name=None, descriptor=None, descriptor_format=None,  vim_user=None, vim_password=None, vim_tenant_name=None, vim_tenant_id=None):
        #check that exist
        uuid = self._get_item_uuid(session, "datacenters", uuid_name, all_tenants=True)
        tenant_text = "/"+self._get_tenant()

        if isinstance(descriptor, str):
            descriptor = self.parse(descriptor, descriptor_format)
        elif descriptor:
            pass
        elif vim_user or vim_password or vim_tenant_name or vim_tenant_id:
            descriptor={"datacenter": {}}
        else:
            raise ROClientException("Missing descriptor or params")
        
        if vim_user or vim_password or vim_tenant_name or vim_tenant_id:
            #print args.name
            try:
                if vim_user:
                    descriptor['datacenter']['vim_user'] = vim_user
                if vim_password:
                    descriptor['datacenter']['vim_password'] = vim_password
                if vim_tenant_name:
                    descriptor['datacenter']['vim_tenant_name'] = vim_tenant_name
                if vim_tenant_id:
                    descriptor['datacenter']['vim_tenant'] = vim_tenant_id
            except (KeyError, TypeError) as e:
                if str(e)=='datacenter':           error_pos= "missing field 'datacenter'"
                else:                       error_pos="wrong format"
                raise ROClientException("Wrong datacenter descriptor: " + error_pos)

        payload_req = yaml.safe_dump(descriptor)
        #print payload_req
        url = "{}{}/datacenters/{}".format(self.endpoint_url, tenant_text, uuid)
        self.logger.debug("openmano POST %s %s", url, payload_req)
        mano_response = requests.post(url, headers = self.headers_req, data=payload_req)
        self.logger.debug("openmano response: %s", mano_response.text )
    
        content = self._parse_yaml(mano_response.text, response=True)
        if mano_response.status_code==200:
            return content
        else:
            raise ROClientException(str(content), http_code=mano_response.status)        

    def detach_datacenter(self, uuid_name=None):
        if not uuid:
            #check that exist
            uuid = self._get_item_uuid(session, "datacenters", uuid_name, all_tenants=False)
        tenant_text = "/"+self._get_tenant()
        url = "{}{}/datacenters/{}".format(self.endpoint_url, tenant_text, uuid)
        self.logger.debug("openmano DELETE %s", url)
        mano_response = requests.delete(url, headers = self.headers_req)
        self.logger.debug("openmano response: %s", mano_response.text )
    
        content = self._parse_yaml(mano_response.text, response=True)
        if mano_response.status_code==200:
            return content
        else:
            raise ROClientException(str(content), http_code=mano_response.status)        

    #VNFS

    def edit_scenario(self, uuid=None, name=None, descriptor=None, descriptor_format=None, all_tenants=False, **kwargs):
        """Edit the parameters of a scenario
        Params: must supply a descriptor or/and a parameters to change
            uuid or/and name. If only name is supplied, there must be only one or an exception is raised
            descriptor: with format {'scenario':{params to change info}}
                must be a dictionary or a json/yaml text.
            parameters to change can be supplyied by the descriptor or as parameters:
                new_name: the scenario name
                public: boolean, available to other tenants
                description: scenario description
                tenant_id. Propietary tenant
        Return: Raises an exception on error, not found or found several
                Obtain a dictionary with format {'scenario':{new_scenario_info}}
        """
 
        if isinstance(descriptor, str):
            descriptor = self.parse(descriptor, descriptor_format)
        elif descriptor:
            pass
        elif kwargs:
            descriptor={"scenario": {}}
        else:
            raise ROClientException("Missing descriptor")
 
        if 'scenario' not in descriptor or len(descriptor)>2:
            raise ROClientException("Descriptor must contain only one 'scenario' field")
        for param in kwargs:
            if param=='new_name':
                descriptor['scenario']['name'] = kwargs[param]
            else:
                descriptor['scenario'][param] = kwargs[param]
        return self._edit_item("scenarios", descriptor, uuid, name, all_tenants=None)

    #VIM ACTIONS
    def vim_action(self, action, item, uuid=None, all_tenants=False, **kwargs):
        """Perform an action over a vim
        Params: 
            action: can be 'list', 'get'/'show', 'delete' or 'create'
            item: can be 'tenants' or 'networks'
            uuid: uuid of the tenant/net to show or to delete. Ignore otherwise
            other parameters:
                datacenter_name, datacenter_id: datacenters to act on, if missing uses classes store datacenter 
                descriptor, descriptor_format: descriptor needed on creation, can be a dict or a yaml/json str 
                    must be a dictionary or a json/yaml text.
                name: for created tenant/net Overwrite descriptor name if any
                description: tenant descriptor. Overwrite descriptor description if any
                
        Return: Raises an exception on error
                Obtain a dictionary with format {'tenant':{new_tenant_info}}
        """
        if item not in ("tenants", "networks", "images"):
            raise ROClientException("Unknown value for item '{}', must be 'tenants', 'nets' or "
                                             "images".format(str(item)))

        image_actions = ['list','get','show','delete']
        if item == "images" and action not in image_actions:
            raise ROClientException("Only available actions for item '{}' are {}\n"
                                             "Requested action was '{}'".format(item, ', '.join(image_actions), action))
        if all_tenants:
            tenant_text = "/any"
        else:
            tenant_text = "/"+self._get_tenant()
        
        if "datacenter_id" in kwargs or "datacenter_name" in kwargs:
            datacenter = self._get_item_uuid(session, "datacenters", kwargs.get("datacenter"), all_tenants=all_tenants)
        else:
            datacenter = self.get_datacenter(session)

        if action=="list":
            url = "{}{}/vim/{}/{}".format(self.endpoint_url, tenant_text, datacenter, item)
            self.logger.debug("GET %s", url )
            mano_response = requests.get(url, headers=self.headers_req)
            self.logger.debug("openmano response: %s", mano_response.text )
            content = self._parse_yaml(mano_response.text, response=True)            
            if mano_response.status_code==200:
                return content
            else:
                raise ROClientException(str(content), http_code=mano_response.status)        
        elif action=="get" or action=="show":
            url = "{}{}/vim/{}/{}/{}".format(self.endpoint_url, tenant_text, datacenter, item, uuid)
            self.logger.debug("GET %s", url )
            mano_response = requests.get(url, headers=self.headers_req)
            self.logger.debug("openmano response: %s", mano_response.text )
            content = self._parse_yaml(mano_response.text, response=True)            
            if mano_response.status_code==200:
                return content
            else:
                raise ROClientException(str(content), http_code=mano_response.status)        
        elif action=="delete":
            url = "{}{}/vim/{}/{}/{}".format(self.endpoint_url, tenant_text, datacenter, item, uuid)
            self.logger.debug("DELETE %s", url )
            mano_response = requests.delete(url, headers=self.headers_req)
            self.logger.debug("openmano response: %s", mano_response.text )
            content = self._parse_yaml(mano_response.text, response=True)            
            if mano_response.status_code==200:
                return content
            else:
                raise ROClientException(str(content), http_code=mano_response.status)        
        elif action=="create":
            if "descriptor" in kwargs:
                if isinstance(kwargs["descriptor"], str):
                    descriptor = self._parse(kwargs["descriptor"], kwargs.get("descriptor_format") )
                else:
                    descriptor = kwargs["descriptor"]
            elif "name" in kwargs:
                descriptor={item[:-1]: {"name": kwargs["name"]}}
            else:
                raise ROClientException("Missing descriptor")
        
            if item[:-1] not in descriptor or len(descriptor)!=1:
                raise ROClientException("Descriptor must contain only one 'tenant' field")
            if "name" in kwargs:
                descriptor[ item[:-1] ]['name'] = kwargs["name"]
            if "description" in kwargs:
                descriptor[ item[:-1] ]['description'] = kwargs["description"]
            payload_req = yaml.safe_dump(descriptor)
            #print payload_req
            url = "{}{}/vim/{}/{}".format(self.endpoint_url, tenant_text, datacenter, item)
            self.logger.debug("openmano POST %s %s", url, payload_req)
            mano_response = requests.post(url, headers = self.headers_req, data=payload_req)
            self.logger.debug("openmano response: %s", mano_response.text )
            content = self._parse_yaml(mano_response.text, response=True)
            if mano_response.status_code==200:
                return content
            else:
                raise ROClientException(str(content), http_code=mano_response.status)
        else:
            raise ROClientException("Unknown value for action '{}".format(str(action))) 


if __name__ == '__main__':
    RO_URL = "http://localhost:9090/openmano"
    RO_TENANT = "osm"
    RO_VIM = "myvim"

    streamformat = "%(asctime)s %(name)s %(levelname)s: %(message)s"
    logging.basicConfig(format=streamformat)

    loop = asyncio.get_event_loop()
    myClient = ROClient(endpoint_url=RO_URL, loop=loop, tenant_id=RO_TENANT, datacenter_id=RO_VIM, debug=True)
    content = loop.run_until_complete(myClient.list_tenants())
    print(content)
    loop.close()

