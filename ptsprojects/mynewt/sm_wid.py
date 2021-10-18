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
import sys
import time

from pybtp import btp
from wid.sm import sm_wid_hdl as gen_wid_hdl
from ptsprojects.stack import get_stack
from ptsprojects.mynewt.iutctl import get_iut

log = logging.warning; logging.getLogger("root").setLevel(logging.DEBUG)


def sm_wid_hdl(wid, description, test_case_name):
    log("%s, %r, %r, %s", sm_wid_hdl.__name__, wid, description,
        test_case_name)
    module = sys.modules[__name__]

    try:
        handler = getattr(module, "hdl_wid_%d" % wid)
        return handler(description)
    except AttributeError:
        return gen_wid_hdl(wid, description, test_case_name, False)


# wid handlers section begin
def hdl_wid_100(desc):
    btp.gap_conn()
    return True


def hdl_wid_102(desc):
    time.sleep(2)
    btp.gap_disconn()
    return True


def hdl_wid_115(desc):
    stack = get_stack()

    btp.gap_set_conn()
    btp.gap_set_gendiscov()
    btp.gap_adv_ind_on(ad=stack.gap.ad)
    return True


def hdl_wid_143(desc):
    mynewtctl = get_iut()

    mynewtctl.wait_iut_ready_event()
    btp.core_reg_svc_gap()
    btp.gap_read_ctrl_info()

    return True
