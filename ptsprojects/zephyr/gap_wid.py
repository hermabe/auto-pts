#
# auto-pts - The Bluetooth PTS Automation Framework
#
# Copyright (c) 2017, Intel Corporation.
#
# This program is free software; you can redistribute it and/or modify it
# under the terms and conditions of the GNU General Public License,
# version 2, as published by the Free Software Foundation.
#
# This program is distributed in the hope it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License for
# more details.
#

import logging
import socket
import sys

from pybtp import btp
from pybtp.types import UUID, AdType, UriScheme
from wid.gap import gap_wid_hdl as gen_wid_hdl, hdl_wid_139_mode1_lvl2, hdl_wid_139_mode1_lvl4
from ptsprojects.stack import get_stack

log = logging.warning; logging.getLogger("root").setLevel(logging.DEBUG)


def gap_wid_hdl(wid, description, test_case_name):
    log("%s, %r, %r, %s", gap_wid_hdl.__name__, wid, description,
        test_case_name)
    module = sys.modules[__name__]

    try:
        handler = getattr(module, "hdl_wid_%d" % wid)
        return handler(description)
    except AttributeError:
        return gen_wid_hdl(wid, description, test_case_name, False)


# For tests that expect "OK" response even if read operation is not successful
def gap_wid_hdl_failed_read(wid, description, test_case_name):
    log("%s, %r, %r, %s", gap_wid_hdl.__name__, wid, description,
        test_case_name)

    if wid == 112:
        bd_addr = btp.pts_addr_get()
        bd_addr_type = btp.pts_addr_type_get()

        handle = btp.parse_handle_description(description)
        if not handle:
            return False

        try:
            btp.gattc_read(bd_addr_type, bd_addr, handle)
            btp.gattc_read_rsp()
        except socket.timeout:
            pass
        return True
    return gap_wid_hdl(wid, description, test_case_name)


# For tests in SC only, mode 1 level 3
def gap_wid_hdl_mode1_lvl2(wid, description, test_case_name):
    if wid == 139:
        log("%s, %r, %r, %s", gap_wid_hdl_mode1_lvl2.__name__, wid, description,
            test_case_name)
        return hdl_wid_139_mode1_lvl2(description)
    return gap_wid_hdl(wid, description, test_case_name)


def gap_wid_hdl_mode1_lvl4(wid, description, test_case_name):
    if wid == 139:
        log("%s, %r, %r, %s", gap_wid_hdl.__name__, wid, description,
            test_case_name)
        return hdl_wid_139_mode1_lvl4(description)
    return gap_wid_hdl(wid, description, test_case_name)


def hdl_wid_46(desc):
    return True


def hdl_wid_73(desc):
    btp.gattc_read_uuid(btp.pts_addr_type_get(None), btp.pts_addr_get(None),
                        '0001', 'FFFF', UUID.device_name)
    return True


def hdl_wid_104(desc):
    return True


def hdl_wid_112(desc):
    bd_addr = btp.pts_addr_get()
    bd_addr_type = btp.pts_addr_type_get()

    handle = btp.parse_handle_description(desc)
    if not handle:
        return False

    btp.gattc_read(bd_addr_type, bd_addr, handle)
    # PTS doesn't respond to read req if we do not respond to this WID
    # btp.gattc_read_rsp()
    return True


def hdl_wid_114(desc):
    return True


def hdl_wid_127(desc):
    btp.gap_conn_param_update(btp.pts_addr_get(), btp.pts_addr_type_get(),
                              720, 864, 0, 400)
    return True


def hdl_wid_130(desc):
    if 'invalid MAC' in desc:
        return btp.gatts_verify_write_fail(desc)
    # GAP/SEC/CSIGN/BI-02-C expects two successes and fail
    # during first success check might occur second gatts_attr_value_changed_ev,
    # which will not be checked. Check up to three times if write fail occured
    for i in range(3):
        if not btp.gatts_verify_write_success(desc):
            return True
    return False


def hdl_wid_162(desc):
    return True


def hdl_wid_173(desc):
    stack = get_stack()

    # Prepare space for URI
    stack.gap.sd.clear()
    stack.gap.sd[AdType.uri] = UriScheme.https + \
        'github.com/intel/auto-pts'.encode()

    btp.gap_adv_ind_on(sd=stack.gap.sd)

    return True


def hdl_wid_224(desc):
    return True
