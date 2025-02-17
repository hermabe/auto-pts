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
import os
import queue
import signal
import socket
import subprocess
import sys
import threading
import time
import serial
if sys.platform != "win32":
    from fcntl import fcntl, F_GETFL, F_SETFL
    from os import O_NONBLOCK

from pybtp import defs
from pybtp.types import BTPError
from pybtp.parser import enc_frame, dec_hdr, dec_data, HDR_LEN

log = logging.debug

# BTP communication transport: unix domain socket file name
BTP_ADDRESS = "/tmp/bt-stack-tester"

EVENT_HANDLER = None


def set_event_handler(event_handler):
    """This is required by BTPWorker to drive stack"""
    global EVENT_HANDLER

    EVENT_HANDLER = event_handler


class BTPSocket:

    def __init__(self):
        self.sock = None
        self.conn = None
        self.addr = None

    def open(self, btp_address=BTP_ADDRESS):
        """Open BTP socket for IUT"""
        if os.path.exists(btp_address):
            os.remove(btp_address)

        if sys.platform == "win32":
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.bind((socket.gethostname(), 0))
        else:
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.bind(btp_address)

        # queue only one connection
        self.sock.listen(1)

    def accept(self, timeout=10.0):
        """Accept incomming Zephyr connection

        timeout - accept timeout in seconds"""

        self.sock.settimeout(timeout)
        self.conn, self.addr = self.sock.accept()
        self.sock.settimeout(None)

    def read(self, timeout=20.0):
        """Read BTP data from socket

        timeout - read timeout in seconds"""
        toread_hdr_len = HDR_LEN
        hdr = bytearray(toread_hdr_len)
        hdr_memview = memoryview(hdr)
        self.conn.settimeout(timeout)

        # Gather frame header
        while toread_hdr_len:
            nbytes = self.conn.recv_into(hdr_memview, toread_hdr_len)
            if nbytes == 0 and toread_hdr_len != 0:
                raise socket.error
            hdr_memview = hdr_memview[nbytes:]
            toread_hdr_len -= nbytes

        tuple_hdr = dec_hdr(hdr)
        toread_data_len = tuple_hdr.data_len

        logging.debug("Received: hdr: %r %r", tuple_hdr, hdr)

        data = bytearray(toread_data_len)
        data_memview = memoryview(data)

        # Gather optional frame data
        while toread_data_len:
            nbytes = self.conn.recv_into(data_memview, toread_data_len)
            data_memview = data_memview[nbytes:]
            toread_data_len -= nbytes

        tuple_data = bytes(str(dec_data(data)), 'utf-8').decode("unicode_escape").replace("b'", "'")

        log("Received data: %r, %r", tuple_data, data)
        self.conn.settimeout(None)
        return tuple_hdr, dec_data(data)

    def send(self, svc_id, op, ctrl_index, data):
        """Send BTP formated data over socket"""
        logging.debug("%s, %r %r %r %r",
                      self.send.__name__, svc_id, op, ctrl_index, str(data))

        logging.debug("btpclient command: send %d %d %d %r",
                      svc_id, op, ctrl_index, str(data))

        frame = enc_frame(svc_id, op, ctrl_index, data)

        logging.debug("sending frame %r", frame)
        self.conn.send(frame)

    def close(self):
        try:
            self.conn.shutdown(socket.SHUT_RDWR)
            self.conn.close()
            self.sock.close()
        except BaseException as e:
            logging.exception(e)
        self.sock = None
        self.conn = None
        self.addr = None


