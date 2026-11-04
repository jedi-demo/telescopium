"""
SPDX-FileCopyrightText: 2026 PixLab, IFIC(CSIC-UV)
SPDX-License-Identifier: EUPL-1.2

Abstract TCP/IP socket interface for AIM-TTi QL355TP power supplies.
"""

from abc import ABCMeta, abstractmethod
import socket
import time
from threading import Lock

class TTiInterface(metaclass=ABCMeta): 
    def __init__(self, ip_address: str, timeout: float = 0.5):
        self._ip = ip_address
        self._port = 9221
        self._timeout = timeout
        self._socket = None
        self._lock = Lock()

    # Network helper functions
    def _connect_unlocked(self):
        if self._socket is None:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(self._timeout)
            try:
                s.connect((self._ip, self._port))
            except Exception:
                s.close()
                raise
            self._socket = s
            
    def _close_unlocked(self):
        if self._socket:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None
                
    def connect(self):
        """
        Establish TCP/IP socket connection to the device
        """
        with self._lock:
            self._connect_unlocked()

    def close(self):
        """
        Close TCP/IP socket connection    
        """
        with self._lock:
            self._close_unlocked()

    def _write(self, command: str):
        """
        Write command to socket
        """
        with self._lock:
            self._connect_unlocked()
            try:
                msg = f"{command}\n".encode('ascii')
                self._socket.sendall(msg)
            except OSError:
                self._close_unlocked()
                raise
            time.sleep(0.05)

    def _query(self, command: str) -> str:
        """
        Query the device and return the response
        """
        with self._lock:
            self._connect_unlocked()
            try:
                # Send
                self._socket.sendall(f"{command}\n".encode('ascii'))
                
                # Wait for device processing
                time.sleep(0.3)

                # Read
                response = self._socket.recv(1024)
            except OSError:
                self._close_unlocked()
                raise
            if not response:
                self._close_unlocked()
                raise ConnectionError("Connection closed by device")   
            return response.decode('ascii').strip()
                
    
    # Device functions

    @abstractmethod
    def identify(self) -> str: 
        # Identify the device   
        pass
    @abstractmethod
    def initialize(self): 
        # Initialize the device
        pass
    @abstractmethod
    def set_output(self, channel: int, enable: bool): 
        # Set the output state of a channel
        pass
    @abstractmethod
    def set_voltage(self, channel: int, voltage: float): 
        # Set the voltage of a channel
        pass
    @abstractmethod
    def set_current_limit(self, channel: int, current: float): 
        # Set the current limit of a channel
        pass
    @abstractmethod
    def set_ovp(self, channel: int, voltage: float): 
        # Set the over-voltage protection of a channel
        pass
    @abstractmethod
    def set_ocp(self, channel: int, current: float): 
        # Set the over-current protection of a channel
        pass
    @abstractmethod
    def get_voltage_readback(self, channel: int) -> float: 
        # Get the voltage readback of a channel
        pass
    @abstractmethod
    def get_current_readback(self, channel: int) -> float: 
        # Get the current readback of a channel
        pass
    @abstractmethod
    def get_limit_status(self, channel: int) -> int: 
        # Get the limit status of a channel
        pass
    @abstractmethod
    def get_execution_error(self) -> int: 
        # Get the execution error
        pass