class BTPWorker(BTPSocket):
    def __init__(self):
        super().__init__()

        self._rx_queue = queue.Queue()
        self._running = threading.Event()

        self._rx_worker = threading.Thread(target=self._rx_task)

        self.event_handler_cb = None

    def _rx_task(self):
        while self._running.is_set():
            try:
                data = super().read(timeout=1.0)

                hdr = data[0]
                if hdr.op >= 0x80:
                    # Do not put handled events on RX queue
                    ret = EVENT_HANDLER(*data)
                    if ret is True:
                        continue

                self._rx_queue.put(data)
            except (socket.timeout, socket.error):
                pass  # these are expected so ignore
            except Exception as e:
                logging.error("%r", e)

    @staticmethod
    def _read_timeout(flag):
        flag.clear()

    def read(self, timeout=20.0):
        logging.debug("%s", self.read.__name__)

        flag = threading.Event()
        flag.set()

        t = threading.Timer(timeout, self._read_timeout, [flag])
        t.start()

        while flag.is_set():
            if self._rx_queue.empty():
                continue

            t.cancel()

            data = self._rx_queue.get()
            self._rx_queue.task_done()

            return data

        raise socket.timeout

    def send_wait_rsp(self, svc_id, op, ctrl_index, data, cb=None,
                      user_data=None):
        super().send(svc_id, op, ctrl_index, data)
        ret = True

        while ret:
            tuple_hdr, tuple_data = self.read()

            if tuple_hdr.svc_id != svc_id:
                raise BTPError(
                    "Incorrect service ID %s in the response, expected %s!" %
                    (tuple_hdr.svc_id, svc_id))

            if tuple_hdr.op == defs.BTP_STATUS:
                raise BTPError("Error opcode in response!")

            if op != tuple_hdr.op:
                raise BTPError(
                    "Invalid opcode 0x%.2x in the response, expected 0x%.2x!" %
                    (tuple_hdr.op, op))

            if cb and callable(cb):
                ret = cb(tuple_data, user_data)
            else:
                return tuple_data

    def _reset_rx_queue(self):
        while not self._rx_queue.empty():
            try:
                self._rx_queue.get_nowait()
            except queue.Empty:
                continue

            self._rx_queue.task_done()

    def accept(self, timeout=10.0):
        logging.debug("%s", self.accept.__name__)

        super().accept(timeout)

        self._running.set()
        self._rx_worker.start()

    def close(self):
        self._running.clear()

        if self._rx_worker.is_alive():
            self._rx_worker.join()

        self._reset_rx_queue()

        super().close()

    def register_event_handler(self, event_handler):
        self.event_handler_cb = event_handler


class RTT2PTY:
    def __init__(self):
        self.serial = None
        self.rtt2pty_process = None
        self.pty_name = None
        self.serial_thread = None
        self.stop_thread = threading.Event()
        self.log_filename = None
        self.log_file = None

    def _start_rtt2pty_proc(self, debugger_snr=None):
        cmd = ['rtt2pty']
        if debugger_snr:
            cmd.append('-s ' + debugger_snr)

        self.rtt2pty_process = subprocess.Popen(cmd,
                                                shell=False,
                                                stdout=subprocess.PIPE,
                                                stderr=subprocess.PIPE)
        flags = fcntl(self.rtt2pty_process.stdout, F_GETFL)  # get current p.stdout flags
        fcntl(self.rtt2pty_process.stdout, F_SETFL, flags | O_NONBLOCK)

        time.sleep(3)
        pty = None
        try:
            for line in iter(self.rtt2pty_process.stdout.readline, b''):
                line = line.decode('UTF-8')
                if line.startswith('PTY name is '):
                    pty = line[len('PTY name is '):].strip()
        except IOError:
            pass

        return pty

    @staticmethod
    def _read_from_port(device, stop_thread, file):
        while not stop_thread.is_set():
            line = device.readline()
            try:
                decoded = line.decode()
            except UnicodeDecodeError:
                continue
            file.write(decoded)
            file.flush()

    def start(self, log_filename, debugger_snr=None):
        self.log_filename = log_filename
        self.pty_name = self._start_rtt2pty_proc(debugger_snr)

        self.serial = serial.Serial(self.pty_name, 115200, timeout=0)
        self.stop_thread.clear()
        self.log_file = open(self.log_filename, 'a')
        self.serial_thread = threading.Thread(
            target=self._read_from_port, args=(self.serial, self.stop_thread, self.log_file))
        self.serial_thread.start()

    def stop(self):
        self.stop_thread.set()

        if self.serial_thread:
            self.serial_thread.join()
            self.serial_thread = None

        if self.log_file:
            self.log_file.close()
            self.log_file = None

        if self.rtt2pty_process and self.rtt2pty_process.poll() is None:
            self.rtt2pty_process.send_signal(signal.SIGINT)
            self.rtt2pty_process.wait()
            self.rtt2pty_process = None


class BTMON:
    def __init__(self):
        self.btmon_process = None
        self.pty_name = None
        self.log_file = None

    def start(self, log_file, debugger_snr):
        self.log_file = log_file
        cmd = ['btmon', '-J', 'NRF52,' + debugger_snr, '-w', self.log_file]

        self.btmon_process = subprocess.Popen(cmd,
                                              shell=False,
                                              stdout=subprocess.PIPE,
                                              stderr=subprocess.PIPE)

    def stop(self):
        if self.btmon_process and self.btmon_process.poll() is None:
            self.btmon_process.send_signal(signal.SIGINT)
            self.btmon_process.wait()
            self.btmon_process = None
